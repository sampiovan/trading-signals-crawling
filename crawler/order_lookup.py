"""
Lookup degli ordini direttamente su posizioni e ordini pendenti LIVE
del conto MT5. Sostituisce il registro CSV della v1: la fonte di verità
sono i dati reali del terminale, quindi non esiste più uno stato
parallelo che possa divergere (ordini chiusi che restano nel registro,
prezzi non aggiornati dopo le modify, ecc.).
"""
import logging

import mt5_client

try:
	import MetaTrader5 as mt5
except ImportError:	# pragma: no cover - dipende dalla piattaforma
	mt5 = None

logger = logging.getLogger(__name__)

# Tipo ordine/posizione MT5 -> signal_type dei messaggi del canale
_MT5_TYPE_TO_SIGNAL = {
	0: 'BUY',
	1: 'SELL',
	2: 'BUY LIMIT',
	3: 'SELL LIMIT',
	4: 'BUY STOP',
	5: 'SELL STOP',
}


def pip_size(asset):
	"""
	Dimensione del pip per l'asset: 0.01 per le coppie quotate in JPY,
	0.0001 per tutte le altre. Una tolleranza assoluta unica sarebbe
	troppo stretta sulle coppie JPY (che quotano ~150) e troppo larga
	su altre.
	"""
	if asset.strip().upper().endswith('JPY'):
		return 0.01
	return 0.0001


def get_order_ticket(asset, entry, signal_type, tol_pips=2):
	"""
	Cerca tra le posizioni aperte e gli ordini pendenti live quello che
	meglio corrisponde al segnale: stesso asset, stesso signal_type (se
	indicato) e prezzo d'ingresso entro tol_pips pip. Tra i candidati
	vince quello con l'entry più vicina.
	Restituisce (ticket, magic_number) come stringhe, o (None, None).
	"""
	target_asset = asset.strip().upper()
	target_signal = signal_type.strip().upper()

	try:
		target_entry = float(entry)
	except Exception:
		target_entry = 0.0

	tol = tol_pips * pip_size(target_asset)

	try:
		symbol = mt5_client.resolve_symbol(target_asset)
	except Exception:
		logger.warning(f"Lookup: simbolo non risolvibile per asset {target_asset}.")
		return None, None

	# Posizioni a mercato e ordini pendenti sono entrambi candidati:
	# i messaggi del canale citano gli uni e gli altri per prezzo.
	candidates = []
	for pos in (mt5.positions_get(symbol=symbol) or ()):
		candidates.append((pos.ticket, pos.magic, pos.price_open, _MT5_TYPE_TO_SIGNAL.get(pos.type, '')))
	for order in (mt5.orders_get(symbol=symbol) or ()):
		candidates.append((order.ticket, order.magic, order.price_open, _MT5_TYPE_TO_SIGNAL.get(order.type, '')))

	best = None
	best_distance = None
	for ticket, magic, price_open, candidate_signal in candidates:
		# I messaggi di chiusura non indicano il tipo: confronta solo se presente
		if target_signal and candidate_signal != target_signal:
			continue
		distance = abs(price_open - target_entry)
		if distance < tol and (best_distance is None or distance < best_distance):
			best = (ticket, magic)
			best_distance = distance

	if best:
		logger.info(f"Trovato ordine live: asset {target_asset}, entry {target_entry}, "
		            f"ticket {best[0]}, magic {best[1]}")
		return str(best[0]), str(best[1])
	return None, None
