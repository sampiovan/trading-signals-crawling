import os
import sys
import asyncio
import logging
import argparse
from telethon import TelegramClient, events

from crawler import executor, mt5_client, order_lookup, risk
from crawler.config import load_config, get_setting
from crawler.crawler_state import (
	load_last_message_id,
	save_last_message_id,
	load_initial_deposit,
	save_initial_deposit,
)
from crawler.log_setup import setup_logger
from crawler.msg_parser import parse_message, OrderNotFoundException
from crawler.news_calendar import CACHE_FILENAME as NEWS_CACHE_FILENAME
from crawler.position_guard import run_guard

logger = logging.getLogger("crawler")


async def _alert(client, text):
	"""Notifica nei Saved Messages di Telegram (best effort)."""
	try:
		await client.send_message('me', text)
	except Exception:
		logger.exception("Impossibile inviare la notifica Telegram.")


async def notify_failure(client, signal, outcome):
	"""Notifica un fallimento definitivo nei Saved Messages di Telegram."""
	await _alert(client, (
		"⚠️ Esecuzione segnale FALLITA\n"
		f"Tipo: {signal['message_type']} {signal['signal_type']} {signal['asset']}\n"
		f"Dettaglio: {outcome.message} (retcode={outcome.retcode})"
	))


def _already_executed(signal):
	"""
	True se un ordine/posizione live corrisponde già al segnale (stesso
	asset, tipo e prezzo/commento "@prezzo"): il crawler è crashato dopo
	l'esecuzione ma prima di salvare lo stato, e il replay del catch-up
	lo sta riproponendo. Residuo accettato: due segnali IDENTICI entrambi
	ancora vivi nello stesso downtime verrebbero de-duplicati — il canale
	non apre due posizioni allo stesso prezzo, e il replay in ordine
	cronologico gestisce comunque placement→close→placement identico.
	"""
	ticket, _ = order_lookup.get_order_ticket(
		signal['asset'], signal['entry'], signal['signal_type'])
	return ticket


async def process_message(client, message, state_path, catching_up=False):
	"""
	Pipeline condivisa tra eventi live e catch-up: recupera l'eventuale
	messaggio citato, riconosce i segnali, li ESEGUE su MT5 e aggiorna
	lo stato (ID dell'ultimo messaggio processato).

	Lo stato avanza anche per messaggi non riconosciuti o falliti: il
	catch-up deve essere deterministico, non ritentare all'infinito.

	Con catching_up=True i segnali che APRONO esposizione (placement e
	open diretto a mercato) vengono saltati se risultano già eseguiti:
	l'esecuzione avviene PRIMA del salvataggio dello stato, quindi un
	crash nel mezzo farebbe rieseguire il segnale duplicando la posizione.
	"""
	message_text = message.raw_text or ''
	logger.info(f"Nuovo messaggio ricevuto (id={message.id}): \n{message_text}\n\n")

	# Se il messaggio è una risposta, recupera il testo del messaggio
	# citato: serve ad alcuni parser (es. move SL) per risalire all'ordine
	reply_text = None
	try:
		reply = await message.get_reply_message()
		if reply:
			reply_text = reply.raw_text
	except Exception:
		logger.exception("Impossibile recuperare il messaggio citato, proseguo senza.")

	try:
		signals = parse_message(message_text, reply_text=reply_text)
	except OrderNotFoundException as e:
		# Segnale riconosciuto ma ordine non trovato tra posizioni/pending
		# live: scarto definitivo, quindi va notificato come i fallimenti
		# di esecuzione (es. una chiusura persa non deve passare inosservata)
		logger.error(f"Ordine non trovato sul conto, segnale scartato: {e}")
		await _alert(client, f"⚠️ Segnale SCARTATO: ordine non trovato sul conto\nDettaglio: {e}")
		signals = None
	except Exception:
		logger.exception("Errore inatteso nel parsing del messaggio, segnale scartato.")
		signals = None

	if signals is None:
		logger.info("Messaggio non riconosciuto come segnale di trading (o scartato).")
	else:
		for signal in signals:
			if (catching_up and signal['message_type'] in ('placement', 'open')
					and not signal['order_id'] and _already_executed(signal)):
				logger.warning(
					f"Catch-up: {signal['message_type']} {signal['signal_type']} "
					f"{signal['asset']} @{signal['entry']} risulta già eseguito "
					f"(crash prima del salvataggio dello stato?): salto il doppione."
				)
				continue
			outcome = executor.execute(signal)
			if not outcome.ok:
				await notify_failure(client, signal, outcome)

	save_last_message_id(message.id, path=state_path)


def resolve_initial_deposit(config, state_path, account):
	"""
	Deposito iniziale per il sizing MODE=BALANCE, in ordine di precedenza:
	config [risk] INITIAL_DEPOSIT > stato persistito > balance attuale
	(rilevato al primo avvio e salvato, così non cambia ai riavvii).
	"""
	configured = get_setting(config, 'risk', 'INITIAL_DEPOSIT')
	if configured:
		logger.info(f"Deposito iniziale da config: {configured}")
		return float(configured)

	stored = load_initial_deposit(path=state_path)
	if stored is not None:
		logger.info(f"Deposito iniziale dallo stato persistito: {stored}")
		return stored

	save_initial_deposit(account.balance, path=state_path)
	logger.info(f"Primo avvio: deposito iniziale rilevato dal balance ({account.balance}) e salvato.")
	return float(account.balance)


async def catch_up(client, channel, state_path):
	"""
	Recupera e processa i messaggi arrivati mentre il crawler era offline.
	Al primo avvio (nessuno stato) non riprocessa lo storico del canale:
	salva solo l'ID dell'ultimo messaggio come punto di partenza.
	"""
	last_id = load_last_message_id(path=state_path)

	if last_id is None:
		# Primo avvio: inizializza lo stato all'ultimo messaggio del canale
		# così i futuri riavvii sanno da dove riprendere.
		async for message in client.iter_messages(channel, limit=1):
			save_last_message_id(message.id, path=state_path)
			logger.info(f"Primo avvio: stato inizializzato all'ultimo messaggio del canale (id={message.id}).")
		return

	missed = 0
	# reverse=True: dal più vecchio al più recente, per rispettare l'ordine dei segnali
	async for message in client.iter_messages(channel, min_id=last_id, reverse=True):
		missed += 1
		await process_message(client, message, state_path, catching_up=True)

	if missed:
		logger.info(f"Catch-up completato: {missed} messaggi recuperati dal downtime.")
	else:
		logger.info("Nessun messaggio perso durante il downtime.")


# ----- Funzione principale asincrona -----
async def main(config_path):
	logger.info(f"Avvio del crawler (config: {config_path})")

	# Tutti i percorsi di runtime (stato, sessione Telegram, log) vivono
	# accanto al file di config: nessuna dipendenza dalla working directory.
	config = load_config(config_path)
	base_dir = os.path.dirname(config_path)
	state_path = os.path.join(base_dir, 'crawler_state.json')

	api_id = int(config['telegram']['YOUR_API_ID'])
	api_hash = config['telegram']['YOUR_API_HASH']
	session_path = os.path.join(base_dir, config['telegram']['SESSION_NAME'])
	channel_entity = config['telegram']['CHANNEL_ENTITY']

	# Connessione a MT5 PRIMA di tutto: se il terminale non c'è o il conto
	# non è hedging è inutile mettersi in ascolto dei segnali.
	account = mt5_client.connect()
	risk.set_initial_deposit(resolve_initial_deposit(config, state_path, account))

	try:
		# Crea il client Telegram utilizzando le credenziali dal config
		client = TelegramClient(session_path, api_id, api_hash)
		await client.start()
		logger.info("Client Telegram avviato.")

		# Ottieni l'entità del canale (per canali privati, devi essere già membro)
		channel = await client.get_entity(channel_entity)
		logger.info(f"Monitoro il canale: {channel.title if hasattr(channel, 'title') else channel}")

		# Recupera i messaggi persi PRIMA di registrare il handler live, per
		# preservare l'ordine dei segnali (resta una finestra di pochi istanti
		# tra catch-up e registrazione, trascurabile rispetto a ore di downtime).
		await catch_up(client, channel, state_path)

		# Registra un event handler per i nuovi messaggi nel canale
		@client.on(events.NewMessage(chats=channel))
		async def handler(event):
			await process_message(client, event.message, state_path)

		# Guardia delle posizioni in perdita (cut & reopen), in parallelo;
		# la cache del calendario notizie vive accanto al config
		news_cache_path = os.path.join(base_dir, NEWS_CACHE_FILENAME)
		guard_task = asyncio.create_task(run_guard(client, news_cache_path=news_cache_path))

		logger.info("In ascolto dei nuovi messaggi... (premi Ctrl+C per terminare)")
		try:
			# Rimane in attesa indefinitamente
			await client.run_until_disconnected()
		finally:
			guard_task.cancel()
	finally:
		mt5_client.shutdown()


# ----- Entry point sincrono (console script e python -m crawler) -----
def run():
	parser = argparse.ArgumentParser(
		prog='signals-crawler',
		description="Esegue su MetaTrader 5 i segnali di trading di un canale Telegram."
	)
	parser.add_argument(
		'--config', default='config.ini',
		help="Percorso di config.ini (default: nella directory corrente). "
		     "Stato, sessione Telegram e log vengono creati accanto al config."
	)
	args = parser.parse_args()

	config_path = os.path.abspath(args.config)
	setup_logger(logs_dir=os.path.join(os.path.dirname(config_path), 'logs'))

	try:
		asyncio.run(main(config_path))
	except KeyboardInterrupt:
		logger.info("Arresto da tastiera. Fine.")
	except Exception:
		logger.exception("Errore inaspettato durante l'esecuzione del crawler")
		# Exit code != 0: permette alla Scheduled Task di riavviare il crawler
		sys.exit(1)


if __name__ == '__main__':
	run()
