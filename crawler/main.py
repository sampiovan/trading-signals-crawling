import os
import asyncio
from telethon import TelegramClient, events

from config import load_config
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

	# Registra un event handler per i nuovi messaggi nel canale
	@client.on(events.NewMessage(chats=channel))
	async def handler(event):
		message_text = event.raw_text
		logger.info(f"Nuovo messaggio ricevuto: \n{message_text}\n\n")

		try:
			parsed = parse_message(message_text)
		except OrderNotFoundException as e:
			# Segnale riconosciuto ma ordine non presente nel registro:
			# logga e resta in ascolto (il messaggio originale è nel log sopra)
			logger.error(f"Ordine non trovato nel registro, segnale scartato: {e}")
			return
		except Exception:
			logger.exception("Errore inatteso nel parsing del messaggio, segnale scartato.")
			return

		if parsed:
			save_signal(csv_fullpath, parsed)
		else:
			logger.info("Messaggio non riconosciuto come segnale di trading.")

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
