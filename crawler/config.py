import configparser


# Cache del ConfigParser: il file viene letto una sola volta
_config = None


def load_config(path='config.ini'):
	"""
	Carica il file di configurazione (default: config.ini nella directory
	di lavoro corrente) e lo mette in cache per le letture successive.
	"""
	global _config
	if _config is None:
		parser = configparser.ConfigParser()
		if not parser.read(path, encoding='utf-8'):
			raise FileNotFoundError(
				f"File di configurazione '{path}' non trovato. "
				"Copia config.example.ini in config.ini e compila i valori."
			)
		_config = parser
	return _config


def reset_config():
	"""Svuota la cache (utile nei test)."""
	global _config
	_config = None
