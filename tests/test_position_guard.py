import asyncio
from types import SimpleNamespace

import pytest

from crawler import executor, mt5_client, position_guard
from crawler.executor import TRADE_ACTION_DEAL, RETCODE_DONE
from crawler.position_guard import check_positions_once

BUY, SELL = 0, 1


def result(retcode, order=777, comment="done"):
    return SimpleNamespace(retcode=retcode, order=order, comment=comment)


def deal(profit=0.0, swap=0.0, commission=0.0):
    return SimpleNamespace(profit=profit, swap=swap, commission=commission)


def position(ticket=555, symbol="EURUSD", ptype=SELL, profit=-130.0, volume=0.10,
             sl=1.36, tp=1.30, magic=54321, comment="@1.3390"):
    return SimpleNamespace(ticket=ticket, symbol=symbol, type=ptype, profit=profit,
                           volume=volume, sl=sl, tp=tp, magic=magic, comment=comment)


class FakeMT5:
    def __init__(self, positions=(), deals=(), send_results=None):
        self._positions = list(positions)
        self._deals = list(deals)
        self.send_results = list(send_results if send_results is not None else [result(RETCODE_DONE)] * 4)
        self.sent_requests = []

    def positions_get(self, ticket=None, symbol=None):
        return tuple(p for p in self._positions
                     if (ticket is None or p.ticket == ticket) and (symbol is None or p.symbol == symbol))

    def orders_get(self, ticket=None):
        return ()

    def history_deals_get(self, date_from, date_to, position=None):
        return tuple(self._deals)

    def order_send(self, request):
        self.sent_requests.append(request)
        return self.send_results.pop(0) if self.send_results else None

    def symbol_info(self, symbol):
        return SimpleNamespace(filling_mode=2, volume_min=0.01, volume_max=50.0,
                               volume_step=0.01, trade_tick_size=0.00001, trade_tick_value=1.0)

    def symbol_info_tick(self, symbol):
        return SimpleNamespace(ask=1.20010, bid=1.20000)

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


# ----- cut & reopen -----

def test_cut_and_reopen_flow(monkeypatch):
    fake = use(monkeypatch, FakeMT5(
        positions=[position(ticket=555, ptype=SELL, profit=-130.0, volume=0.10,
                            sl=1.36, tp=1.30, magic=54321, comment="@1.3390")],
        deals=[deal(commission=-0.7), deal(profit=-127.0, swap=-2.1, commission=-0.7)]))
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
        deals=[deal(), deal(profit=-125.0, swap=-1.4)]))
    run(check_positions_once(FakeClient()))
    # 120 precedenti + 126 realizzati adesso (125.0+1.4 -> round 126)
    assert fake.sent_requests[1]['comment'] == '@1.3390 (-246)'


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
        deals=[deal(), deal(profit=-130.0)],
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
