import json
from datetime import datetime, timedelta, timezone

import pytest

from crawler import news_calendar
from crawler.news_calendar import in_blackout, refresh

NOW = datetime(2030, 7, 16, 12, 0, tzinfo=timezone.utc)
NY = timezone(timedelta(hours=-4))  # offset del feed (US/Eastern)


def feed_event(country="USD", offset_minutes=0, impact="High", title="CPI m/m"):
    """Evento nel formato grezzo del feed, con data in fuso non-UTC."""
    when = (NOW + timedelta(minutes=offset_minutes)).astimezone(NY)
    return {"title": title, "country": country, "date": when.isoformat(),
            "impact": impact, "forecast": "0.2%", "previous": "0.2%"}


@pytest.fixture(autouse=True)
def reset_state():
    news_calendar._events = []
    news_calendar._next_refresh = None
    news_calendar._warned_no_data = False
    yield
    news_calendar._events = []
    news_calendar._next_refresh = None


def load_events(raw_events):
    news_calendar._events = news_calendar._parse_events(raw_events)


# ----- parsing -----

def test_only_high_impact_events_are_kept():
    load_events([feed_event(impact="High"), feed_event(impact="Medium"),
                 feed_event(impact="Low"), feed_event(impact="Holiday")])
    assert len(news_calendar._events) == 1


def test_dates_are_converted_to_utc():
    load_events([feed_event(offset_minutes=0)])
    assert news_calendar._events[0]['when'] == NOW


def test_malformed_event_is_ignored():
    load_events([{"title": "rotto"}, {"impact": "High", "country": "USD", "date": "boh"},
                 feed_event()])
    assert len(news_calendar._events) == 1


# ----- in_blackout: globale, qualunque valuta -----

def test_event_inside_window_blacks_out():
    load_events([feed_event(offset_minutes=20)])
    assert in_blackout(30, now=NOW) is not None


def test_event_outside_window_does_not_black_out():
    load_events([feed_event(offset_minutes=45)])
    assert in_blackout(30, now=NOW) is None


def test_event_in_the_past_still_blacks_out_within_window():
    load_events([feed_event(offset_minutes=-25)])
    assert in_blackout(30, now=NOW) is not None


def test_no_events_no_blackout():
    assert in_blackout(30, now=NOW) is None


# ----- refresh: cache, fetch, fail-open -----

def write_cache(path, events, fetched_at):
    path.write_text(json.dumps({"fetched_at": fetched_at.isoformat(),
                                "events": events}), encoding="utf-8")


def test_fresh_cache_is_used_without_fetching(tmp_path, monkeypatch):
    cache = tmp_path / "news_calendar.json"
    write_cache(cache, [feed_event()], datetime.now(timezone.utc))
    monkeypatch.setattr(news_calendar, '_fetch_feed',
                        lambda: pytest.fail("non deve scaricare con cache fresca"))
    refresh(str(cache))
    assert len(news_calendar._events) == 1


def test_stale_cache_triggers_fetch_and_caches_only_high(tmp_path, monkeypatch):
    cache = tmp_path / "news_calendar.json"
    write_cache(cache, [], datetime.now(timezone.utc) - timedelta(hours=7))
    monkeypatch.setattr(news_calendar, '_fetch_feed',
                        lambda: [feed_event(), feed_event(impact="Low")])
    refresh(str(cache))
    assert len(news_calendar._events) == 1
    saved = json.loads(cache.read_text(encoding="utf-8"))
    assert len(saved['events']) == 1  # in cache SOLO gli eventi High


def test_failed_fetch_falls_back_to_stale_cache(tmp_path, monkeypatch):
    cache = tmp_path / "news_calendar.json"
    write_cache(cache, [feed_event()], datetime.now(timezone.utc) - timedelta(hours=7))

    def boom():
        raise OSError("rete giu'")
    monkeypatch.setattr(news_calendar, '_fetch_feed', boom)
    refresh(str(cache))
    assert len(news_calendar._events) == 1  # fail-open sulla cache scaduta


def test_failed_fetch_without_cache_disables_blackout(tmp_path, monkeypatch):
    def boom():
        raise OSError("rete giu'")
    monkeypatch.setattr(news_calendar, '_fetch_feed', boom)
    refresh(str(tmp_path / "news_calendar.json"))
    assert news_calendar._events == []
    assert in_blackout(30) is None


def test_refresh_is_throttled_between_calls(tmp_path, monkeypatch):
    calls = []

    def counting_fetch():
        calls.append(1)
        return [feed_event()]
    monkeypatch.setattr(news_calendar, '_fetch_feed', counting_fetch)
    cache = str(tmp_path / "news_calendar.json")
    refresh(cache)
    refresh(cache)  # subito dopo: no-op in memoria
    assert len(calls) == 1
