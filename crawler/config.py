import configparser


# Cache del ConfigParser: il file viene letto una sola volta
_config = None

# Chiavi obbligatorie per sezione: verificate all'avvio per dare un
# errore chiaro invece di un KeyError al primo accesso.
REQUIRED_KEYS = {
	'telegram': ['YOUR_API_ID', 'YOUR_API_HASH', 'SESSION_NAME', 'CHANNEL_ENTITY'],
}


def get_mt5_setting(config, key, default=''):
	"""Legge una chiave opzionale della sezione [mt5] (assente nei config v1)."""
	return config.get('mt5', key, fallback=default).strip()


def _validate_config(parser, path):
	"""Verifica sezioni e chiavi obbligatorie; solleva ValueError se ne mancano."""
	missing = []
	for section, keys in REQUIRED_KEYS.items():
		if not parser.has_section(section):
			missing.extend(f"[{section}] {key}" for key in keys)
			continue
		for key in keys:
			if not parser.get(section, key, fallback='').strip():
				missing.append(f"[{section}] {key}")
	if missing:
		raise ValueError(
			f"Configurazione '{path}' incompleta, chiavi mancanti o vuote: "
			+ ", ".join(missing)
			+ ". Vedi config.example.ini per il formato atteso."
		)


def load_config(path='config.ini'):
	"""
	Carica il file di configurazione (default: config.ini nella directory
	di lavoro corrente), ne valida le chiavi obbligatorie e lo mette in
	cache per le letture successive.
	"""
	global _config
	if _config is None:
		parser = configparser.ConfigParser()
		if not parser.read(path, encoding='utf-8'):
			raise FileNotFoundError(
				f"File di configurazione '{path}' non trovato. "
				"Copia config.example.ini in config.ini e compila i valori."
			)
		_validate_config(parser, path)
		_config = parser
	return _config


def reset_config():
	"""Svuota la cache (utile nei test)."""
	global _config
	_config = None
