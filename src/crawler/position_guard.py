"""
Guardia delle posizioni aperte in perdita: quando la perdita di prezzo di
una posizione del crawler supera la soglia di taglio, la posizione viene
CHIUSA e RIAPERTA immediatamente a mercato con stessi direzione/volume/
SL/TP/magic. La soglia è una frazione della perdita giornaliera consentita
(config [guard] CUT_LOSS_PERCENT, default 2.5): il budget giornaliero è
DAILY_LOSS_PERCENT (config [risk], default 5 — regola FTMO) del deposito
iniziale, quindi resta FISSO in valuta anche quando il balance cresce e
ogni taglio consuma una frazione nota e costante del budget.

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
giro. Due protezioni ([guard] in config): le posizioni più giovani di
MIN_AGE_SECONDS non vengono toccate (ogni riaperta è una posizione
nuova, quindi l'età minima fa anche da pausa tra un taglio e l'altro) e
il taglio è rinviato finché la soglia non supera SPREAD_FACTOR volte il
costo corrente dello spread. L'età è confrontata in tempo SERVER
(pos.time e tick.time), mai con l'orologio locale.

Blackout notizie (NEWS_BLACKOUT): la guardia è sospesa DEL TUTTO, su
tutti gli asset, nella finestra di ±NEWS_BLACKOUT_MINUTES attorno a
qualunque notizia ad alto impatto (calendario gratuito di Forex Factory,
vedi news_calendar). Ferma SOLO i tagli della guardia: la pipeline dei
messaggi Telegram (parsing ed esecuzione dei segnali) non è toccata.
"""
import asyncio
import logging

from crawler import executor, news_calendar, risk
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

# Per loggare il blackout solo alle transizioni (non a ogni passata)
_blackout_active = False


_TRUE_VALUES = ('true', '1', 'yes', 'si', 'sì')


def _guard_setting(key, default):
	return get_setting(load_config(), 'guard', key, default=default)


def _guard_flag(key, default):
	return _guard_setting(key, default).lower() in _TRUE_VALUES


def _cut_loss_percent():
	return float(_guard_setting('CUT_LOSS_PERCENT', '2.5') or 2.5)


def _cut_loss_threshold():
	"""
	Soglia di taglio in valuta del conto: CUT_LOSS_PERCENT % della perdita
	giornaliera consentita (risk.daily_loss_budget). None se il deposito
	iniziale non è ancora noto.
	"""
	budget = risk.daily_loss_budget()
	if budget is None:
		return None
	return budget * _cut_loss_percent() / 100


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


def _in_news_blackout():
	"""True se la guardia è sospesa per una notizia ad alto impatto (logga le transizioni)."""
	global _blackout_active
	event = None
	if _guard_flag('NEWS_BLACKOUT', 'true'):
		minutes = float(_guard_setting('NEWS_BLACKOUT_MINUTES', '30') or 0)
		event = news_calendar.in_blackout(minutes)
	if event is not None and not _blackout_active:
		logger.info(f"Guardia in blackout notizie ({event['country']} {event['title']}): tagli sospesi.")
	elif event is None and _blackout_active:
		logger.info("Guardia riattivata dopo il blackout notizie.")
	_blackout_active = event is not None
	return _blackout_active


async def check_positions_once(client):
	"""Un passaggio della guardia su tutte le posizioni aperte."""
	if _in_news_blackout():
		return

	cut_loss = _cut_loss_threshold()
	if cut_loss is None:
		logger.warning("Guardia: deposito iniziale non disponibile, soglia non calcolabile: passata saltata.")
		return
	min_age = float(_guard_setting('MIN_AGE_SECONDS', '60') or 0)
	spread_factor = float(_guard_setting('SPREAD_FACTOR', '2') or 0)

	for pos in (mt5.positions_get() or ()):
		# Trigger sulla sola perdita di prezzo (esclusi swap/commissioni)
		if pos.profit > -cut_loss:
			continue
		tick = mt5.symbol_info_tick(pos.symbol)
		if tick is None:
			logger.warning(f"Guardia: nessun tick per {pos.symbol}, taglio rinviato.")
			continue
		# Anti-churn: età minima in tempo server (una riaperta è una
		# posizione nuova: fa anche da pausa tra un taglio e l'altro)
		if tick.time - getattr(pos, 'time', 0) < min_age:
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
		await _cut_and_reopen(client, pos, parsed, cut_loss)


async def run_guard(client, news_cache_path=None):
	"""Loop della guardia: un check ogni INTERVAL_SECONDS, robusto agli errori."""
	if not _guard_flag('ENABLED', 'true'):
		logger.info("Guardia posizioni disabilitata da config ([guard] ENABLED).")
		return

	interval = float(_guard_setting('INTERVAL_SECONDS', '15') or 15)
	cut_loss = _cut_loss_threshold()
	threshold = f"-{cut_loss:.0f}" if cut_loss is not None else "deposito non ancora noto"
	cut_percent = _cut_loss_percent()
	logger.info(
		f"Guardia posizioni attiva: taglio al {cut_percent:g}% "
		f"del budget giornaliero ({threshold}), check ogni {interval:.0f}s."
	)

	news_enabled = _guard_flag('NEWS_BLACKOUT', 'true') and news_cache_path is not None

	while True:
		try:
			if news_enabled:
				# Bloccante (rete/disco): fuori dall'event loop. Internamente
				# è un no-op finché la cache in memoria è fresca.
				await asyncio.to_thread(news_calendar.refresh, news_cache_path)
			await check_positions_once(client)
		except asyncio.CancelledError:
			raise
		except Exception:
			logger.exception("Errore nel ciclo della guardia posizioni: continuo.")
		await asyncio.sleep(interval)
