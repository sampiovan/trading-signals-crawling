"""
Persistenza dello stato del crawler: l'ID dell'ultimo messaggio Telegram
processato. Permette, al riavvio, di recuperare i messaggi arrivati
mentre il crawler era offline (catch-up) invece di perderli.

Il file vive nella directory di lavoro del crawler (accanto a config.ini),
NON nella cartella Files di MT4: è stato interno del crawler.
"""
import json
import logging
import os

logger = logging.getLogger(__name__)

STATE_FILENAME = 'crawler_state.json'


def load_last_message_id(path=STATE_FILENAME):
	"""
	Restituisce l'ID dell'ultimo messaggio processato, o None se lo stato
	non esiste o non è leggibile (primo avvio / file corrotto).
	"""
	if not os.path.exists(path):
		return None
	try:
		with open(path, 'r', encoding='utf-8') as f:
			state = json.load(f)
		last_id = state.get('last_message_id')
		return int(last_id) if last_id is not None else None
	except (ValueError, OSError, json.JSONDecodeError):
		logger.exception(f"Stato del crawler illeggibile ({path}): riparto senza catch-up.")
		return None


def save_last_message_id(message_id, path=STATE_FILENAME):
	"""Salva l'ID dell'ultimo messaggio processato."""
	try:
		with open(path, 'w', encoding='utf-8') as f:
			json.dump({'last_message_id': int(message_id)}, f)
	except OSError:
		logger.exception(f"Impossibile salvare lo stato del crawler su {path}.")
