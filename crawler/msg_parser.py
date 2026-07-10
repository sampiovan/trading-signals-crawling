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
		parse_order_open,
		parse_order_modify,
		parse_orders_multi_close,	# PRIMA del close singolo: il suo pattern è più generico e catturerebbe (male) i multi-close
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
