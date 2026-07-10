import csv
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# Header per messaggi di tipo "placement", "open", "modify", "close", "cancel"
CSV_HEADER = ['timestamp', 'order_id', 'magic_number', 'message_type', 'asset',
              'signal_type', 'entry', 'sl', 'tp', 'comment']


def initialize_csv(csv_fullpath):
	"""Crea il file CSV dei segnali con il solo header, se non esiste già."""
	try:
		with open(csv_fullpath, 'x', newline='', encoding='utf-8') as csvfile:
			writer = csv.writer(csvfile)
			writer.writerow(CSV_HEADER)
			logger.info(f"File {csv_fullpath} creato con header.")
	except FileExistsError:
		pass  # Il file esiste già, non fare nulla


def save_signal(csv_fullpath, data):
	"""Accoda un segnale (dict con le chiavi di CSV_HEADER) al file CSV."""
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
