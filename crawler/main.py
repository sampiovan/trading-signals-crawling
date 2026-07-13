import os
import asyncio
from telethon import TelegramClient, events

from config import load_config
from crawler_state import load_last_message_id, save_last_message_id
from log_setup import setup_logger
from msg_parser import parse_message, OrderNotFoundException
from signals_csv import initialize_csv, save_signal

logger = setup_logger()


# ----- Lettura del file di configurazione -----
config = load_config()

api_id = int(config['telegram']['YOUR_API_ID'])
api_hash = config['telegram']['YOUR_API_HASH']

# Nome della sessione: verrà creato un file "session_name.session" per salvare la sessione
session_name = config['telegram']['SESSION_NAME']

# Nome del canale (username o ID) da cui vuoi estrarre i messaggi
channel_entity = config['telegram']['CHANNEL_ENTITY']

# Percorso del file CSV di output (nella cartella Files di MT4)
mt4_files_folder = config['paths']['MT4_FILES_FOLDER']
csv_fullpath = os.path.join(mt4_files_folder, 'trading_signals.csv')


async def process_message(message):
	"""
	Pipeline condivisa tra eventi live e catch-up: recupera l'eventuale
	messaggio citato, riconosce il segnale, salva su CSV e aggiorna lo
	stato (ID dell'ultimo messaggio processato).

	Lo stato avanza anche per messaggi non riconosciuti o falliti: il
	catch-up deve essere deterministico, non ritentare all'infinito.
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
		# Segnale riconosciuto ma ordine non presente nel registro:
		# logga e prosegui (il messaggio originale è nel log sopra)
		logger.error(f"Ordine non trovato nel registro, segnale scartato: {e}")
		signals = None
	except Exception:
		logger.exception("Errore inatteso nel parsing del messaggio, segnale scartato.")
		signals = None

	if signals is None:
		logger.info("Messaggio non riconosciuto come segnale di trading (o scartato).")
	else:
		for signal in signals:
			save_signal(csv_fullpath, signal)

	save_last_message_id(message.id)


async def catch_up(client, channel):
	"""
	Recupera e processa i messaggi arrivati mentre il crawler era offline.
	Al primo avvio (nessuno stato) non riprocessa lo storico del canale:
	salva solo l'ID dell'ultimo messaggio come punto di partenza.
	"""
	last_id = load_last_message_id()

	if last_id is None:
		# Primo avvio: inizializza lo stato all'ultimo messaggio del canale
		# così i futuri riavvii sanno da dove riprendere.
		async for message in client.iter_messages(channel, limit=1):
			save_last_message_id(message.id)
			logger.info(f"Primo avvio: stato inizializzato all'ultimo messaggio del canale (id={message.id}).")
		return

	missed = 0
	# reverse=True: dal più vecchio al più recente, per rispettare l'ordine dei segnali
	async for message in client.iter_messages(channel, min_id=last_id, reverse=True):
		missed += 1
		await process_message(message)

	if missed:
		logger.info(f"Catch-up completato: {missed} messaggi recuperati dal downtime.")
	else:
		logger.info("Nessun messaggio perso durante il downtime.")


# ----- Funzione principale asincrona -----
async def main():
	logger.info("Avvio del crawler")

	# Inizializza il file CSV
	initialize_csv(csv_fullpath)

	# Crea il client Telegram utilizzando le credenziali dal config.ini
	client = TelegramClient(session_name, api_id, api_hash)
	await client.start()
	logger.info("Client Telegram avviato.")

	# Ottieni l'entità del canale (per canali privati, devi essere già membro)
	channel = await client.get_entity(channel_entity)
	logger.info(f"Monitoro il canale: {channel.title if hasattr(channel, 'title') else channel}")

	# Recupera i messaggi persi PRIMA di registrare il handler live, per
	# preservare l'ordine dei segnali (resta una finestra di pochi istanti
	# tra catch-up e registrazione, trascurabile rispetto a ore di downtime).
	await catch_up(client, channel)

	# Registra un event handler per i nuovi messaggi nel canale
	@client.on(events.NewMessage(chats=channel))
	async def handler(event):
		await process_message(event.message)

	logger.info("In ascolto dei nuovi messaggi... (premi Ctrl+C per terminare)")
	# Rimane in attesa indefinitamente
	await client.run_until_disconnected()

# ----- Avvia il loop principale -----
if __name__ == '__main__':
	try:
		asyncio.run(main())
	except KeyboardInterrupt:
		logger.info("Arresto da tastiera. Fine.")
	except Exception:
		logger.exception("Errore inaspettato durante l'esecuzione del crawler")
