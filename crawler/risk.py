"""
Calcolo della dimensione del lotto per i segnali.

Due modalità, configurabili nella sezione [risk] di config.ini:
- FIXED (default): lotto fisso da FIXED_LOT — parità di comportamento con la v1.
- RISK_PERCENT: lotto calcolato dal rischio massimo tollerato per trade
  (RISK_PERCENT % dell'equity) e dalla distanza dello Stop Loss.

In ogni caso il volume viene normalizzato sui limiti del simbolo
(volume_min / volume_max / volume_step).
"""
import math
import logging

from config import load_config, get_setting

logger = logging.getLogger(__name__)

MODE_FIXED = 'FIXED'
MODE_RISK_PERCENT = 'RISK_PERCENT'


def _risk_setting(key, default):
	return get_setting(load_config(), 'risk', key, default=default)


def normalize_volume(lot, symbol_info):
	"""
	Adegua il lotto ai limiti del simbolo: arrotonda per difetto al
	multiplo di volume_step e clampa su volume_min/volume_max.
	"""
	step = getattr(symbol_info, 'volume_step', 0.01) or 0.01
	lot = math.floor(lot / step + 1e-9) * step
	lot = max(symbol_info.volume_min, min(lot, symbol_info.volume_max))
	# volume_step tipici: 0.01 o 0.1 -> 2 decimali bastano ad evitare
	# artefatti float tipo 0.30000000000000004
	return round(lot, 2)


def compute_lot(signal, symbol_info, account_info):
	"""
	Restituisce il volume da usare per l'ordine del segnale.

	Con MODE=RISK_PERCENT: rischio = equity * RISK_PERCENT/100; la perdita
	per lotto se lo SL viene colpito è |entry − sl| / trade_tick_size *
	trade_tick_value; il lotto è il rapporto tra i due. Se il segnale non
	ha SL o entry validi (o mancano i dati del conto) si ricade sul lotto
	fisso, con warning.
	"""
	fixed_lot = float(_risk_setting('FIXED_LOT', '0.01') or 0.01)
	mode = _risk_setting('MODE', MODE_FIXED).upper() or MODE_FIXED

	if mode != MODE_RISK_PERCENT:
		if mode != MODE_FIXED:
			logger.warning(f"[risk] MODE '{mode}' non riconosciuto: uso FIXED.")
		return normalize_volume(fixed_lot, symbol_info)

	try:
		entry = float(signal['entry'] or 0)
		sl = float(signal['sl'] or 0)
	except (TypeError, ValueError):
		entry, sl = 0.0, 0.0

	if sl <= 0 or entry <= 0 or account_info is None:
		logger.warning(
			f"RISK_PERCENT non applicabile al segnale {signal['message_type']} "
			f"{signal['asset']} (entry={signal['entry']}, sl={signal['sl']}): "
			"fallback sul lotto fisso."
		)
		return normalize_volume(fixed_lot, symbol_info)

	risk_percent = float(_risk_setting('RISK_PERCENT', '1.0') or 1.0)
	risk_amount = account_info.equity * risk_percent / 100.0

	ticks = abs(entry - sl) / symbol_info.trade_tick_size
	loss_per_lot = ticks * symbol_info.trade_tick_value
	if loss_per_lot <= 0:
		logger.warning(f"Dati simbolo non validi per il sizing ({signal['asset']}): fallback sul lotto fisso.")
		return normalize_volume(fixed_lot, symbol_info)

	lot = normalize_volume(risk_amount / loss_per_lot, symbol_info)
	logger.info(
		f"Sizing {signal['asset']}: rischio {risk_percent}% di {account_info.equity} "
		f"= {risk_amount:.2f}, SL a {abs(entry - sl):.5f} -> lotto {lot}"
	)
	return lot
