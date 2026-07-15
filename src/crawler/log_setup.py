import os
import logging
from logging.handlers import TimedRotatingFileHandler


def setup_logger(logs_dir="logs", level=logging.INFO):
	"""
	Configura il root logger con:
	- TimedRotatingFileHandler: ruota il file a mezzanotte, mantiene 30 giorni
	- StreamHandler: output anche su console

	Configurando il root logger, i log di tutti i moduli del package
	(logging.getLogger(__name__)) vengono raccolti dagli stessi handler.
	Restituisce il logger "crawler" da usare nell'entry point.
	"""
	os.makedirs(logs_dir, exist_ok=True)
	log_file_path = os.path.join(logs_dir, "crawler.log")

	formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

	file_handler = TimedRotatingFileHandler(
		log_file_path,
		when='midnight',
		interval=1,
		backupCount=30,
		encoding='utf-8'
	)
	file_handler.suffix = "%Y-%m-%d"  # nome del file dopo la rotazione
	file_handler.setFormatter(formatter)

	console_handler = logging.StreamHandler()
	console_handler.setFormatter(formatter)

	root = logging.getLogger()
	root.setLevel(level)
	root.addHandler(file_handler)
	root.addHandler(console_handler)

	return logging.getLogger("crawler")
