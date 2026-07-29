import asyncio
from types import SimpleNamespace

import pytest

from crawler import main as crawler_main


def signal(message_type="placement", signal_type="BUY LIMIT", asset="EURUSD",
           entry=1.3390, order_id=""):
    return {"order_id": order_id, "magic_number": "12345",
            "message_type": message_type, "signal_type": signal_type,
            "asset": asset, "entry": entry, "sl": "", "tp": "", "comment": ""}


def message(msg_id=100, text="segnale"):
    async def no_reply():
        return None
    return SimpleNamespace(id=msg_id, raw_text=text, get_reply_message=no_reply)


class FakeClient:
    def __init__(self):
        self.alerts = []

    async def send_message(self, target, text):
        self.alerts.append((target, text))


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    """Pipeline con parser, executor e lookup finti; ritorna i registri delle chiamate."""
    calls = SimpleNamespace(executed=[], lookups=[], signals=[], live_ticket=None,
                            skips=[], fallback_flags=[])

    def fake_parse(text, reply_text=None, on_skip=None):
        for detail in calls.skips:
            on_skip(detail)
        return calls.signals
    monkeypatch.setattr(crawler_main, 'parse_message', fake_parse)

    def fake_execute(sig):
        calls.executed.append(sig)
        return SimpleNamespace(ok=True, ticket=777, retcode=10009, message="ok")
    monkeypatch.setattr(crawler_main.executor, 'execute', fake_execute)

    def fake_lookup(asset, entry, signal_type, allow_comment_fallback=True):
        calls.lookups.append((asset, entry, signal_type))
        calls.fallback_flags.append(allow_comment_fallback)
        return (calls.live_ticket, "12345") if calls.live_ticket else (None, None)
    monkeypatch.setattr(crawler_main.order_lookup, 'get_order_ticket', fake_lookup)

    calls.state_path = str(tmp_path / "crawler_state.json")
    return calls


def process(calls, catching_up):
    asyncio.run(crawler_main.process_message(
        FakeClient(), message(), calls.state_path, catching_up=catching_up))


def test_catchup_skips_placement_already_executed(pipeline):
    # Crash dopo l'esecuzione ma prima del salvataggio: al replay l'ordine
    # è già vivo sul conto -> NIENTE doppione, ma lo stato avanza comunque
    pipeline.signals = [signal("placement")]
    pipeline.live_ticket = "555"
    process(pipeline, catching_up=True)
    assert pipeline.executed == []
    assert pipeline.lookups  # il lookup è stato interrogato
    from crawler.crawler_state import load_last_message_id
    assert load_last_message_id(path=pipeline.state_path) == 100


def test_catchup_dedup_never_uses_the_comment_fallback(pipeline):
    # La deduplica chiede "è ESATTAMENTE questo ordine?": il ripiego sul
    # commento ignora l'asset e potrebbe agganciare un'altra coppia con lo
    # stesso prezzo nel commento, facendo saltare un'apertura legittima
    pipeline.signals = [signal("placement")]
    pipeline.live_ticket = None
    process(pipeline, catching_up=True)
    assert pipeline.fallback_flags == [False]


def test_catchup_skip_alerts_telegram(pipeline):
    # Un'apertura non eseguita non deve restare sepolta nel log: se la
    # deduplica sbagliasse, il segnale sarebbe perso in silenzio
    pipeline.signals = [signal("placement")]
    pipeline.live_ticket = "555"

    client = FakeClient()
    asyncio.run(crawler_main.process_message(
        client, message(), pipeline.state_path, catching_up=True))

    assert pipeline.executed == []
    assert client.alerts and "SALTATA" in client.alerts[0][1]


def test_live_message_still_allows_the_fallback(pipeline):
    # Fuori dal catch-up la deduplica non gira affatto: nessun lookup, quindi
    # il fallback resta disponibile a chi cerca l'ordine citato dal canale
    pipeline.signals = [signal("placement")]
    pipeline.live_ticket = "555"
    process(pipeline, catching_up=False)
    assert pipeline.fallback_flags == []
    assert len(pipeline.executed) == 1


def test_catchup_executes_placement_not_yet_live(pipeline):
    pipeline.signals = [signal("placement")]
    pipeline.live_ticket = None
    process(pipeline, catching_up=True)
    assert len(pipeline.executed) == 1


def test_live_message_never_does_the_lookup(pipeline):
    # Fuori dal catch-up il messaggio è nuovo per definizione: si esegue e basta
    pipeline.signals = [signal("placement")]
    pipeline.live_ticket = "555"
    process(pipeline, catching_up=False)
    assert len(pipeline.executed) == 1
    assert pipeline.lookups == []


def test_catchup_open_with_order_id_skips_the_lookup(pipeline):
    # L'open di un pending noto è già idempotente nell'executor
    pipeline.signals = [signal("open", signal_type="BUY", order_id="444")]
    pipeline.live_ticket = "555"
    process(pipeline, catching_up=True)
    assert len(pipeline.executed) == 1
    assert pipeline.lookups == []


def test_catchup_skips_market_open_already_executed(pipeline):
    pipeline.signals = [signal("open", signal_type="BUY", order_id="")]
    pipeline.live_ticket = "555"
    process(pipeline, catching_up=True)
    assert pipeline.executed == []


def test_order_not_found_alerts_telegram(pipeline, monkeypatch):
    # Segnale riconosciuto ma ordine non trovato (es. refuso dell'asset nel
    # canale): lo scarto deve arrivare nei Saved Messages, non solo nel log
    def raise_not_found(text, reply_text=None, on_skip=None):
        raise crawler_main.OrderNotFoundException(
            "Order ID non trovato per segnale close: asset=GPSUSD, entry=1.34946")
    monkeypatch.setattr(crawler_main, 'parse_message', raise_not_found)

    client = FakeClient()
    asyncio.run(crawler_main.process_message(client, message(), pipeline.state_path))

    assert client.alerts and "GPSUSD" in client.alerts[0][1]
    assert pipeline.executed == []
    from crawler.crawler_state import load_last_message_id
    assert load_last_message_id(path=pipeline.state_path) == 100  # lo stato avanza


def test_multi_close_partial_skip_alerts_telegram(pipeline):
    # L'incidente del msg id=373: 2 righe su 3 saltate, ma il messaggio è
    # riuscito in parte -> nessuna eccezione, quindi nessun alert. Ora sì.
    pipeline.signals = [signal("close", signal_type="", order_id="444")]
    pipeline.skips = ["ordine non trovato nel registro per asset=AUDUSD, entry=1.20200. Posizione saltata."]

    client = FakeClient()
    asyncio.run(crawler_main.process_message(client, message(), pipeline.state_path))

    assert client.alerts and "AUDUSD" in client.alerts[0][1]
    assert len(pipeline.executed) == 1  # la riga riuscita viene comunque eseguita


def test_total_failure_does_not_duplicate_skip_alerts(pipeline, monkeypatch):
    # Se falliscono TUTTE, l'eccezione produce già il suo alert: le singole
    # righe che l'hanno composta non vanno ripetute
    def raise_not_found(text, reply_text=None, on_skip=None):
        on_skip("asset=AUDNZD, entry=1.21600. Posizione saltata.")
        raise crawler_main.OrderNotFoundException("Multi-close: nessuna delle 2 posizioni trovata.")
    monkeypatch.setattr(crawler_main, 'parse_message', raise_not_found)

    client = FakeClient()
    asyncio.run(crawler_main.process_message(client, message(), pipeline.state_path))

    assert len(client.alerts) == 1
    assert "SCARTATO" in client.alerts[0][1]


def test_catchup_close_is_never_deduplicated(pipeline):
    # close/modify/move_sl sono già innocui al replay: nessun lookup
    pipeline.signals = [signal("close", signal_type="", order_id="444")]
    pipeline.live_ticket = "555"
    process(pipeline, catching_up=True)
    assert len(pipeline.executed) == 1
    assert pipeline.lookups == []
