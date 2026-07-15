"""
Guardia delle posizioni aperte in perdita: quando la perdita di prezzo di
una posizione del crawler supera la soglia (config [guard] CUT_LOSS,
default 125 nella valuta del conto), la posizione viene CHIUSA e RIAPERTA
immediatamente a mercato con stessi direzione/volume/SL/TP/magic.

Il commento della nuova posizione conserva il prezzo di apertura ORIGINALE
del canale e accumula la perdita realizzata (interi, INCLUSI swap e
commissioni dai deal): "@1.3390 (-120)". Il trigger invece considera solo
la perdita di prezzo (position.profit, esclusi swap/commissioni).

Oltre alle posizioni col commento nel formato del crawler, la guardia
ADOTTA quelle legacy del vecchio executor (commento 'placement'/'open')
e quelle senza commento (es. aperte a mano): al primo taglio il loro
prezzo di apertura reale diventa il prezzo del commento e la riaperta
nasce già nel formato nuovo. Le posizioni con commenti di altri sistemi
sono ignorate.

Anti-churn: una posizione appena aperta parte già in perdita dello
spread e, quando lo spread è largo (volatilità, notizie), tagliarla
innescherebbe un ciclo di chiusure/riaperture che paga lo spread a ogni
giro. Tre protezioni ([guard] in config): le posizioni più giovani di
MIN_AGE_SECONDS non vengono toccate; dopo un taglio il SIMBOLO è in
cooldown per COOLDOWN_SECONDS (il ticket cambia a ogni riaperta); il
taglio è rinviato finché CUT_LOSS non supera SPREAD_FACTOR volte il
costo corrente dello spread. Età e cooldown sono confrontati in tempo
SERVER (pos.time e tick.time), mai con l'orologio locale.

Blackout notizie (NEWS_BLACKOUT): i tagli sono sospesi nella finestra di
±NEWS_BLACKOUT_MINUTES attorno agli eventi ad alto impatto sulle valute
del simbolo (calendario gratuito di Forex Factory, vedi news_calendar).
Ferma SOLO i tagli della guardia: le aperture da segnale del canale non
vengono mai bloccate.
"""
import asyncio
import logging

from crawler import executor, news_calendar
from crawler.comments import parse_comment, format_loss_comment, format_price_comment
from crawler.config import load_config, get_setting

try:
	import MetaTrader5 as mt5
except ImportError:	# pragma: no cover - dipende dalla piattaforma
	mt5 = None

logger = logging.getLogger(__name__)

DEAL_ENTRY_OUT = 1	# ENUM_DEAL_ENTRY: deal di uscita (chiusura della posizione)

# Commenti scritti dal vecchio executor (pre-commenti "@prezzo")
LEGACY_COMMENTS = frozenset({'placement', 'open'})

# Ultimo taglio per simbolo, in tempo server (epoch del broker)
_last_cut_at = {}


_TRUE_VALUES = ('true', '1', 'yes', 'si', 'sì')


def _guard_setting(key, default):
	return get_setting(load_config(), 'guard', key, default=default)


def _guard_flag(key, default):
	return _guard_setting(key, default).lower() in _TRUE_VALUES


async def _alert(client, text):
	"""Notifica nei Saved Messages di Telegram (best effort)."""
	try:
		await client.send_message('me', f"⚠️ Guardia posizioni\n{text}")
	except Exception:
		logger.exception("Impossibile inviare l'alert Telegram della guardia.")


async def _realized_loss(ticket, estimate):
	"""
	P/L realizzato della posizione chiusa dai suoi deal in history:
	somma di profit + swap + commission (include i costi di apertura e
	chiusura). history_deals_get va chiamata SENZA date: passandole
	insieme a position= il package ignora il filtro e restituisce tutti
	i deal del conto (deposito incluso). Il deal di chiusura può
	impiegare qualche istante ad apparire in history: breve polling;
	se non arriva, fallback sulla stima catturata al momento del taglio.
	"""
	for _ in range(10):
		deals = [d for d in (mt5.history_deals_get(position=ticket) or ())
		         if getattr(d, 'position_id', None) == ticket]
		if any(getattr(d, 'entry', None) == DEAL_ENTRY_OUT for d in deals):
			return sum(d.profit + d.swap + getattr(d, 'commission', 0.0) for d in deals)
		await asyncio.sleep(0.3)
	logger.warning(
		f"Guardia: deal di chiusura non in history per {ticket}, "
		f"perdita stimata dalla posizione al momento del taglio ({estimate:.2f})."
	)
	return estimate


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

	# Perdita realizzata (con swap e commissioni), cumulata con i tagli
	# precedenti; la stima dalla posizione è il fallback se la history tarda
	realized = await _realized_loss(pos.ticket, pos.profit + getattr(pos, 'swap', 0.0))
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


def _parse_or_adopt(pos):
	"""
	(price_str, perdita_cumulata) della posizione, adottandola se non ha
	ancora un commento del crawler: per le legacy del vecchio executor
	('placement'/'open') e per quelle senza commento il prezzo di apertura
	reale diventa il prezzo del commento (con la riaperta la posizione
	migra al formato "@prezzo"). None per i commenti di altri sistemi.
	"""
	comment = (getattr(pos, 'comment', '') or '').strip()
	parsed = parse_comment(comment)
	if parsed is not None:
		return parsed
	if comment and comment.lower() not in LEGACY_COMMENTS:
		return None	# posizione di un altro sistema: non toccarla
	logger.info(
		f"Guardia: adotto {pos.symbol} ticket {pos.ticket} "
		f"(commento '{comment}') al prezzo di apertura {pos.price_open}."
	)
	return parse_comment(format_price_comment(pos.symbol, pos.price_open))


def _spread_cost(tick, sym_info, volume):
	"""Costo dello spread in valuta del conto per il volume dato (0 se non calcolabile)."""
	tick_size = getattr(sym_info, 'trade_tick_size', 0) or 0
	tick_value = getattr(sym_info, 'trade_tick_value', 0) or 0
	if tick_size <= 0 or tick_value <= 0:
		return 0.0
	return (tick.ask - tick.bid) / tick_size * tick_value * volume


async def check_positions_once(client):
	"""Un passaggio della guardia su tutte le posizioni aperte."""
	cut_loss = float(_guard_setting('CUT_LOSS', '125') or 125)
	min_age = float(_guard_setting('MIN_AGE_SECONDS', '60') or 0)
	cooldown = float(_guard_setting('COOLDOWN_SECONDS', '300') or 0)
	spread_factor = float(_guard_setting('SPREAD_FACTOR', '2') or 0)
	news_blackout = _guard_flag('NEWS_BLACKOUT', 'true')
	news_minutes = float(_guard_setting('NEWS_BLACKOUT_MINUTES', '30') or 0)

	for pos in (mt5.positions_get() or ()):
		# Trigger sulla sola perdita di prezzo (esclusi swap/commissioni)
		if pos.profit > -cut_loss:
			continue
		tick = mt5.symbol_info_tick(pos.symbol)
		if tick is None:
			logger.warning(f"Guardia: nessun tick per {pos.symbol}, taglio rinviato.")
			continue
		# Anti-churn: età minima e cooldown per simbolo, in tempo server
		if tick.time - getattr(pos, 'time', 0) < min_age:
			continue
		if tick.time - _last_cut_at.get(pos.symbol, 0) < cooldown:
			continue
		parsed = _parse_or_adopt(pos)
		if parsed is None:
			continue
		# Anti-churn: con la soglia troppo vicina al costo dello spread la
		# riaperta rientrerebbe subito in zona taglio: rinvia finché rientra
		cost = _spread_cost(tick, mt5.symbol_info(pos.symbol), pos.volume)
		if cost > 0 and cut_loss <= spread_factor * cost:
			logger.warning(
				f"Guardia: spread su {pos.symbol} costa {cost:.2f} e la soglia "
				f"{cut_loss:.0f} non supera {spread_factor:g}x: taglio rinviato."
			)
			continue
		# Blackout notizie: niente tagli a ridosso degli eventi ad alto
		# impatto sulle valute del simbolo (spread e volatilità anomali)
		if news_blackout:
			event = news_calendar.in_blackout(pos.symbol, news_minutes)
			if event is not None:
				logger.warning(
					f"Guardia: blackout notizie su {pos.symbol} "
					f"({event['country']} {event['title']}): taglio rinviato."
				)
				continue
		await _cut_and_reopen(client, pos, parsed, cut_loss)
		_last_cut_at[pos.symbol] = tick.time


async def run_guard(client, news_cache_path=None):
	"""Loop della guardia: un check ogni INTERVAL_SECONDS, robusto agli errori."""
	if not _guard_flag('ENABLED', 'true'):
		logger.info("Guardia posizioni disabilitata da config ([guard] ENABLED).")
		return

	interval = float(_guard_setting('INTERVAL_SECONDS', '15') or 15)
	cut_loss = _guard_setting('CUT_LOSS', '125')
	logger.info(f"Guardia posizioni attiva: taglio a -{cut_loss}, check ogni {interval:.0f}s.")

	news_enabled = _guard_flag('NEWS_BLACKOUT', 'true') and news_cache_path is not None
	refresh_hours = float(_guard_setting('NEWS_REFRESH_HOURS', '6') or 6)

	while True:
		try:
			if news_enabled:
				# Bloccante (rete/disco): fuori dall'event loop. Internamente
				# è un no-op finché la cache in memoria è fresca.
				await asyncio.to_thread(news_calendar.refresh, news_cache_path, refresh_hours)
			await check_positions_once(client)
		except asyncio.CancelledError:
			raise
		except Exception:
			logger.exception("Errore nel ciclo della guardia posizioni: continuo.")
		await asyncio.sleep(interval)
