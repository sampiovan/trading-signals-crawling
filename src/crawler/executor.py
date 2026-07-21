"""
Esecuzione diretta dei segnali su MetaTrader 5 (sostituisce l'EA MQL4
e il ponte CSV della v1): ogni segnale prodotto da msg_parser viene
tradotto in una richiesta di trading e inviato con `order_send`,
con retry sui retcode transitori e outcome sincrono.

Come in mt5_client, l'import del package è difensivo: i test iniettano
uno stub su `executor.mt5`.
"""
import time
import logging
from collections import namedtuple

from crawler import mt5_client
from crawler.comments import format_price_comment
from crawler.config import load_config, get_mt5_setting
from crawler.risk import compute_lot, grow_volume_to_balance

try:
	import MetaTrader5 as mt5
except ImportError:	# pragma: no cover - dipende dalla piattaforma
	mt5 = None

logger = logging.getLogger(__name__)

# Esito di un'esecuzione: ok, ticket coinvolto, retcode MT5, descrizione
Outcome = namedtuple('Outcome', ['ok', 'ticket', 'retcode', 'message'])

# ----- Costanti del protocollo MT5 -------------------------------------
# Valori pubblici e stabili del package MetaTrader5, definiti localmente
# così il modulo è importabile (e testabile) anche senza il package.
TRADE_ACTION_DEAL = 1
TRADE_ACTION_PENDING = 5
TRADE_ACTION_SLTP = 6
TRADE_ACTION_MODIFY = 7
TRADE_ACTION_REMOVE = 8

ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1

ORDER_TIME_GTC = 0
ORDER_FILLING_FOK = 0
ORDER_FILLING_IOC = 1
ORDER_FILLING_RETURN = 2
SYMBOL_FILLING_FOK = 1	# flag nel bitmask symbol_info().filling_mode
SYMBOL_FILLING_IOC = 2

RETCODE_PLACED = 10008
RETCODE_DONE = 10009
RETCODE_DONE_PARTIAL = 10010
SUCCESS_RETCODES = {RETCODE_PLACED, RETCODE_DONE, RETCODE_DONE_PARTIAL}

# Retcode transitori: vale la pena ritentare con prezzo aggiornato
TRANSIENT_RETCODES = {
	10004,	# REQUOTE
	10020,	# PRICE_CHANGED
	10021,	# PRICE_OFF (nessuna quotazione)
	10024,	# TOO_MANY_REQUESTS
	10031,	# CONNECTION (nessuna connessione al trade server)
}

PENDING_ORDER_TYPES = {
	'BUY LIMIT': 2,		# ORDER_TYPE_BUY_LIMIT
	'SELL LIMIT': 3,	# ORDER_TYPE_SELL_LIMIT
	'BUY STOP': 4,		# ORDER_TYPE_BUY_STOP
	'SELL STOP': 5,		# ORDER_TYPE_SELL_STOP
}
MARKET_ORDER_TYPES = {
	'BUY': ORDER_TYPE_BUY,
	'SELL': ORDER_TYPE_SELL,
}

MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 1.0


# ----- Helper -----------------------------------------------------------

def _deviation_points():
	"""Slippage massimo in punti da config [mt5] DEVIATION_POINTS (default 30 = 3 pip a 5 cifre)."""
	return int(get_mt5_setting(load_config(), 'DEVIATION_POINTS', default='30') or 30)


def _filling_mode(symbol):
	"""Filling mode supportato dal simbolo (evita il retcode 10030 Unsupported filling)."""
	filling = mt5.symbol_info(symbol).filling_mode
	if filling & SYMBOL_FILLING_IOC:
		return ORDER_FILLING_IOC
	if filling & SYMBOL_FILLING_FOK:
		return ORDER_FILLING_FOK
	return ORDER_FILLING_RETURN


def _market_price(symbol, order_type):
	"""Prezzo corrente per un ordine a mercato: Ask per BUY, Bid per SELL."""
	tick = mt5.symbol_info_tick(symbol)
	return tick.ask if order_type == ORDER_TYPE_BUY else tick.bid


def _signal_comment(signal):
	"""
	Commento dell'ordine: il prezzo di apertura INVIATO DAL CANALE
	("@1.3390"), mai il prezzo di fill. È l'identificatore stabile del
	segnale usato anche dal lookup (vedi comments.py).
	"""
	try:
		return format_price_comment(signal['asset'], signal['entry'])
	except (TypeError, ValueError):
		return ''


def _send_with_retry(build_request):
	"""
	Invia una richiesta di trading con retry sui retcode transitori.
	build_request è una callable: viene rieseguita a ogni tentativo così
	i prezzi di mercato vengono aggiornati.
	"""
	last = Outcome(False, None, None, "nessun tentativo eseguito")
	for attempt in range(1, MAX_ATTEMPTS + 1):
		request = build_request()
		result = mt5.order_send(request)

		if result is None:
			# Errore di comunicazione col terminale: trattato come transitorio
			last = Outcome(False, None, None, f"order_send ha restituito None: {mt5.last_error()}")
			logger.warning(f"{last.message} (tentativo {attempt}/{MAX_ATTEMPTS})")
		elif result.retcode in SUCCESS_RETCODES:
			return Outcome(True, getattr(result, 'order', None), result.retcode, result.comment or 'ok')
		elif result.retcode in TRANSIENT_RETCODES:
			last = Outcome(False, None, result.retcode, result.comment)
			logger.warning(f"Retcode transitorio {result.retcode} ({result.comment}), tentativo {attempt}/{MAX_ATTEMPTS}")
		else:
			# Fallimento definitivo: inutile ritentare
			return Outcome(False, None, result.retcode, f"fallito: {result.comment}")

		if attempt < MAX_ATTEMPTS:
			time.sleep(RETRY_DELAY_SECONDS)
	return last


def _market_order_request(symbol, order_type, signal):
	"""Costruisce la richiesta per un ordine a mercato (prezzo corrente)."""
	return {
		'action': TRADE_ACTION_DEAL,
		'symbol': symbol,
		'volume': compute_lot(signal, mt5.symbol_info(symbol), mt5.account_info()),
		'type': order_type,
		'price': _market_price(symbol, order_type),
		'sl': float(signal['sl'] or 0),
		'tp': float(signal['tp'] or 0),
		'deviation': _deviation_points(),
		'magic': int(signal['magic_number'] or 0),
		'comment': _signal_comment(signal),
		'type_time': ORDER_TIME_GTC,
		'type_filling': _filling_mode(symbol),
	}


def open_market(symbol, order_type, volume, sl, tp, magic, comment):
	"""
	Apre una posizione a mercato con parametri ESPLICITI (volume, SL/TP,
	magic e commento già decisi dal chiamante). Usata dalla guardia
	posizioni per riaprire un trade tagliato mantenendo il commento
	del segnale con la perdita cumulata.
	"""
	def build():
		return {
			'action': TRADE_ACTION_DEAL,
			'symbol': symbol,
			'volume': volume,
			'type': order_type,
			'price': _market_price(symbol, order_type),
			'sl': sl,
			'tp': tp,
			'deviation': _deviation_points(),
			'magic': magic,
			'comment': comment,
			'type_time': ORDER_TIME_GTC,
			'type_filling': _filling_mode(symbol),
		}
	return _send_with_retry(build)


# ----- Handler per message_type -----------------------------------------

def _do_placement(signal):
	symbol = mt5_client.resolve_symbol(signal['asset'])
	signal_type = signal['signal_type'].upper()

	if signal_type in PENDING_ORDER_TYPES:
		order_type = PENDING_ORDER_TYPES[signal_type]

		def build():
			return {
				'action': TRADE_ACTION_PENDING,
				'symbol': symbol,
				'volume': compute_lot(signal, mt5.symbol_info(symbol), mt5.account_info()),
				'type': order_type,
				'price': float(signal['entry']),
				'sl': float(signal['sl'] or 0),
				'tp': float(signal['tp'] or 0),
				'magic': int(signal['magic_number'] or 0),
				'comment': _signal_comment(signal),
				'type_time': ORDER_TIME_GTC,
				'type_filling': _filling_mode(symbol),
			}
		return _send_with_retry(build)

	if signal_type in MARKET_ORDER_TYPES:
		order_type = MARKET_ORDER_TYPES[signal_type]
		return _send_with_retry(lambda: _market_order_request(symbol, order_type, signal))

	return Outcome(False, None, None, f"signal_type non riconosciuto: {signal_type}")


def _do_open(signal):
	# Caso 1: ordine noto (pending piazzato in precedenza) -> solo verifica.
	# Il broker triggera il pending da solo: aprire qui duplicherebbe.
	if signal['order_id']:
		ticket = int(signal['order_id'])
		if mt5.positions_get(ticket=ticket):
			return Outcome(True, ticket, None, "pending confermato aperto come posizione")
		if mt5.orders_get(ticket=ticket):
			logger.warning(f"Open: l'ordine {ticket} risulta ancora pendente nonostante il segnale di apertura.")
			return Outcome(True, ticket, None, "ancora pendente, nessuna azione")
		return Outcome(False, ticket, None, "ticket non trovato tra posizioni e pending (chiuso o cancellato?)")

	# Caso 2: ordine diretto a mercato (nessun placement precedente)
	signal_type = signal['signal_type'].upper()
	order_type = MARKET_ORDER_TYPES.get(signal_type)
	if order_type is None:
		return Outcome(False, None, None, f"signal_type non riconosciuto per open: {signal_type}")

	symbol = mt5_client.resolve_symbol(signal['asset'])
	return _send_with_retry(lambda: _market_order_request(symbol, order_type, signal))


def _do_modify(signal):
	ticket = int(signal['order_id'])
	entry = float(signal['entry'] or 0)
	sl = float(signal['sl'] or 0)
	tp = float(signal['tp'] or 0)

	orders = mt5.orders_get(ticket=ticket)
	if orders:
		# Pending: MT5 non permette di cambiare il commento con una MODIFY,
		# ma il commento deve riflettere il nuovo prezzo del canale.
		# Sequenza: REMOVE del pending + nuovo PENDING con prezzo, SL/TP
		# fusi (0 = invariato) e commento "@nuovo-prezzo".
		order = orders[0]
		new_price = entry if entry > 0 else order.price_open
		new_sl = sl if sl > 0 else order.sl
		new_tp = tp if tp > 0 else order.tp

		removed = _send_with_retry(lambda: {'action': TRADE_ACTION_REMOVE, 'order': ticket})
		if not removed.ok:
			return removed

		def build():
			return {
				'action': TRADE_ACTION_PENDING,
				'symbol': order.symbol,
				'volume': grow_volume_to_balance(signal, order.volume_current,
				                                 mt5.symbol_info(order.symbol), mt5.account_info()),
				'type': order.type,
				'price': new_price,
				'sl': new_sl,
				'tp': new_tp,
				'magic': order.magic,
				'comment': format_price_comment(signal['asset'], new_price),
				'type_time': ORDER_TIME_GTC,
				'type_filling': _filling_mode(order.symbol),
			}
		outcome = _send_with_retry(build)
		if not outcome.ok:
			# Il pending è stato rimosso ma non ripiazzato: serve intervento manuale
			return Outcome(False, ticket, outcome.retcode,
			               f"CRITICO: pending {ticket} rimosso ma ripiazzo fallito "
			               f"({outcome.message}) — ripiazzare a mano a {new_price}")
		return outcome

	positions = mt5.positions_get(ticket=ticket)
	if positions:
		# Posizione a mercato: modificabili solo SL/TP
		pos = positions[0]

		def build():
			return {
				'action': TRADE_ACTION_SLTP,
				'position': ticket,
				'symbol': pos.symbol,
				'sl': sl if sl > 0 else pos.sl,
				'tp': tp if tp > 0 else pos.tp,
			}
		return _send_with_retry(build)

	return Outcome(False, ticket, None, "ticket non trovato né tra i pending né tra le posizioni")


def _do_move_sl(signal):
	new_sl = float(signal['sl'] or 0)
	if new_sl <= 0:
		return Outcome(False, None, None, f"valore SL non valido: {signal['sl']}")

	symbol = mt5_client.resolve_symbol(signal['asset'])
	positions = mt5.positions_get(symbol=symbol) or ()
	if not positions:
		return Outcome(False, None, None, f"nessuna posizione aperta su {symbol}")

	updated, failed = 0, 0
	for pos in positions:
		if pos.sl == new_sl:
			continue	# già impostato: evita il retcode "no changes"

		def build(pos=pos):
			return {
				'action': TRADE_ACTION_SLTP,
				'position': pos.ticket,
				'symbol': symbol,
				'sl': new_sl,
				'tp': pos.tp,
			}
		outcome = _send_with_retry(build)
		if outcome.ok:
			updated += 1
		else:
			failed += 1
			logger.error(f"Move SL fallito su ticket {pos.ticket}: {outcome.message}")

	return Outcome(failed == 0, None, None,
	               f"SL spostato a {new_sl} su {updated} posizioni {symbol}" +
	               (f", {failed} fallite" if failed else ""))


def _do_close(signal):
	ticket = int(signal['order_id'])
	positions = mt5.positions_get(ticket=ticket)
	if not positions:
		return Outcome(False, ticket, None, "posizione non trovata (già chiusa?)")

	pos = positions[0]
	# Chiusura = deal di segno opposto agganciato alla posizione
	close_type = ORDER_TYPE_SELL if pos.type == ORDER_TYPE_BUY else ORDER_TYPE_BUY

	def build():
		return {
			'action': TRADE_ACTION_DEAL,
			'symbol': pos.symbol,
			'volume': pos.volume,
			'type': close_type,
			'position': ticket,
			'price': _market_price(pos.symbol, close_type),
			'deviation': _deviation_points(),
			'magic': pos.magic,
			'type_time': ORDER_TIME_GTC,
			'type_filling': _filling_mode(pos.symbol),
		}
	return _send_with_retry(build)


def _do_cancel(signal):
	ticket = int(signal['order_id'])

	def build():
		return {'action': TRADE_ACTION_REMOVE, 'order': ticket}
	return _send_with_retry(build)


# ----- Entry point -------------------------------------------------------

_HANDLERS = {
	'placement': _do_placement,
	'open': _do_open,
	'modify': _do_modify,
	'move_sl': _do_move_sl,
	'close': _do_close,
	'cancel': _do_cancel,
}


def execute(signal):
	"""
	Esegue un segnale su MT5 e restituisce un Outcome. Non solleva mai:
	ogni errore diventa un Outcome(ok=False, ...) che il chiamante logga
	ed eventualmente notifica.
	"""
	handler = _HANDLERS.get(signal['message_type'])
	if handler is None:
		return Outcome(False, None, None, f"message_type non gestito: {signal['message_type']}")

	try:
		outcome = handler(signal)
	except Exception as e:
		logger.exception(f"Errore inatteso nell'esecuzione del segnale {signal}")
		return Outcome(False, None, None, f"errore inatteso: {e}")

	log = logger.info if outcome.ok else logger.error
	log(f"Esecuzione {signal['message_type']} {signal['asset']}: "
	    f"{'OK' if outcome.ok else 'FALLITA'} - {outcome.message} "
	    f"(ticket={outcome.ticket}, retcode={outcome.retcode})")
	return outcome
