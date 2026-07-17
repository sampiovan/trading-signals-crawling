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
    calls = SimpleNamespace(executed=[], lookups=[], signals=[], live_ticket=None)

    monkeypatch.setattr(crawler_main, 'parse_message',
                        lambda text, reply_text=None: calls.signals)

    def fake_execute(sig):
        calls.executed.append(sig)
        return SimpleNamespace(ok=True, ticket=777, retcode=10009, message="ok")
    monkeypatch.setattr(crawler_main.executor, 'execute', fake_execute)

    def fake_lookup(asset, entry, signal_type):
        calls.lookups.append((asset, entry, signal_type))
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
    def raise_not_found(text, reply_text=None):
        raise crawler_main.OrderNotFoundException(
            "Order ID non trovato per segnale close: asset=GPSUSD, entry=1.34946")
    monkeypatch.setattr(crawler_main, 'parse_message', raise_not_found)

    client = FakeClient()
    asyncio.run(crawler_main.process_message(client, message(), pipeline.state_path))

    assert client.alerts and "GPSUSD" in client.alerts[0][1]
    assert pipeline.executed == []
    from crawler.crawler_state import load_last_message_id
    assert load_last_message_id(path=pipeline.state_path) == 100  # lo stato avanza


def test_catchup_close_is_never_deduplicated(pipeline):
    # close/modify/move_sl sono già innocui al replay: nessun lookup
    pipeline.signals = [signal("close", signal_type="", order_id="444")]
    pipeline.live_ticket = "555"
    process(pipeline, catching_up=True)
    assert len(pipeline.executed) == 1
    assert pipeline.lookups == []
