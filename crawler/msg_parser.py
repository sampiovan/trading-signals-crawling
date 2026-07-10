import re
import logging

from order_registry import load_order_registry, get_order_ticket
from utils import generate_magic

logger = logging.getLogger(__name__)


# Definiamo una eccezione personalizzata
class OrderNotFoundException(Exception):
	pass


def parse_message(message_text, reply_text=None):
	"""
	Funzione principale che prova a riconoscere il tipo di messaggio
	chiamando in sequenza le funzioni dedicate. Prima di ogni parsing,
	viene aggiornato il registro globale degli ordini.

	reply_text è il testo del messaggio Telegram citato (se il messaggio
	è una risposta): alcuni parser lo usano per risalire all'ordine.

	Restituisce:
	- None se il messaggio non è riconosciuto;
	- []   se è riconosciuto ma non richiede azioni (es. notifiche);
	- una lista di segnali altrimenti (un messaggio può produrne più
	  di uno, es. chiusura di più posizioni).
	"""
	registry = load_order_registry()	# Aggiorna il registro globale
	logger.debug(f"Registro caricato: {registry}")

	# I parser che usano il reply ricevono anche reply_text.
	# L'ordine conta: i pattern più specifici devono precedere quelli
	# più generici (es. multi-close prima della chiusura singola).
	parsers = [
		parse_order_placement,
		parse_market_order,
		parse_order_open,
		parse_order_modify,
		parse_move_sl_all,
		lambda text: parse_move_sl_breakeven(text, reply_text),
		parse_orders_multi_close,	# PRIMA del close singolo: il suo pattern è più generico e catturerebbe (male) i multi-close
		parse_close_notification,	# idem: notifiche di chiusura automatica, nessuna azione
		parse_order_close,
		parse_order_cancel
	]

	for parser in parsers:
		result = parser(message_text)
		if result is None:
			continue
		# Normalizza al contratto a lista: i parser storici
		# restituiscono un singolo dict, i nuovi una lista.
		if isinstance(result, dict):
			return [result]
		return result
	return None


def parse_order_placement(text):
	"""
	Riconosce un messaggio di piazzamento ordine.
	Esempio:
		"📈BUY LIMIT  EUR/USD
		 Prezzo 1.12500  (di apertura)

		 Stop Loss   🔴 1.08500

		 Take Profit  🟢  1.20000"
	"""
	pattern = re.compile(
		r"(?i)^(?:\S+\s*)?(BUY LIMIT|SELL LIMIT|BUY STOP|SELL STOP|BUY|SELL)\s+([A-Z]{3}/[A-Z]{3}).*?Prezzo\s+([\d\.]+).*?(?:di\s+apertura).*?Stop Loss\s*[^\d]*([\d\.]+).*?Take Profit\s*[^\d]*([\d\.]+)",
		re.DOTALL
	)
	match = pattern.search(text)

	if match:
		return {
			'order_id': '',			# Non ancora noto al momento della creazione
			'magic_number': generate_magic(),		# Genera un magic number per il segnale
			'message_type': 'placement',
			'signal_type': match.group(1).upper(),
			'asset': match.group(2).upper().replace("/", ""),
			'entry': match.group(3),
			'sl': match.group(4),
			'tp': match.group(5),
			'comment': ''
		}
	return None


def parse_market_order(text):
	"""
	Riconosce un'operazione diretta a mercato (esecuzione immediata al
	prezzo corrente, non un ordine pendente).
	Esempio:
		"📊GBP/USD

		ATTENZIONE QUESTA E' UNA OPERAZIONE IN SELL DIRETTA A MERCATO:

		1) Piazzare un SELL su GBP/USD  ADESSO AL PREZZO ATTUALE:

		📉SELL  GBP/USD
		Prezzo 1.34121  (nostro prezzo di apertura)

		Stop Loss   🔴 1.36100

		Take Profit  🟢 1.30000
		..."

	Produce un segnale 'placement' con signal_type BUY/SELL: l'EA esegue
	i tipi a mercato al prezzo corrente (Ask/Bid) e registra il ticket con
	l'entry indicata dal provider, usata poi per il matching dei messaggi
	successivi (move SL, close).
	"""
	trigger = re.compile(r"(?i)OPERAZIONE\s+IN\s+(?:BUY|SELL)\s+DIRETTA\s+A\s+MERCATO")
	if not trigger.search(text):
		return None

	pattern = re.compile(
		r"(?i)(BUY|SELL)\s+([A-Z]{3}/[A-Z]{3})\s*.*?Prezzo\s+([\d\.]+).*?apertura.*?Stop Loss\s*[^\d]*([\d\.]+).*?Take Profit\s*[^\d]*([\d\.]+)",
		re.DOTALL
	)
	match = pattern.search(text)
	if not match:
		logger.warning("Operazione a mercato riconosciuta ma blocco ordine non estratto.")
		return None

	return {
		'order_id': '',
		'magic_number': generate_magic(),
		'message_type': 'placement',
		'signal_type': match.group(1).upper(),
		'asset': match.group(2).upper().replace("/", ""),
		'entry': match.group(3),
		'sl': match.group(4),
		'tp': match.group(5),
		'comment': ''
	}


def parse_order_open(text):
	"""
	Riconosce un messaggio di apertura ordine.
	Esempio:
		"Ordine Buy  EUR/USD    Aperto
		Prezzo di ingresso  1.12500"
	"""
	pattern = re.compile(
		r"(?i)Ordine\s+(BUY|SELL)\s+([A-Z]{3}/[A-Z]{3}).*?Aperto.*?Prezzo di ingresso\s+([\d\.]+)",
		re.DOTALL
	)
	match = pattern.search(text)

	if match:
		# Estraggo i valori
		signal_type = match.group(1).upper()
		asset = match.group(2).upper().replace("/", "")
		entry = match.group(3)

		order_id, magic_number = get_order_ticket(asset, entry, signal_type)
		if not order_id:
			# Nessun placement precedente nel registro: è un ordine diretto
			# a mercato. order_id vuoto segnala all'EA di aprire la posizione
			# (e registrarne il ticket) invece di verificare un pending.
			logger.info(f"Nessun pending nel registro per open {asset} @ {entry}: ordine diretto a mercato.")
			order_id = ''
			magic_number = generate_magic()
		return {
			'order_id': order_id,
			'magic_number': magic_number,
			'message_type': 'open',
			'signal_type': signal_type,
			'asset': asset,
			'entry': entry,
			'sl': '',
			'tp': '',
			'comment': ''
		}
	return None


def parse_order_modify(text):
	"""
	Riconosce un messaggio di modifica ordine.
	Esempio:
		"(BUY LIMIT EUR/USD) - MODIFICARE IL PREZZO DI INGRESSO DA 1.12500 A  1.13000  mantenendo uguale Stop loss e Take Profit 👍✅"
	"""
	pattern = re.compile(
		r"(?i)\((BUY LIMIT|SELL LIMIT|BUY STOP|SELL STOP)\s+([A-Z]{3}/[A-Z]{3})\).*?MODIFICARE IL PREZZO DI INGRESSO DA\s+([\d\.]+)\s+A\s+([\d\.]+)",
		re.DOTALL
	)
	match = pattern.search(text)

	if match:
		# Estraggo i valori
		signal_type = match.group(1).upper()
		asset = match.group(2).upper().replace("/", "")
		old_price = match.group(3)
		new_price = match.group(4)	# Vecchio prezzo limite

		# Cerco l'ordine per il vecchio prezzo d'entrata
		order_id, magic_number = get_order_ticket(asset, old_price, signal_type)
		if not order_id:
			raise OrderNotFoundException(f"Order ID non trovato per segnale open: asset={asset}, entry={old_price}, signal={signal_type}")
		return {
			'order_id': order_id,
			'magic_number': magic_number,
			'message_type': 'modify',
			'signal_type': signal_type,
			'asset': asset,
			'entry': new_price,		# Nuovo prezzo di ingresso
			'sl': 0,
			'tp': 0,
			'comment': ''
		}
	return None


def _move_sl_signal(asset, sl_value):
	"""Segnale 'move_sl': l'EA applica il nuovo SL a TUTTE le posizioni a mercato sull'asset."""
	return {
		'order_id': '',
		'magic_number': '',
		'message_type': 'move_sl',
		'signal_type': '',
		'asset': asset,
		'entry': 0,
		'sl': sl_value,
		'tp': 0,
		'comment': ''
	}


def parse_move_sl_all(text):
	"""
	Riconosce la richiesta esplicita di spostare lo stop loss su tutte
	le operazioni in corso su un asset.
	Esempio:
		"📊EUR/USD

		MODIFICARE IL VALORE DI STOP LOSS SU TUTTE LE OPERAZIONI IN CORSO SU EUR/USD a  0.90000"
	"""
	pattern = re.compile(
		r"(?i)MODIFICARE\s+IL\s+VALORE\s+DI\s+STOP\s+LOSS\s+SU\s+TUTTE\s+LE\s+OPERAZIONI\s+IN\s+CORSO\s+SU\s+([A-Z]{3}/[A-Z]{3})\s+a\s+([\d\.]+)",
		re.DOTALL
	)
	match = pattern.search(text)

	if match:
		asset = match.group(1).upper().replace("/", "")
		sl_value = match.group(2)
		return _move_sl_signal(asset, sl_value)
	return None


def parse_move_sl_breakeven(text, reply_text=None):
	"""
	Riconosce lo spostamento dello stop loss a breakeven.
	Esempio:
		"GBP/USD Move Stop Loss to Breakeven o comunque in posizione di profitto a  1.33890✅"

	Questo messaggio arriva di solito come RISPOSTA Telegram al messaggio
	di apertura dell'ordine: se il testo citato (reply_text) è parsabile
	e l'ordine è nel registro, il segnale è una 'modify' mirata sul
	singolo ticket; altrimenti fallback su 'move_sl' (tutte le posizioni
	a mercato sull'asset).
	"""
	pattern = re.compile(
		r"(?i)([A-Z]{3}/[A-Z]{3})\s+Move\s+Stop\s+Loss\s+to\s+Breakeven.*?a\s+([\d\.]+)",
		re.DOTALL
	)
	match = pattern.search(text)
	if not match:
		return None

	asset = match.group(1).upper().replace("/", "")
	sl_value = match.group(2)

	# Prova a risalire all'ordine esatto tramite il messaggio citato
	if reply_text:
		ref = parse_order_placement(reply_text) or parse_order_open(reply_text)
		if ref and ref['asset'] == asset:
			order_id, magic_number = get_order_ticket(asset, ref['entry'], '')
			if order_id:
				logger.info(f"Move SL mirato via reply: asset={asset}, ticket={order_id}, nuovo SL={sl_value}")
				return {
					'order_id': order_id,
					'magic_number': magic_number,
					'message_type': 'modify',
					'signal_type': '',
					'asset': asset,
					'entry': 0,		# invariato: l'EA mantiene il prezzo corrente
					'sl': sl_value,
					'tp': 0,		# invariato
					'comment': ''
				}
		logger.info(f"Move SL: reply non risolto nel registro, applico a tutte le posizioni su {asset}.")

	return _move_sl_signal(asset, sl_value)


def parse_orders_multi_close(text):
	"""
	Riconosce un messaggio di chiusura di PIÙ posizioni e restituisce
	una lista di segnali 'close', uno per posizione. Le posizioni possono
	essere anche su asset diversi.
	Esempio:
		"📊AUD/NZD

		CHIUDERE MANUALMENTE DUE POSIZIONI DI CUI:

		UNA IN PROFITTO su           AUD/NZD   (1.21600)

		UNA IN PROFITTO su          AUD/NZD  (1.21403)

		TOTALE IN PROFITTO✅✅✅"
	"""
	trigger = re.compile(r"(?i)CHIUDERE\s+MANUALMENTE\s+\w+\s+POSIZIONI")
	if not trigger.search(text):
		return None

	positions = re.findall(
		r"(?i)UNA\s+IN\s+(?:PROFITTO|PERDITA|PARI)\s+su\s+([A-Z]{3}/[A-Z]{3})\s*\(([\d\.]+)",
		text
	)
	if not positions:
		logger.warning("Messaggio multi-close riconosciuto ma nessuna posizione estratta.")
		return None

	signals = []
	for raw_asset, entry_price in positions:
		asset = raw_asset.upper().replace("/", "")

		order_id, magic_number = get_order_ticket(asset, entry_price, '')
		if not order_id:
			# Successo parziale: non scartare le altre posizioni del messaggio
			logger.error(f"Multi-close: ordine non trovato nel registro per asset={asset}, entry={entry_price}. Posizione saltata.")
			continue
		signals.append({
			'order_id': order_id,
			'magic_number': magic_number,
			'message_type': 'close',
			'asset': asset,
			'signal_type': '',
			'entry': 0.0,	# Prezzo di chiusura (a mercato)
			'sl': '',
			'tp': '',
			'comment': ''
		})

	if not signals:
		raise OrderNotFoundException(
			f"Multi-close: nessuna delle {len(positions)} posizioni trovata nel registro."
		)
	return signals


def parse_close_notification(text):
	"""
	Riconosce le NOTIFICHE di chiusura automatica: la posizione è già
	stata chiusa dal broker (stop loss o breakeven raggiunto), quindi
	non serve alcuna azione — solo log.
	Esempi:
		"CHIUSA A BREAKEVEN  GBP/USD A  (1.35290)✅"
		"CHIUSURA IN STOP (4.704.50)

		🔹 Operazione che si è chiusa automaticamente questa notte."

	Restituisce [] (riconosciuto, nessun segnale) o None se non è una notifica.
	"""
	pattern = re.compile(r"(?i)CHIUSA\s+A\s+BREAKEVEN|CHIUSURA\s+IN\s+STOP")
	if pattern.search(text):
		logger.info("Notifica di chiusura automatica (SL/breakeven eseguito dal broker): nessuna azione necessaria.")
		return []
	return None


def parse_order_close(text):
	"""
	Riconosce un messaggio di chiusura ordine.
	Esempio:
		"📊EUR/USD

		CHIUDERE MANUALMENTE UNA POSIZIONE IN PROFITTO SU EUR/USD  (1.12500)  ✅✅✅"
	"""
	pattern = re.compile(
		r"(?i)[\s\S]*([A-Z]{3}/[A-Z]{3}).*?CHIUDERE.*?\(([\d\.]+)\)",
		re.DOTALL
	)
	match = pattern.search(text)

	if match:
		# Estraggo i valori
		asset = match.group(1).upper().replace("/", "")
		entry_price = match.group(2)
		close_price = 0.0

		# Cerco l'ordine per il prezzo d'entrata
		order_id, magic_number = get_order_ticket(asset, entry_price, '')
		if not order_id:
			raise OrderNotFoundException(f"Order ID non trovato per segnale close: asset={asset}, entry={entry_price}")
		return {
			'order_id': order_id,
			'magic_number': magic_number,
			'message_type': 'close',
			'asset': asset,
			'signal_type': '',	# Non specificato
			'entry': close_price,	# Prezzo di chiusura
			'sl': '',
			'tp': '',
			'comment': ''
		}
	return None


def parse_order_cancel(text):
	"""
	Riconosce un messaggio di annullamento ordine.
	Esempio:
		"ANNULLARE BUY LIMIT EUR/USD ... (1.12500)✅"
	"""
	pattern = re.compile(
		r"(?i)ANNULLARE\s+(BUY LIMIT|SELL LIMIT|BUY STOP|SELL STOP)\s+([A-Z]{3}/[A-Z]{3}).*?\(([\d\.]+)",
		re.DOTALL
	)
	match = pattern.search(text)

	if match:
		# Estraggo i valori
		signal_type = match.group(1).upper()
		asset = match.group(2).upper().replace("/", "")
		entry = match.group(3)

		order_id, magic_number = get_order_ticket(asset, entry, signal_type)
		if not order_id:
			raise OrderNotFoundException(f"Order ID non trovato per segnale cancel: asset={asset}, entry={entry}, signal={signal_type}")
		return {
			'order_id': order_id,
			'magic_number': magic_number,
			'message_type': 'cancel',
			'signal_type': signal_type,
			'asset': asset,
			'entry': entry,
			'sl': '',
			'tp': '',
			'comment': ''
		}
	return None
