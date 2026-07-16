"""
Persistenza dello stato del crawler (file JSON accanto al config):
- last_message_id: ultimo messaggio Telegram processato (per il catch-up);
- initial_deposit: deposito iniziale rilevato al primo avvio (per il sizing
  MODE=BALANCE), quando non è specificato in config.
"""
import json
import logging
import os

logger = logging.getLogger(__name__)

STATE_FILENAME = 'crawler_state.json'


def _load_state(path):
	"""Carica lo stato come dict; {} se il file manca o è illeggibile."""
	if not os.path.exists(path):
		return {}
	try:
		with open(path, 'r', encoding='utf-8') as f:
			state = json.load(f)
		return state if isinstance(state, dict) else {}
	except (ValueError, OSError, json.JSONDecodeError):
		logger.exception(f"Stato del crawler illeggibile ({path}): riparto da zero.")
		return {}


def _save_state(state, path):
	"""
	Scrittura ATOMICA (file temporaneo + os.replace): un crash a metà
	scrittura non deve mai corrompere lo stato esistente — file corrotto
	= catch-up che riparte "da primo avvio" saltando i segnali persi.
	"""
	tmp_path = f"{path}.tmp"
	try:
		with open(tmp_path, 'w', encoding='utf-8') as f:
			json.dump(state, f)
		os.replace(tmp_path, path)
	except OSError:
		logger.exception(f"Impossibile salvare lo stato del crawler su {path}.")
		try:
			os.remove(tmp_path)
		except OSError:
			pass


def load_last_message_id(path=STATE_FILENAME):
	"""
	Restituisce l'ID dell'ultimo messaggio processato, o None se lo stato
	non esiste o non è leggibile (primo avvio / file corrotto).
	"""
	last_id = _load_state(path).get('last_message_id')
	return int(last_id) if last_id is not None else None


def save_last_message_id(message_id, path=STATE_FILENAME):
	"""Salva l'ID dell'ultimo messaggio processato (preserva le altre chiavi)."""
	state = _load_state(path)
	state['last_message_id'] = int(message_id)
	_save_state(state, path)


def load_initial_deposit(path=STATE_FILENAME):
	"""Deposito iniziale rilevato in un avvio precedente, o None."""
	value = _load_state(path).get('initial_deposit')
	return float(value) if value is not None else None


def save_initial_deposit(value, path=STATE_FILENAME):
	"""Persiste il deposito iniziale rilevato (preserva le altre chiavi)."""
	state = _load_state(path)
	state['initial_deposit'] = float(value)
	_save_state(state, path)
