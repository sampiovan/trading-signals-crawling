"""
Connessione al terminale MetaTrader 5 tramite il package ufficiale
`MetaTrader5` (solo Windows). Il terminale deve essere in esecuzione
e loggato su un conto di tipo HEDGING.

L'import è difensivo: sui sistemi senza il package (es. CI Linux)
il modulo si importa comunque e i test iniettano uno stub su
`mt5_client.mt5`; l'errore emerge solo chiamando connect().
"""
import logging

from config import load_config, get_mt5_setting

try:
	import MetaTrader5 as mt5
except ImportError:	# pragma: no cover - dipende dalla piattaforma
	mt5 = None

logger = logging.getLogger(__name__)


class Mt5ConnectionError(Exception):
	pass


def connect():
	"""
	Inizializza la connessione al terminale MT5 e verifica il conto.
	Solleva Mt5ConnectionError se il package manca, il terminale non
	risponde, nessun conto è loggato o il conto non è hedging.
	"""
	if mt5 is None:
		raise Mt5ConnectionError(
			"Package MetaTrader5 non disponibile (richiede Windows: "
			"pip install MetaTrader5)."
		)

	config = load_config()
	terminal_path = get_mt5_setting(config, 'TERMINAL_PATH')

	initialized = mt5.initialize(terminal_path) if terminal_path else mt5.initialize()
	if not initialized:
		raise Mt5ConnectionError(f"mt5.initialize fallita: {mt5.last_error()}")

	account = mt5.account_info()
	if account is None:
		mt5.shutdown()
		raise Mt5ConnectionError(f"Nessun conto loggato nel terminale: {mt5.last_error()}")

	# I conti netting ammettono UNA sola posizione per simbolo: incompatibile
	# col canale, che tiene anche 4 posizioni sullo stesso cambio.
	if account.margin_mode != mt5.ACCOUNT_MARGIN_MODE_RETAIL_HEDGING:
		mt5.shutdown()
		raise Mt5ConnectionError(
			"Il conto MT5 loggato non è di tipo HEDGING (margin_mode="
			f"{account.margin_mode}). Serve un conto hedging per gestire "
			"più posizioni sullo stesso simbolo."
		)

	logger.info(
		f"MT5 connesso: login={account.login}, server={account.server}, "
		f"equity={account.equity}, conto hedging OK"
	)
	return account


def shutdown():
	"""Chiude la connessione col terminale (idempotente)."""
	if mt5 is not None:
		mt5.shutdown()
		logger.info("Connessione MT5 chiusa.")


def resolve_symbol(asset):
	"""
	Converte l'asset del segnale (es. EURUSD) nel simbolo del broker
	(es. EURUSD.m con SYMBOL_SUFFIX=.m) e lo rende visibile nel Market
	Watch se necessario. Solleva ValueError se il simbolo non esiste.
	"""
	config = load_config()
	symbol = asset + get_mt5_setting(config, 'SYMBOL_SUFFIX')

	info = mt5.symbol_info(symbol)
	if info is None:
		raise ValueError(f"Simbolo '{symbol}' non esistente presso il broker.")
	if not info.visible and not mt5.symbol_select(symbol, True):
		raise ValueError(f"Impossibile selezionare il simbolo '{symbol}' nel Market Watch.")
	return symbol
