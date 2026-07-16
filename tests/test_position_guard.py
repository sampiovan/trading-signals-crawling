import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from crawler import executor, mt5_client, news_calendar, position_guard
from crawler.executor import TRADE_ACTION_DEAL, RETCODE_DONE
from crawler.position_guard import check_positions_once

BUY, SELL = 0, 1


def result(retcode, order=777, comment="done"):
    return SimpleNamespace(retcode=retcode, order=order, comment=comment)


ENTRY_IN, ENTRY_OUT = 0, 1


def deal(profit=0.0, swap=0.0, commission=0.0, entry=ENTRY_OUT, position_id=555):
    return SimpleNamespace(profit=profit, swap=swap, commission=commission,
                           entry=entry, position_id=position_id)


SERVER_NOW = 100_000  # "adesso" in tempo server (epoch del broker) negli stub


def position(ticket=555, symbol="EURUSD", ptype=SELL, profit=-130.0, volume=0.10,
             sl=1.36, tp=1.30, magic=54321, comment="@1.3390", price_open=1.3390,
             swap=0.0, time=SERVER_NOW - 10_000):
    return SimpleNamespace(ticket=ticket, symbol=symbol, type=ptype, profit=profit,
                           volume=volume, sl=sl, tp=tp, magic=magic, comment=comment,
                           price_open=price_open, swap=swap, time=time)


class FakeMT5:
    def __init__(self, positions=(), deals=(), send_results=None, deals_sequence=None,
                 tick=None):
        self._tick = tick or SimpleNamespace(ask=1.20010, bid=1.20000, time=SERVER_NOW)
        self._positions = list(positions)
        # deals_sequence: una tupla di deal per ogni chiamata a history_deals_get
        # (l'ultima si ripete); simula il deal di chiusura che tarda ad apparire.
        self._deals_seq = [list(step) for step in deals_sequence] if deals_sequence else [list(deals)]
        self.send_results = list(send_results if send_results is not None else [result(RETCODE_DONE)] * 4)
        self.sent_requests = []

    def positions_get(self, ticket=None, symbol=None):
        return tuple(p for p in self._positions
                     if (ticket is None or p.ticket == ticket) and (symbol is None or p.symbol == symbol))

    def orders_get(self, ticket=None):
        return ()

    def history_deals_get(self, position=None):
        # Firma senza date: con le date il package ignorerebbe position=
        step = self._deals_seq.pop(0) if len(self._deals_seq) > 1 else self._deals_seq[0]
        return tuple(step)

    def order_send(self, request):
        self.sent_requests.append(request)
        return self.send_results.pop(0) if self.send_results else None

    def symbol_info(self, symbol):
        return SimpleNamespace(filling_mode=2, volume_min=0.01, volume_max=50.0,
                               volume_step=0.01, trade_tick_size=0.00001, trade_tick_value=1.0)

    def symbol_info_tick(self, symbol):
        return self._tick

    def account_info(self):
        return SimpleNamespace(equity=100000.0, balance=100000.0)

    def last_error(self):
        return (-1, "errore finto")


class FakeClient:
    def __init__(self):
        self.alerts = []

    async def send_message(self, target, text):
        self.alerts.append((target, text))


@pytest.fixture(autouse=True)
def wire_stub(monkeypatch):
    """Config con soglia 125, stub condiviso guardia+executor, niente attese."""
    values = {'CUT_LOSS': '125', 'INTERVAL_SECONDS': '15', 'ENABLED': 'true'}
    monkeypatch.setattr(position_guard, 'load_config', lambda: None)
    monkeypatch.setattr(position_guard, 'get_setting',
                        lambda cfg, section, key, default='': values.get(key, default))
    monkeypatch.setattr(executor, 'load_config', lambda: None)
    monkeypatch.setattr(executor, 'get_mt5_setting', lambda cfg, key, default='': default)
    monkeypatch.setattr(executor, 'compute_lot', lambda signal, si, ai: 0.01)
    monkeypatch.setattr(executor.time, 'sleep', lambda s: None)
    monkeypatch.setattr(mt5_client, 'resolve_symbol', lambda asset: asset)

    async def no_sleep(_seconds):
        pass
    monkeypatch.setattr(position_guard.asyncio, 'sleep', no_sleep)
    monkeypatch.setattr(position_guard, '_blackout_active', False)
    monkeypatch.setattr(news_calendar, '_events', [])


def use(monkeypatch, fake):
    monkeypatch.setattr(position_guard, 'mt5', fake)
    monkeypatch.setattr(executor, 'mt5', fake)
    return fake


def run(coro):
    return asyncio.run(coro)


# ----- trigger -----

def test_position_above_threshold_is_left_alone(monkeypatch):
    fake = use(monkeypatch, FakeMT5(positions=[position(profit=-80.0)]))
    run(check_positions_once(FakeClient()))
    assert fake.sent_requests == []


def test_foreign_positions_are_ignored(monkeypatch):
    # Perdita profonda ma commento non nostro: la guardia non la tocca
    fake = use(monkeypatch, FakeMT5(positions=[position(profit=-500.0, comment="trade manuale")]))
    run(check_positions_once(FakeClient()))
    assert fake.sent_requests == []


# ----- anti-churn: età minima, cooldown per simbolo, filtro spread -----

def test_young_position_is_left_alone(monkeypatch):
    # Aperta da 10s (< MIN_AGE_SECONDS=60): parte in perdita dello spread
    # ma la guardia non la tocca — il caso del loop con spread alto
    fake = use(monkeypatch, FakeMT5(
        positions=[position(profit=-130.0, time=SERVER_NOW - 10)]))
    run(check_positions_once(FakeClient()))
    assert fake.sent_requests == []


def test_wide_spread_defers_cut(monkeypatch):
    # Spread 0.01 su 0.10 lotti -> costo 100; CUT_LOSS 125 <= 2x100: rinviato
    fake = use(monkeypatch, FakeMT5(
        positions=[position(profit=-130.0)],
        tick=SimpleNamespace(ask=1.2100, bid=1.2000, time=SERVER_NOW)))
    run(check_positions_once(FakeClient()))
    assert fake.sent_requests == []


def test_unreadable_spread_does_not_block_cut(monkeypatch):
    # trade_tick_size a 0 (broker che non lo espone): il filtro spread si
    # disattiva da solo e il taglio procede come prima
    class NoTickSizeMT5(FakeMT5):
        def symbol_info(self, symbol):
            si = super().symbol_info(symbol)
            si.trade_tick_size = 0.0
            return si

    fake = use(monkeypatch, NoTickSizeMT5(
        positions=[position(profit=-130.0)],
        deals=[deal(entry=ENTRY_IN), deal(profit=-130.0)]))
    run(check_positions_once(FakeClient()))
    assert len(fake.sent_requests) == 2


def test_missing_tick_defers_cut(monkeypatch):
    class NoTickMT5(FakeMT5):
        def symbol_info_tick(self, symbol):
            return None

    fake = use(monkeypatch, NoTickMT5(positions=[position(profit=-130.0)]))
    run(check_positions_once(FakeClient()))
    assert fake.sent_requests == []


def _high_impact_event_now(country="USD"):
    return {'title': 'CPI m/m', 'country': country,
            'when': datetime.now(timezone.utc)}


def test_news_blackout_suspends_guard_on_all_assets(monkeypatch):
    # Notizia High su una valuta QUALSIASI: la guardia si ferma del tutto
    monkeypatch.setattr(news_calendar, '_events', [_high_impact_event_now("CAD")])
    fake = use(monkeypatch, FakeMT5(
        positions=[position(symbol="EURUSD", profit=-130.0),
                   position(ticket=556, symbol="GBPUSD", profit=-500.0)],
        deals=[deal(entry=ENTRY_IN), deal(profit=-130.0)]))
    run(check_positions_once(FakeClient()))
    assert fake.sent_requests == []


def test_news_blackout_disabled_cuts(monkeypatch):
    values = {'CUT_LOSS': '125', 'NEWS_BLACKOUT': 'false'}
    monkeypatch.setattr(position_guard, 'get_setting',
                        lambda cfg, section, key, default='': values.get(key, default))
    monkeypatch.setattr(news_calendar, '_events', [_high_impact_event_now("USD")])
    fake = use(monkeypatch, FakeMT5(
        positions=[position(profit=-130.0)],
        deals=[deal(entry=ENTRY_IN), deal(profit=-130.0)]))
    run(check_positions_once(FakeClient()))
    assert len(fake.sent_requests) == 2


# ----- adozione delle posizioni senza commento del crawler -----

@pytest.mark.parametrize("comment,magic", [
    ("placement", 54321),   # legacy del vecchio executor
    ("open", 54321),        # legacy del vecchio executor
    ("", 0),                # manuale senza commento
])
def test_adopts_positions_without_crawler_comment(monkeypatch, comment, magic):
    fake = use(monkeypatch, FakeMT5(
        positions=[position(profit=-130.0, comment=comment, magic=magic, price_open=1.3415)],
        deals=[deal(entry=ENTRY_IN), deal(profit=-130.0)]))
    run(check_positions_once(FakeClient()))
    # Riaperta col prezzo di apertura reale adottato come prezzo del commento
    assert fake.sent_requests[1]['comment'] == '@1.3415 (-130)'
    assert fake.sent_requests[1]['magic'] == magic


def test_adopted_jpy_price_uses_two_decimals(monkeypatch):
    fake = use(monkeypatch, FakeMT5(
        positions=[position(symbol="USDJPY", profit=-130.0, comment="placement",
                            price_open=145.503)],
        deals=[deal(entry=ENTRY_IN), deal(profit=-130.0)]))
    run(check_positions_once(FakeClient()))
    assert fake.sent_requests[1]['comment'] == '@145.50 (-130)'


def test_adoptable_position_above_threshold_is_left_alone(monkeypatch):
    fake = use(monkeypatch, FakeMT5(positions=[position(profit=-80.0, comment="placement")]))
    run(check_positions_once(FakeClient()))
    assert fake.sent_requests == []


# ----- cut & reopen -----

def test_cut_and_reopen_flow(monkeypatch):
    fake = use(monkeypatch, FakeMT5(
        positions=[position(ticket=555, ptype=SELL, profit=-130.0, volume=0.10,
                            sl=1.36, tp=1.30, magic=54321, comment="@1.3390")],
        deals=[deal(commission=-0.7, entry=ENTRY_IN), deal(profit=-127.0, swap=-2.1, commission=-0.7)]))
    client = FakeClient()
    run(check_positions_once(client))

    close_req, reopen_req = fake.sent_requests
    # Chiusura: deal opposto agganciato alla posizione
    assert close_req['action'] == TRADE_ACTION_DEAL and close_req['position'] == 555
    assert close_req['type'] == BUY  # opposto del SELL
    # Riapertura: stessa direzione/volume/SL/TP/magic, commento con perdita
    assert reopen_req['action'] == TRADE_ACTION_DEAL
    assert reopen_req['type'] == SELL
    assert reopen_req['volume'] == 0.10
    assert reopen_req['sl'] == 1.36 and reopen_req['tp'] == 1.30
    assert reopen_req['magic'] == 54321
    # -127.0 - 2.1 - 0.7 - 0.7 = -130.5 -> perdita 130 (round half-to-even)
    assert reopen_req['comment'] == '@1.3390 (-130)'
    assert client.alerts == []


def test_losses_accumulate_across_cuts(monkeypatch):
    fake = use(monkeypatch, FakeMT5(
        positions=[position(profit=-126.0, comment="@1.3390 (-120)")],
        deals=[deal(entry=ENTRY_IN), deal(profit=-125.0, swap=-1.4)]))
    run(check_positions_once(FakeClient()))
    # 120 precedenti + 126 realizzati adesso (125.0+1.4 -> round 126)
    assert fake.sent_requests[1]['comment'] == '@1.3390 (-246)'


def test_realized_loss_ignores_deals_of_other_positions(monkeypatch):
    # Il bug reale: history_deals_get restituiva TUTTI i deal del conto
    # (deposito da +100k incluso) e la perdita veniva azzerata dal max(0, ...)
    fake = use(monkeypatch, FakeMT5(
        positions=[position(ticket=555, profit=-130.0)],
        deals=[deal(profit=100000.0, entry=ENTRY_IN, position_id=0),   # deposito
               deal(profit=42.0, position_id=999),                     # altra posizione
               deal(entry=ENTRY_IN, commission=-0.25),
               deal(profit=-5.5, commission=-0.25)]))
    run(check_positions_once(FakeClient()))
    assert fake.sent_requests[1]['comment'] == '@1.3390 (-6)'


def test_realized_loss_waits_for_exit_deal(monkeypatch):
    # Finché in history c'è solo il deal di apertura la guardia riprova;
    # la somma è quella del passaggio con il deal di uscita
    fake = use(monkeypatch, FakeMT5(
        positions=[position(profit=-130.0)],
        deals_sequence=[[deal(entry=ENTRY_IN)],
                        [deal(entry=ENTRY_IN)],
                        [deal(entry=ENTRY_IN), deal(profit=-130.5)]]))
    run(check_positions_once(FakeClient()))
    assert fake.sent_requests[1]['comment'] == '@1.3390 (-130)'


def test_realized_loss_falls_back_to_position_snapshot(monkeypatch):
    # Il deal di uscita non arriva mai in history: stima da profit+swap
    # catturati al momento del taglio
    fake = use(monkeypatch, FakeMT5(
        positions=[position(profit=-130.0, swap=-2.0)],
        deals=[deal(entry=ENTRY_IN)]))
    run(check_positions_once(FakeClient()))
    assert fake.sent_requests[1]['comment'] == '@1.3390 (-132)'


def test_failed_close_alerts_and_does_not_reopen(monkeypatch):
    fake = use(monkeypatch, FakeMT5(
        positions=[position(profit=-130.0)],
        send_results=[result(10019, comment="no money")]))
    client = FakeClient()
    run(check_positions_once(client))
    assert len(fake.sent_requests) == 1  # solo il tentativo di chiusura
    assert client.alerts and "Taglio FALLITO" in client.alerts[0][1]


def test_failed_reopen_alerts_uncovered_position(monkeypatch):
    fake = use(monkeypatch, FakeMT5(
        positions=[position(profit=-130.0)],
        deals=[deal(entry=ENTRY_IN), deal(profit=-130.0)],
        send_results=[result(RETCODE_DONE), result(10019, comment="no money")]))
    client = FakeClient()
    run(check_positions_once(client))
    assert len(fake.sent_requests) == 2
    assert client.alerts and "POSIZIONE SCOPERTA" in client.alerts[0][1]


def test_run_guard_disabled_exits_immediately(monkeypatch):
    values = {'ENABLED': 'false'}
    monkeypatch.setattr(position_guard, 'get_setting',
                        lambda cfg, section, key, default='': values.get(key, default))
    # Se non uscisse subito, asyncio.run non terminerebbe (loop infinito)
    run(position_guard.run_guard(FakeClient()))
