"""
Calendario economico per il blackout dei tagli della guardia attorno
alle notizie ad alto impatto.

La fonte è il feed settimanale gratuito di Forex Factory (FairEconomy),
senza API key: ff_calendar_thisweek.json. Si considerano solo gli eventi
con impact "High" (equivalenti ai "3 tori" di Investing.com). Il feed
ammette al massimo 2 download ogni 5 minuti: viene scaricato solo quando
la cache su disco (news_calendar.json, accanto al config) è più vecchia
di NEWS_REFRESH_HOURS; a feed irraggiungibile si riprova dopo un quarto
d'ora.

Fail-open: se il feed non risponde si usa la cache anche scaduta; senza
nessuna cache il blackout resta semplicemente inattivo (warning nel log)
— la guardia non viene mai bloccata per sempre da dati mancanti.

Le date del feed sono ISO-8601 con offset: tutti i confronti avvengono
in UTC, senza dipendere dal fuso locale o da quello del server MT5.
"""
import json
import logging
import re
import urllib.request
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

FEED_URL = 'https://nfs.faireconomy.media/ff_calendar_thisweek.json'
CACHE_FILENAME = 'news_calendar.json'
RETRY_MINUTES = 15	# attesa dopo un fetch fallito prima di riprovare

# Eventi High della settimana: dict {'title', 'country', 'when' (UTC)}
_events = []
_next_refresh = None	# prossimo istante (UTC) in cui vale la pena ritentare
_warned_no_data = False

# Simbolo FX "AAABBB" con eventuale suffisso broker (es. EURUSD.m)
_FX_SYMBOL_RE = re.compile(r'^([A-Z]{3})([A-Z]{3})($|[^A-Z])')


def _parse_events(raw_events):
	events = []
	for ev in raw_events or ():
		try:
			if ev.get('impact') != 'High':
				continue
			when = datetime.fromisoformat(ev['date']).astimezone(timezone.utc)
			events.append({'title': ev.get('title', '?'), 'country': ev['country'], 'when': when})
		except (KeyError, ValueError, TypeError):
			logger.warning(f"Evento del calendario notizie non interpretabile, ignorato: {ev!r}")
	return events


def _load_cache(path):
	try:
		with open(path, 'r', encoding='utf-8') as f:
			cache = json.load(f)
		return cache if isinstance(cache, dict) else None
	except (OSError, ValueError):
		return None


def _save_cache(cache, path):
	try:
		with open(path, 'w', encoding='utf-8') as f:
			json.dump(cache, f)
	except OSError:
		logger.exception(f"Impossibile salvare la cache del calendario notizie su {path}.")


def _fetch_feed():
	# Lo User-Agent di default di urllib viene rifiutato dal feed (403)
	req = urllib.request.Request(FEED_URL, headers={'User-Agent': 'Mozilla/5.0 (signals-crawler)'})
	with urllib.request.urlopen(req, timeout=30) as resp:
		return json.loads(resp.read().decode('utf-8'))


def refresh(cache_path, refresh_hours):
	"""
	Aggiorna gli eventi in memoria: dalla cache su disco se fresca,
	altrimenti dal feed (persistendo la nuova cache). Chiamata bloccante
	(rete/disco): va eseguita fuori dall'event loop (asyncio.to_thread).
	"""
	global _events, _next_refresh, _warned_no_data
	now = datetime.now(timezone.utc)
	if _next_refresh is not None and now < _next_refresh:
		return

	cache = _load_cache(cache_path)
	if cache:
		try:
			fetched_at = datetime.fromisoformat(cache['fetched_at'])
			if now - fetched_at < timedelta(hours=float(refresh_hours)):
				_events = _parse_events(cache.get('events'))
				_next_refresh = fetched_at + timedelta(hours=float(refresh_hours))
				return
		except (KeyError, ValueError, TypeError):
			cache = None	# cache malformata: si riscarica

	try:
		raw = _fetch_feed()
	except Exception:
		_next_refresh = now + timedelta(minutes=RETRY_MINUTES)
		if cache:
			logger.warning("Feed del calendario notizie non raggiungibile: uso la cache scaduta.")
			_events = _parse_events(cache.get('events'))
		elif not _warned_no_data:
			logger.warning("Feed del calendario notizie non raggiungibile e nessuna cache: blackout notizie inattivo.")
			_warned_no_data = True
		return

	_save_cache({'fetched_at': now.isoformat(), 'events': raw}, cache_path)
	_events = _parse_events(raw)
	_next_refresh = now + timedelta(hours=float(refresh_hours))
	logger.info(f"Calendario notizie aggiornato: {len(_events)} eventi ad alto impatto in settimana.")


def in_blackout(symbol, minutes, now=None):
	"""
	L'evento ad alto impatto in finestra ±minutes per una delle due valute
	del simbolo FX, o None. I simboli non-FX (nome non "AAABBB": indici,
	metalli, crypto) non matchano mai: nessun blackout.
	"""
	match = _FX_SYMBOL_RE.match(symbol.upper())
	if not match:
		return None
	currencies = {match.group(1), match.group(2)}
	now = now or datetime.now(timezone.utc)
	window = timedelta(minutes=float(minutes))
	for ev in _events:
		if ev['country'] in currencies and abs(ev['when'] - now) <= window:
			return ev
	return None
