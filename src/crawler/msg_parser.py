"""
Riconoscimento dei messaggi del canale Telegram e conversione in segnali
di trading per l'Expert Advisor (via CSV, vedi signals_csv).

Contratto di parse_message(text, reply_text=None):
- None  -> messaggio non riconosciuto;
- []    -> riconosciuto ma nessuna azione (es. notifiche di chiusura automatica);
- lista -> uno o più segnali (dict con lo schema di _build_signal).

I parser sono elencati (e definiti nel file) nell'ordine di dispatch:
i pattern più specifici precedono quelli più generici.
"""
import re
import random
import logging

from crawler.order_lookup import get_order_ticket

logger = logging.getLogger(__name__)


def generate_magic():
	"""Genera un magic number casuale (stringa di 5 cifre) che identifica il segnale."""
	return str(random.randint(10000, 99999))


# Definiamo una eccezione personalizzata
class OrderNotFoundException(Exception):
	pass


# -------------------- Regex dei messaggi --------------------
# Compilate una volta a livello di modulo. Nelle regex con più gruppi
# l'asset è sempre nel formato XXX/YYY e i prezzi sono decimali col punto.

# Gruppo di cattura del prezzo, tollerante ai refusi del canale con spazi
# spuri DENTRO il numero ("1. 20200", "1 .20200"): consente spazi/tab tra
# le cifre ma MAI i newline (le regex usano DOTALL), ed è ancorato a una
# cifra a inizio e fine, così non ingloba testo o prezzi adiacenti. Va poi
# ripulito con _clean_price. Un solo gruppo: gli indici match.group() restano.
_PRICE = r"(\d[\d.\t ]*\d|\d)"

# Piazzamento/apertura: usate sia dai parser sia da _extract_order_ref
# per risolvere i reply Telegram. In entrambe: group(2)=asset, group(3)=entry.
_PLACEMENT_RE = re.compile(
	rf"(?i)^(?:\S+\s*)?(BUY LIMIT|SELL LIMIT|BUY STOP|SELL STOP|BUY|SELL)\s+([A-Z]{{3}}/[A-Z]{{3}}).*?Prezzo\s+{_PRICE}.*?(?:di\s+apertura).*?Stop Loss\s*[^\d]*{_PRICE}.*?Take Profit\s*[^\d]*{_PRICE}",
	re.DOTALL
)
_OPEN_RE = re.compile(
	# Il canale alterna "Prezzo di ingresso" e "Livello di ingresso"
	rf"(?i)Ordine\s+(BUY|SELL)\s+([A-Z]{{3}}/[A-Z]{{3}}).*?Aperto.*?(?:Prezzo|Livello)\s+di\s+ingresso\s+{_PRICE}",
	re.DOTALL
)

# Operazione diretta a mercato: trigger + blocco ordine
_MARKET_TRIGGER_RE = re.compile(r"(?i)OPERAZIONE\s+IN\s+(?:BUY|SELL)\s+DIRETTA\s+A\s+MERCATO")
_MARKET_ORDER_RE = re.compile(
	rf"(?i)(BUY|SELL)\s+([A-Z]{{3}}/[A-Z]{{3}})\s*.*?Prezzo\s+{_PRICE}.*?apertura.*?Stop Loss\s*[^\d]*{_PRICE}.*?Take Profit\s*[^\d]*{_PRICE}",
	re.DOTALL
)

# Modifica del prezzo d'ingresso di un pending
_MODIFY_RE = re.compile(
	rf"(?i)\((BUY LIMIT|SELL LIMIT|BUY STOP|SELL STOP)\s+([A-Z]{{3}}/[A-Z]{{3}})\).*?MODIFICARE IL PREZZO DI INGRESSO DA\s+{_PRICE}\s+A\s+{_PRICE}",
	re.DOTALL
)

# Spostamento dello stop loss (due varianti)
_MOVE_SL_ALL_RE = re.compile(
	rf"(?i)MODIFICARE\s+IL\s+VALORE\s+DI\s+STOP\s+LOSS\s+SU\s+TUTTE\s+LE\s+OPERAZIONI\s+IN\s+CORSO\s+SU\s+([A-Z]{{3}}/[A-Z]{{3}})\s+a\s+{_PRICE}",
	re.DOTALL
)
_MOVE_SL_BREAKEVEN_RE = re.compile(
	rf"(?i)([A-Z]{{3}}/[A-Z]{{3}})\s+Move\s+Stop\s+Loss\s+to\s+Breakeven.*?a\s+{_PRICE}",
	re.DOTALL
)

# Chiusure: multipla (trigger + righe posizione), notifica automatica, singola
_MULTI_CLOSE_TRIGGER_RE = re.compile(r"(?i)CHIUDERE\s+MANUALMENTE\s+\w+\s+POSIZIONI")
_MULTI_CLOSE_POSITION_RE = re.compile(
	rf"(?i)UNA\s+IN\s+(?:PROFITTO|PERDITA|PARI)\s+su\s+([A-Z]{{3}}/[A-Z]{{3}})\s*\({_PRICE}"
)
_CLOSE_NOTIFICATION_RE = re.compile(r"(?i)CHIUSA\s+A\s+BREAKEVEN|CHIUSURA\s+IN\s+STOP")
_CLOSE_RE = re.compile(
	rf"(?i)[\s\S]*([A-Z]{{3}}/[A-Z]{{3}}).*?CHIUDERE.*?\({_PRICE}\)",
	re.DOTALL
)

# Annullamento di un pending
_CANCEL_RE = re.compile(
	rf"(?i)ANNULLARE\s+(BUY LIMIT|SELL LIMIT|BUY STOP|SELL STOP)\s+([A-Z]{{3}}/[A-Z]{{3}}).*?\({_PRICE}",
	re.DOTALL
)


# -------------------- Helper privati --------------------

def _clean_price(raw):
	"""
	Ripulisce un prezzo catturato dal refuso dello spazio interno del canale
	("1. 20200" -> "1.20200"): rimuove spazi e tab. Logica di pulizia in un
	solo punto, richiamata da ogni parser dove estrae un prezzo (vedi _PRICE).
	"""
	return re.sub(r"[\t ]", "", raw) if raw else raw


def _build_signal(message_type, asset, signal_type='', order_id='', magic_number='',
                  entry='', sl='', tp='', comment=''):
	"""Unica definizione dello schema del segnale (vedi signals_csv.CSV_HEADER)."""
	return {
		'order_id': order_id,
		'magic_number': magic_number,
		'message_type': message_type,
		'signal_type': signal_type,
		'asset': asset,
		'entry': entry,
		'sl': sl,
		'tp': tp,
		'comment': comment
	}


def _find_order_or_raise(asset, entry, signal_type, context):
	"""Lookup nel registro; solleva OrderNotFoundException se l'ordine non c'è."""
	order_id, magic_number = get_order_ticket(asset, entry, signal_type)
	if not order_id:
		raise OrderNotFoundException(
			f"Order ID non trovato per segnale {context}: asset={asset}, entry={entry}, signal={signal_type}"
		)
	return order_id, magic_number


def _close_signal(asset, entry_price):
	"""
	Chiusura atomica di UNA posizione: lookup per (asset, prezzo d'entrata)
	e costruzione del segnale 'close'. Solleva OrderNotFoundException se
	la posizione non è nel registro.
	"""
	order_id, magic_number = _find_order_or_raise(asset, entry_price, '', 'close')
	return _build_signal(
		'close', asset,
		order_id=order_id,
		magic_number=magic_number,
		entry=0.0		# chiusura al prezzo di mercato corrente
	)


def _extract_order_ref(text):
	"""
	Estrae (asset, entry) da un messaggio di piazzamento o di apertura,
	SENZA lookup nel registro né log: serve a identificare l'ordine
	citato quando un segnale arriva come risposta Telegram.
	"""
	match = _PLACEMENT_RE.search(text) or _OPEN_RE.search(text) or _MARKET_ORDER_RE.search(text)
	if match:
		return match.group(2).upper().replace("/", ""), _clean_price(match.group(3))
	return None


def _move_sl_signal(asset, sl_value):
	"""Segnale 'move_sl': l'EA applica il nuovo SL a TUTTE le posizioni a mercato sull'asset."""
	return _build_signal('move_sl', asset, entry=0, sl=sl_value, tp=0)


# -------------------- Dispatcher --------------------

def parse_message(message_text, reply_text=None):
	"""
	Funzione principale che prova a riconoscere il tipo di messaggio
	chiamando in sequenza le funzioni dedicate. Le lookup degli ordini
	avvengono direttamente sulle posizioni live del conto MT5.

	reply_text è il testo del messaggio Telegram citato (se il messaggio
	è una risposta): alcuni parser lo usano per risalire all'ordine.

	Restituisce:
	- None se il messaggio non è riconosciuto;
	- []   se è riconosciuto ma non richiede azioni (es. notifiche);
	- una lista di segnali altrimenti (un messaggio può produrne più
	  di uno, es. chiusura di più posizioni).
	"""
	# I parser che usano il reply ricevono anche reply_text.
	# L'ordine conta: i pattern più specifici devono precedere quelli
	# più generici (es. multi-close prima della chiusura singola).
	parsers = [
		parse_order_placement,
		parse_market_order,
		lambda text: parse_order_open(text, reply_text),
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


# -------------------- Parser (in ordine di dispatch) --------------------

def parse_order_placement(text):
	"""
	Riconosce un messaggio di piazzamento ordine.
	Esempio:
		"📈BUY LIMIT  EUR/USD
		 Prezzo 1.12500  (di apertura)

		 Stop Loss   🔴 1.08500

		 Take Profit  🟢  1.20000"
	"""
	match = _PLACEMENT_RE.search(text)

	if match:
		return _build_signal(
			'placement',
			match.group(2).upper().replace("/", ""),
			signal_type=match.group(1).upper(),
			magic_number=generate_magic(),	# order_id non ancora noto: il segnale è identificato dal magic
			entry=_clean_price(match.group(3)),
			sl=_clean_price(match.group(4)),
			tp=_clean_price(match.group(5))
		)
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
	if not _MARKET_TRIGGER_RE.search(text):
		return None

	match = _MARKET_ORDER_RE.search(text)
	if not match:
		logger.warning("Operazione a mercato riconosciuta ma blocco ordine non estratto.")
		return None

	return _build_signal(
		'placement',
		match.group(2).upper().replace("/", ""),
		signal_type=match.group(1).upper(),
		magic_number=generate_magic(),
		entry=_clean_price(match.group(3)),
		sl=_clean_price(match.group(4)),
		tp=_clean_price(match.group(5))
	)


def parse_order_open(text, reply_text=None):
	"""
	Riconosce un messaggio di apertura ordine: la NOTIFICA che un pending
	piazzato in precedenza è stato riempito (non un comando di apertura).
	Esempio:
		"Ordine Buy  EUR/USD    Aperto
		Prezzo di ingresso  1.12500"

	Arriva spesso come RISPOSTA Telegram al messaggio di piazzamento: se il
	testo citato (reply_text) è parsabile e sullo stesso asset, il lookup usa
	il suo prezzo (più affidabile di quello del messaggio, che il canale a
	volte storpia), altrimenti il prezzo del messaggio stesso. Se il pending
	non si trova si solleva OrderNotFoundException: mai aprire a mercato alla
	cieca, sarebbe un duplicato dell'esposizione già pendente.
	"""
	match = _OPEN_RE.search(text)

	if match:
		signal_type = match.group(1).upper()
		asset = match.group(2).upper().replace("/", "")
		entry = _clean_price(match.group(3))

		# Il reply al placement è la fonte più affidabile del prezzo d'ingresso
		ref = _extract_order_ref(reply_text) if reply_text else None
		lookup_entry = ref[1] if ref and ref[0] == asset else entry

		order_id, magic_number = _find_order_or_raise(asset, lookup_entry, signal_type, 'open')
		return _build_signal(
			'open', asset,
			signal_type=signal_type,
			order_id=order_id,
			magic_number=magic_number,
			entry=entry
		)
	return None


def parse_order_modify(text):
	"""
	Riconosce un messaggio di modifica ordine.
	Esempio:
		"(BUY LIMIT EUR/USD) - MODIFICARE IL PREZZO DI INGRESSO DA 1.12500 A  1.13000  mantenendo uguale Stop loss e Take Profit 👍✅"
	"""
	match = _MODIFY_RE.search(text)

	if match:
		# Estraggo i valori
		signal_type = match.group(1).upper()
		asset = match.group(2).upper().replace("/", "")
		old_price = _clean_price(match.group(3))
		new_price = _clean_price(match.group(4))

		# La lookup avviene sul VECCHIO prezzo d'entrata, il segnale porta il nuovo
		order_id, magic_number = _find_order_or_raise(asset, old_price, signal_type, 'modify')
		return _build_signal(
			'modify', asset,
			signal_type=signal_type,
			order_id=order_id,
			magic_number=magic_number,
			entry=new_price,	# Nuovo prezzo di ingresso
			sl=0,				# 0 = invariato
			tp=0				# 0 = invariato
		)
	return None


def parse_move_sl_all(text):
	"""
	Riconosce la richiesta esplicita di spostare lo stop loss su tutte
	le operazioni in corso su un asset.
	Esempio:
		"📊EUR/USD

		MODIFICARE IL VALORE DI STOP LOSS SU TUTTE LE OPERAZIONI IN CORSO SU EUR/USD a  0.90000"
	"""
	match = _MOVE_SL_ALL_RE.search(text)

	if match:
		asset = match.group(1).upper().replace("/", "")
		sl_value = _clean_price(match.group(2))
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
	match = _MOVE_SL_BREAKEVEN_RE.search(text)
	if not match:
		return None

	asset = match.group(1).upper().replace("/", "")
	sl_value = _clean_price(match.group(2))

	# Prova a risalire all'ordine esatto tramite il messaggio citato
	if reply_text:
		ref = _extract_order_ref(reply_text)
		if ref and ref[0] == asset:
			order_id, magic_number = get_order_ticket(asset, ref[1], '')
			if order_id:
				logger.info(f"Move SL mirato via reply: asset={asset}, ticket={order_id}, nuovo SL={sl_value}")
				return _build_signal(
					'modify', asset,
					order_id=order_id,
					magic_number=magic_number,
					entry=0,	# invariato: l'EA mantiene il prezzo corrente
					sl=sl_value,
					tp=0		# invariato
				)
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
	if not _MULTI_CLOSE_TRIGGER_RE.search(text):
		return None

	positions = _MULTI_CLOSE_POSITION_RE.findall(text)
	if not positions:
		logger.warning("Messaggio multi-close riconosciuto ma nessuna posizione estratta.")
		return None

	signals = []
	for raw_asset, entry_price in positions:
		asset = raw_asset.upper().replace("/", "")
		try:
			signals.append(_close_signal(asset, _clean_price(entry_price)))
		except OrderNotFoundException:
			# Successo parziale: non scartare le altre posizioni del messaggio
			logger.error(f"Multi-close: ordine non trovato nel registro per asset={asset}, entry={entry_price}. Posizione saltata.")

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
	if _CLOSE_NOTIFICATION_RE.search(text):
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
	match = _CLOSE_RE.search(text)

	if match:
		asset = match.group(1).upper().replace("/", "")
		entry_price = _clean_price(match.group(2))
		return _close_signal(asset, entry_price)
	return None


def parse_order_cancel(text):
	"""
	Riconosce un messaggio di annullamento ordine.
	Esempio:
		"ANNULLARE BUY LIMIT EUR/USD ... (1.12500)✅"
	"""
	match = _CANCEL_RE.search(text)

	if match:
		# Estraggo i valori
		signal_type = match.group(1).upper()
		asset = match.group(2).upper().replace("/", "")
		entry = _clean_price(match.group(3))

		order_id, magic_number = _find_order_or_raise(asset, entry, signal_type, 'cancel')
		return _build_signal(
			'cancel', asset,
			signal_type=signal_type,
			order_id=order_id,
			magic_number=magic_number,
			entry=entry
		)
	return None
