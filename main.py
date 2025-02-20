import asyncio
import re
import csv
import configparser
from datetime import datetime
from telethon import TelegramClient, events


# ----- Lettura del file di configurazione -----
config = configparser.ConfigParser()
config.read('config.ini')

# Sostituisci questi valori con i tuoi dati (dopo averli ottenuti)
api_id = int(config['telegram']['YOUR_API_ID'])
api_hash = config['telegram']['YOUR_API_HASH']

# Nome della sessione: verrà creato un file "session_name.session" per salvare la sessione
session_name = config['telegram']['session_name']

# Nome del canale (username o ID) da cui vuoi estrarre i messaggi.
# Per un canale privato, puoi usare l'invito oppure l'ID (se lo conosci)
channel_entity = config['telegram']['channel_entity']


# ----- Impostazione del file CSV per salvare i segnali -----
# Percorso del file CSV di output
csv_filename = 'trading_signals.csv'

# Se il file non esiste, creiamo l'header
def initialize_csv():
	try:
		with open(csv_filename, 'x', newline='', encoding='utf-8') as csvfile:
			writer = csv.writer(csvfile)
			writer.writerow(['timestamp', 'asset', 'signal_type', 'entry', 'sl', 'tp'])
			print(f"File {csv_filename} creato con header.")
	except FileExistsError:
		pass # Il file esiste già, non fare nulla


# ----- Funzione per analizzare il messaggio e estrarre i dati con espressioni regolari -----
def parse_signal(message_text):
	# Definiamo un pattern base: personalizza questo pattern in base al formato dei tuoi messaggi
	"""
    Esempio di messaggio:
    "📈BUY LIMIT  CAD/CHF
    Prezzo 0.63600  (di apertura)
    
    Stop Loss   🔴 0.58000
    
    Take Profit  🟢  0.68000 
    
    ..."
    """
	pattern = re.compile(
		# r"SIGNAL:\s*(BUY|SELL(?:\s+LIMIT|(?:\s+STOP))?)\s+(\w+)\s+Entry:\s*([\d\.]+)\s+SL:\s*([\d\.]+)\s+TP:\s*([\d\.]+)",
		# re.IGNORECASE
		r"(?i)^(?:\S+\s*)?(BUY LIMIT|SELL LIMIT|BUY STOP|SELL STOP|BUY|SELL)\s+([A-Z]{3}/[A-Z]{3}).*?Prezzo\s+([\d\.]+).*?Stop Loss\s*[^\d]*([\d\.]+).*?Take Profit\s*[^\d]*([\d\.]+)",
        re.DOTALL
	)
	match = pattern.search(message_text)
	if match:
		signal_type = match.group(1).upper()
		asset = match.group(2).upper()
		entry = match.group(3)
		sl = match.group(4)
		tp = match.group(5)
		return {
			'asset': asset,
			'signal_type': signal_type,
			'entry': entry,
			'sl': sl,
			'tp': tp
		}
	else:
		return None


# ----- Funzione per salvare i dati estratti nel file CSV -----
def save_signal(data):
	timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
	with open(csv_filename, 'a', newline='', encoding='utf-8') as csvfile:
		writer = csv.writer(csvfile)
		writer.writerow([timestamp, data['asset'], data['signal_type'], data['entry'], data['sl'], data['tp']])
	print(f"Salvato segnale: {data} a {timestamp}")


# ----- Funzione principale asincrona -----
async def main():
	# Inizializza il file CSV
	initialize_csv()

	# Crea il client Telegram utilizzando le credenziali dal config.ini
	client = TelegramClient(session_name, api_id, api_hash)
	await client.start()
	print("Client Telegram avviato.")

	# Ottieni l'entità del canale (per canali privati, devi essere già membro)
	channel = await client.get_entity(channel_entity)
	print(f"Monitoro il canale: {channel.title if hasattr(channel, 'title') else channel}")

	# Registra un event handler per i nuovi messaggi nel canale
	@client.on(events.NewMessage(chats=channel))
	async def handler(event):
		message_text = event.raw_text
		print(f"Nuovo messaggio ricevuto: \n{message_text}\n")
		signal = parse_signal(message_text)
		if signal:
			save_signal(signal)
		else:
			print("Messaggio non riconosciuto come segnale di trading.")

	print("In ascolto dei nuovi messaggi... (premi Ctrl+C per terminare)")
	# Rimane in attesa indefinitamente
	await client.run_until_disconnected()

# ----- Avvia il loop principale -----
if __name__ == '__main__':
	asyncio.run(main())
