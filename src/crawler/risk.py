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

from crawler.config import load_config, get_setting

logger = logging.getLogger(__name__)

MODE_FIXED = 'FIXED'
MODE_RISK_PERCENT = 'RISK_PERCENT'
MODE_BALANCE = 'BALANCE'

# Deposito iniziale del conto, risolto all'avvio da main
# (config INITIAL_DEPOSIT > stato persistito > balance al primo avvio)
_initial_deposit = None


def set_initial_deposit(value):
	"""Imposta il deposito iniziale usato dalla modalità BALANCE."""
	global _initial_deposit
	_initial_deposit = float(value) if value else None
	if _initial_deposit:
		logger.info(f"Deposito iniziale per il sizing: {_initial_deposit:.2f}")


def daily_loss_budget():
	"""
	Perdita giornaliera consentita in valuta del conto: DAILY_LOSS_PERCENT
	(config [risk], default 5 — regola FTMO) per cento del deposito iniziale,
	quindi fissa anche quando il balance cresce. None se il deposito non è
	utilizzabile (non ancora noto, oppure ≤ 0 per un refuso in config: una
	soglia negativa invertirebbe i confronti di chi la usa).
	"""
	if _initial_deposit is None or _initial_deposit <= 0:
		return None
	percent = float(_risk_setting('DAILY_LOSS_PERCENT', '5') or 5)
	return _initial_deposit * percent / 100


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


def _balance_lot(signal, symbol_info, account_info, fixed_lot):
	"""
	MODE=BALANCE: LOT_PER_STEP lotti (default 0.01) ogni BALANCE_STEP
	(default 1000, valuta del conto) di capitale DISPONIBILE, dove
	disponibile = balance − (100 − AVAILABLE_PERCENT)% del deposito iniziale.

	Usa il BALANCE (solo P/L realizzato, niente flottante): con deposito
	100k e AVAILABLE_PERCENT=10, a balance 100k sono disponibili 10k
	-> 0.10 lotti; i lotti seguono i profitti/perdite realizzati.
	"""
	if account_info is None or _initial_deposit is None:
		logger.warning("MODE=BALANCE senza balance o deposito iniziale: fallback sul lotto fisso.")
		return normalize_volume(fixed_lot, symbol_info)

	available_percent = float(_risk_setting('AVAILABLE_PERCENT', '10') or 10)
	step_balance = float(_risk_setting('BALANCE_STEP', '1000') or 1000)
	lot_per_step = float(_risk_setting('LOT_PER_STEP', '0.01') or 0.01)

	floor_capital = _initial_deposit * (1 - available_percent / 100.0)
	available = max(0.0, account_info.balance - floor_capital)
	steps = math.floor(available / step_balance + 1e-9)
	lot = steps * lot_per_step

	if steps <= 0:
		logger.warning(
			f"MODE=BALANCE: capitale disponibile {available:.2f} sotto il primo scalino "
			f"({step_balance:.0f}): uso il volume minimo del simbolo."
		)
	normalized = normalize_volume(lot, symbol_info)
	logger.info(
		f"Sizing BALANCE {signal['asset']}: balance {account_info.balance:.2f}, "
		f"disponibile {available:.2f} -> lotto {normalized}"
	)
	return normalized


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

	if mode == MODE_BALANCE:
		return _balance_lot(signal, symbol_info, account_info, fixed_lot)

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
