import os
import logging
from logging.handlers import TimedRotatingFileHandler
import asyncio
import csv
import configparser
from datetime import datetime
from telethon import TelegramClient, events

from msg_parser import parse_message


# ----- Logger -----------------------------
# Crea cartella logs se non esiste
logs_dir = "logs"
os.makedirs(logs_dir, exist_ok=True)

# Percorso del file di log (base)
log_file_path = os.path.join(logs_dir, "crawler.log")

# Creo il logger
logger = logging.getLogger("data_crawler")
logger.setLevel(logging.INFO)  # livello di default (puoi metterlo su DEBUG, WARNING, ecc.)

# TimedRotatingFileHandler
# - when='midnight': ruota il file di log a mezzanotte
# - interval=1: ruota ogni 1 'when'
# - backupCount=30: mantiene 30 file di log, poi elimina i più vecchi
handler = TimedRotatingFileHandler(
	log_file_path, 
	when='midnight', 
	interval=1, 
	backupCount=30,
	encoding='utf-8'
)

# Nome del file dopo la rotazione (suffix con data)
handler.suffix = "%Y-%m-%d"

# Formattazione del log
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
handler.setFormatter(formatter)

# Aggiunge il handler al logger
logger.addHandler(handler)

# Se vuoi disabilitare i messaggi su console, rimuovi la riga sotto
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)
# ----- end of Logger ----------------------


# ----- Lettura del file di configurazione -----
config = configparser.ConfigParser()
config.read('config.ini')

# Sostituisci questi valori con i tuoi dati (dopo averli ottenuti)
api_id = int(config['telegram']['YOUR_API_ID'])
api_hash = config['telegram']['YOUR_API_HASH']

# Nome della sessione: verrà creato un file "session_name.session" per salvare la sessione
session_name = config['telegram']['SESSION_NAME']

# Nome del canale (username o ID) da cui vuoi estrarre i messaggi.
# Per un canale privato, puoi usare l'invito oppure l'ID (se lo conosci)
channel_entity = config['telegram']['CHANNEL_ENTITY']


# ----- Impostazione del file CSV per salvare i segnali -----
# Percorso del file CSV di output
mt4_files_folder = config['paths']['MT4_FILES_FOLDER']
csv_filename = 'trading_signals.csv'

# Costruiamo il path completo del file CSV
csv_fullpath = os.path.join(mt4_files_folder, csv_filename)

# Se il file non esiste, creiamo l'header
def initialize_csv():
	try:
		with open(csv_fullpath, 'x', newline='', encoding='utf-8') as csvfile:
			writer = csv.writer(csvfile)
			# Header per messaggi di tipo "signal", "open", "modify", "close", "cancel"
			writer.writerow(['timestamp', 'order_id', 'magic_number', 'message_type', 'asset', 'signal_type', 'entry', 'sl', 'tp', 'comment'])
			logger.info(f"File {csv_filename} creato con header.")
	except FileExistsError:
		pass # Il file esiste già, non fare nulla


# ----- Funzione per salvare i dati estratti nel file CSV -----
def save_signal(data):
	timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
	with open(csv_fullpath, 'a', newline='', encoding='utf-8') as csvfile:
		writer = csv.writer(csvfile)
		writer.writerow([
			timestamp,
			data['order_id'],
			data['magic_number'],
			data['message_type'],
			data['asset'],
			data['signal_type'],
			data['entry'],
			data['sl'],
			data['tp'],
			data['comment']
		])
	logger.info(f"Segnale salvato: {data} a {timestamp}")


# ----- Funzione principale asincrona -----
async def main():
	logger.info("Avvio del crawler")

	# Inizializza il file CSV
	initialize_csv()

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

		#TODO: aggiungere try/except per gestire eventuali errori
		parsed = parse_message(message_text)
		if parsed:
			save_signal(parsed)
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
