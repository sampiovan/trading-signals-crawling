"""
Guardia delle posizioni aperte in perdita: quando la perdita di prezzo di
una posizione del crawler supera la soglia (config [guard] CUT_LOSS,
default 125 nella valuta del conto), la posizione viene CHIUSA e RIAPERTA
immediatamente a mercato con stessi direzione/volume/SL/TP/magic.

Il commento della nuova posizione conserva il prezzo di apertura ORIGINALE
del canale e accumula la perdita realizzata (interi, INCLUSI swap e
commissioni dai deal): "@1.3390 (-120)". Il trigger invece considera solo
la perdita di prezzo (position.profit, esclusi swap/commissioni).

Vengono gestite SOLO le posizioni col commento nel formato del crawler:
quelle manuali o di altri sistemi sono ignorate.
"""
import asyncio
import logging
from datetime import datetime, timedelta

from crawler import executor
from crawler.comments import parse_comment, format_loss_comment
from crawler.config import load_config, get_setting

try:
	import MetaTrader5 as mt5
except ImportError:	# pragma: no cover - dipende dalla piattaforma
	mt5 = None

logger = logging.getLogger(__name__)


def _guard_setting(key, default):
	return get_setting(load_config(), 'guard', key, default=default)


async def _alert(client, text):
	"""Notifica nei Saved Messages di Telegram (best effort)."""
	try:
		await client.send_message('me', f"⚠️ Guardia posizioni\n{text}")
	except Exception:
		logger.exception("Impossibile inviare l'alert Telegram della guardia.")


async def _realized_loss(ticket):
	"""
	P/L realizzato della posizione chiusa dai suoi deal in history:
	somma di profit + swap + commission (include i costi di apertura e
	chiusura). Il deal di chiusura può impiegare qualche istante ad
	apparire in history: breve polling.
	"""
	date_from = datetime(2020, 1, 1)
	date_to = datetime.now() + timedelta(days=1)
	for _ in range(10):
		deals = mt5.history_deals_get(date_from, date_to, position=ticket)
		if deals and len(deals) >= 2:	# deal di apertura + almeno uno di chiusura
			return sum(d.profit + d.swap + getattr(d, 'commission', 0.0) for d in deals)
		await asyncio.sleep(0.3)
	logger.warning(f"Guardia: deal di chiusura non ancora in history per {ticket}, perdita stimata dai deal disponibili.")
	deals = mt5.history_deals_get(date_from, date_to, position=ticket) or ()
	return sum(d.profit + d.swap + getattr(d, 'commission', 0.0) for d in deals)


async def _cut_and_reopen(client, pos, parsed, cut_loss):
	price_str, prev_loss = parsed
	logger.info(
		f"Guardia: {pos.symbol} ticket {pos.ticket} in perdita {pos.profit:.2f} "
		f"(soglia -{cut_loss:.0f}): taglio e riapertura."
	)

	close_signal = {
		'order_id': str(pos.ticket), 'magic_number': '', 'message_type': 'close',
		'signal_type': '', 'asset': pos.symbol, 'entry': 0.0, 'sl': '', 'tp': '', 'comment': ''
	}
	closed = executor.execute(close_signal)
	if not closed.ok:
		await _alert(client, f"Taglio FALLITO su {pos.symbol} ticket {pos.ticket}: {closed.message}")
		return

	# Perdita realizzata (con swap e commissioni), cumulata con i tagli precedenti
	realized = await _realized_loss(pos.ticket)
	loss_amount = max(0, round(-realized))
	cumulative = prev_loss + loss_amount
	comment = format_loss_comment(price_str, cumulative)

	reopened = executor.open_market(pos.symbol, pos.type, pos.volume,
	                                pos.sl, pos.tp, pos.magic, comment)
	if reopened.ok:
		logger.info(
			f"Guardia: riaperta {pos.symbol} {pos.volume} lotti (ticket {reopened.ticket}), "
			f"perdita realizzata {loss_amount}, commento '{comment}'."
		)
	else:
		await _alert(client,
		             f"POSIZIONE SCOPERTA: {pos.symbol} tagliata (ticket {pos.ticket}) ma "
		             f"riapertura fallita: {reopened.message}. Riaprire a mano "
		             f"{pos.volume} lotti {'BUY' if pos.type == 0 else 'SELL'} "
		             f"con SL {pos.sl} TP {pos.tp}, commento '{comment}'.")


async def check_positions_once(client):
	"""Un passaggio della guardia su tutte le posizioni aperte."""
	cut_loss = float(_guard_setting('CUT_LOSS', '125') or 125)

	for pos in (mt5.positions_get() or ()):
		parsed = parse_comment(getattr(pos, 'comment', ''))
		if parsed is None:
			continue	# posizione manuale o di altri sistemi: non toccarla
		# Trigger sulla sola perdita di prezzo (esclusi swap/commissioni)
		if pos.profit > -cut_loss:
			continue
		await _cut_and_reopen(client, pos, parsed, cut_loss)


async def run_guard(client):
	"""Loop della guardia: un check ogni INTERVAL_SECONDS, robusto agli errori."""
	enabled = _guard_setting('ENABLED', 'true').lower()
	if enabled not in ('true', '1', 'yes', 'si', 'sì'):
		logger.info("Guardia posizioni disabilitata da config ([guard] ENABLED).")
		return

	interval = float(_guard_setting('INTERVAL_SECONDS', '15') or 15)
	cut_loss = _guard_setting('CUT_LOSS', '125')
	logger.info(f"Guardia posizioni attiva: taglio a -{cut_loss}, check ogni {interval:.0f}s.")

	while True:
		try:
			await check_positions_once(client)
		except asyncio.CancelledError:
			raise
		except Exception:
			logger.exception("Errore nel ciclo della guardia posizioni: continuo.")
		await asyncio.sleep(interval)
