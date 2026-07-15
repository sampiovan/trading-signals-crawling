from types import SimpleNamespace

import pytest

from crawler import executor
from crawler import mt5_client
from crawler.executor import (
    execute,
    TRADE_ACTION_DEAL,
    TRADE_ACTION_PENDING,
    TRADE_ACTION_SLTP,
    TRADE_ACTION_REMOVE,
    ORDER_TYPE_BUY,
    ORDER_TYPE_SELL,
    RETCODE_DONE,
    MAX_ATTEMPTS,
)


def result(retcode, order=777, comment="done"):
    return SimpleNamespace(retcode=retcode, order=order, comment=comment)


class FakeMT5:
    """Stub del package MetaTrader5: order_send consuma una coda di risultati."""

    def __init__(self, send_results=None, positions=(), orders=()):
        self.send_results = list(send_results if send_results is not None else [result(RETCODE_DONE)])
        self.sent_requests = []
        self._positions = list(positions)
        self._orders = list(orders)

    def order_send(self, request):
        self.sent_requests.append(request)
        return self.send_results.pop(0) if self.send_results else None

    def positions_get(self, ticket=None, symbol=None):
        found = [p for p in self._positions
                 if (ticket is None or p.ticket == ticket) and (symbol is None or p.symbol == symbol)]
        return tuple(found)

    def orders_get(self, ticket=None):
        return tuple(o for o in self._orders if ticket is None or o.ticket == ticket)

    def symbol_info(self, symbol):
        return SimpleNamespace(filling_mode=2,  # supporta IOC
                               volume_min=0.01, volume_max=50.0, volume_step=0.01,
                               trade_tick_size=0.00001, trade_tick_value=1.0)

    def symbol_info_tick(self, symbol):
        return SimpleNamespace(ask=1.20010, bid=1.20000)

    def account_info(self):
        return SimpleNamespace(equity=100000.0)

    def last_error(self):
        return (-1, "errore finto")


def position(ticket=555, symbol="EURUSD", ptype=ORDER_TYPE_BUY, sl=0.0, tp=1.30, volume=0.05, magic=42):
    return SimpleNamespace(ticket=ticket, symbol=symbol, type=ptype, sl=sl, tp=tp,
                           volume=volume, magic=magic)


def pending(ticket=666, price_open=1.10, sl=1.05, tp=1.30, symbol="EURUSD",
            volume_current=0.05, otype=2, magic=54321):
    return SimpleNamespace(ticket=ticket, price_open=price_open, sl=sl, tp=tp,
                           symbol=symbol, volume_current=volume_current,
                           type=otype, magic=magic)


def make_signal(**overrides):
    signal = {
        'order_id': '', 'magic_number': '54321', 'message_type': 'placement',
        'signal_type': 'BUY LIMIT', 'asset': 'EURUSD',
        'entry': '1.12500', 'sl': '1.08500', 'tp': '1.20000', 'comment': ''
    }
    signal.update(overrides)
    return signal


@pytest.fixture(autouse=True)
def wire_stub(monkeypatch):
    """Config neutro, niente sleep nei retry, resolve_symbol identità, lotto fisso."""
    monkeypatch.setattr(executor, 'load_config', lambda: None)
    monkeypatch.setattr(executor, 'get_mt5_setting',
                        lambda cfg, key, default='': default)
    monkeypatch.setattr(executor, 'compute_lot', lambda signal, si, ai: 0.01)
    monkeypatch.setattr(executor.time, 'sleep', lambda s: None)
    monkeypatch.setattr(mt5_client, 'resolve_symbol', lambda asset: asset)


def use(monkeypatch, fake):
    monkeypatch.setattr(executor, 'mt5', fake)
    return fake


# ----- placement -----

def test_pending_placement(monkeypatch):
    fake = use(monkeypatch, FakeMT5())
    outcome = execute(make_signal())

    assert outcome.ok and outcome.ticket == 777
    req = fake.sent_requests[0]
    assert req['action'] == TRADE_ACTION_PENDING
    assert req['type'] == 2  # BUY_LIMIT
    assert req['price'] == 1.125
    assert req['sl'] == 1.085 and req['tp'] == 1.2
    assert req['magic'] == 54321
    assert req['comment'] == '@1.1250'  # prezzo del canale, pip-rounded


def test_market_comment_is_channel_price_not_fill(monkeypatch):
    fake = use(monkeypatch, FakeMT5())
    execute(make_signal(signal_type='SELL', entry='1.34121'))
    req = fake.sent_requests[0]
    assert req['price'] == 1.20000          # fill al prezzo corrente...
    assert req['comment'] == '@1.3412'      # ...ma il commento resta quello del canale


def test_market_placement_uses_current_price(monkeypatch):
    fake = use(monkeypatch, FakeMT5())
    outcome = execute(make_signal(signal_type='SELL', entry='1.34121'))

    assert outcome.ok
    req = fake.sent_requests[0]
    assert req['action'] == TRADE_ACTION_DEAL
    assert req['type'] == ORDER_TYPE_SELL
    assert req['price'] == 1.20000  # bid corrente, non l'entry del segnale


def test_volume_comes_from_risk_module(monkeypatch):
    fake = use(monkeypatch, FakeMT5())
    monkeypatch.setattr(executor, 'compute_lot', lambda signal, si, ai: 0.25)
    execute(make_signal())
    assert fake.sent_requests[0]['volume'] == 0.25


def test_unknown_signal_type_fails_without_sending(monkeypatch):
    fake = use(monkeypatch, FakeMT5())
    outcome = execute(make_signal(signal_type='PIPPO'))
    assert not outcome.ok and fake.sent_requests == []


# ----- open -----

def test_open_with_known_position_verifies_only(monkeypatch):
    fake = use(monkeypatch, FakeMT5(positions=[position(ticket=555)]))
    outcome = execute(make_signal(message_type='open', order_id='555', signal_type='BUY'))
    assert outcome.ok and fake.sent_requests == []


def test_open_still_pending_warns_without_action(monkeypatch):
    fake = use(monkeypatch, FakeMT5(orders=[pending(ticket=666)]))
    outcome = execute(make_signal(message_type='open', order_id='666', signal_type='BUY'))
    assert outcome.ok and "pendente" in outcome.message
    assert fake.sent_requests == []


def test_open_with_missing_ticket_fails(monkeypatch):
    use(monkeypatch, FakeMT5())
    outcome = execute(make_signal(message_type='open', order_id='999', signal_type='BUY'))
    assert not outcome.ok


def test_open_direct_market_order(monkeypatch):
    fake = use(monkeypatch, FakeMT5())
    outcome = execute(make_signal(message_type='open', order_id='', signal_type='SELL', sl='', tp=''))
    assert outcome.ok
    assert fake.sent_requests[0]['action'] == TRADE_ACTION_DEAL


# ----- modify -----

def test_modify_pending_removes_and_replaces_with_new_comment(monkeypatch):
    # MT5 non permette di cambiare il commento con la MODIFY: la modifica
    # di un pending è remove + re-place con commento "@nuovo-prezzo"
    fake = use(monkeypatch, FakeMT5(send_results=[result(RETCODE_DONE), result(RETCODE_DONE, order=888)],
                                    orders=[pending(ticket=666, sl=1.05, tp=1.30)]))
    outcome = execute(make_signal(message_type='modify', order_id='666',
                                  entry='1.13000', sl=0, tp=0))
    assert outcome.ok
    remove_req, place_req = fake.sent_requests
    assert remove_req == {'action': TRADE_ACTION_REMOVE, 'order': 666}
    assert place_req['action'] == TRADE_ACTION_PENDING
    assert place_req['price'] == 1.13
    assert place_req['sl'] == 1.05 and place_req['tp'] == 1.30  # 0 = invariato
    assert place_req['volume'] == 0.05 and place_req['magic'] == 54321  # ereditati dal pending
    assert place_req['comment'] == '@1.1300'  # commento aggiornato al nuovo prezzo


def test_modify_pending_replace_failure_is_critical(monkeypatch):
    # Remove riuscito ma re-place respinto: outcome critico (serve intervento manuale)
    fake = use(monkeypatch, FakeMT5(send_results=[result(RETCODE_DONE), result(10019, comment="no money")],
                                    orders=[pending(ticket=666)]))
    outcome = execute(make_signal(message_type='modify', order_id='666', entry='1.13000', sl=0, tp=0))
    assert not outcome.ok
    assert 'CRITICO' in outcome.message
    assert len(fake.sent_requests) == 2


def test_modify_position_sets_sl_only(monkeypatch):
    fake = use(monkeypatch, FakeMT5(positions=[position(ticket=555, sl=0.0, tp=1.30)]))
    outcome = execute(make_signal(message_type='modify', order_id='555',
                                  entry=0, sl='1.33890', tp=0))
    assert outcome.ok
    req = fake.sent_requests[0]
    assert req['action'] == TRADE_ACTION_SLTP
    assert req['position'] == 555
    assert req['sl'] == 1.33890 and req['tp'] == 1.30


def test_modify_missing_ticket_fails(monkeypatch):
    use(monkeypatch, FakeMT5())
    outcome = execute(make_signal(message_type='modify', order_id='404', entry='1.1'))
    assert not outcome.ok


# ----- move_sl -----

def test_move_sl_updates_all_positions_on_symbol(monkeypatch):
    fake = use(monkeypatch, FakeMT5(
        send_results=[result(RETCODE_DONE), result(RETCODE_DONE)],
        positions=[position(ticket=1, sl=0.0), position(ticket=2, sl=0.0),
                   position(ticket=3, symbol="GBPUSD")]))
    outcome = execute(make_signal(message_type='move_sl', signal_type='', order_id='',
                                  magic_number='', entry=0, sl='1.15000', tp=0))
    assert outcome.ok
    assert len(fake.sent_requests) == 2  # solo le 2 EURUSD
    assert all(r['action'] == TRADE_ACTION_SLTP and r['sl'] == 1.15 for r in fake.sent_requests)


def test_move_sl_skips_positions_already_at_target(monkeypatch):
    fake = use(monkeypatch, FakeMT5(positions=[position(ticket=1, sl=1.15)]))
    outcome = execute(make_signal(message_type='move_sl', order_id='', magic_number='',
                                  entry=0, sl='1.15000', tp=0))
    assert outcome.ok and fake.sent_requests == []


def test_move_sl_without_positions_fails(monkeypatch):
    use(monkeypatch, FakeMT5())
    outcome = execute(make_signal(message_type='move_sl', order_id='', magic_number='',
                                  entry=0, sl='1.15000', tp=0))
    assert not outcome.ok


# ----- close / cancel -----

def test_close_sends_opposite_deal_on_position(monkeypatch):
    fake = use(monkeypatch, FakeMT5(positions=[position(ticket=555, ptype=ORDER_TYPE_BUY, volume=0.05)]))
    outcome = execute(make_signal(message_type='close', order_id='555', signal_type='',
                                  entry=0.0, sl='', tp=''))
    assert outcome.ok
    req = fake.sent_requests[0]
    assert req['action'] == TRADE_ACTION_DEAL
    assert req['type'] == ORDER_TYPE_SELL  # opposto del BUY
    assert req['position'] == 555
    assert req['volume'] == 0.05
    assert req['price'] == 1.20000  # bid per chiudere un BUY


def test_close_missing_position_fails(monkeypatch):
    use(monkeypatch, FakeMT5())
    outcome = execute(make_signal(message_type='close', order_id='404', entry=0.0))
    assert not outcome.ok


def test_cancel_removes_pending(monkeypatch):
    fake = use(monkeypatch, FakeMT5())
    outcome = execute(make_signal(message_type='cancel', order_id='666'))
    assert outcome.ok
    assert fake.sent_requests[0] == {'action': TRADE_ACTION_REMOVE, 'order': 666}


# ----- retry -----

def test_retry_on_transient_then_success(monkeypatch):
    fake = use(monkeypatch, FakeMT5(send_results=[result(10004, comment="requote"),
                                                  result(RETCODE_DONE)]))
    outcome = execute(make_signal())
    assert outcome.ok
    assert len(fake.sent_requests) == 2  # richiesta ricostruita e ritentata


def test_no_retry_on_definitive_failure(monkeypatch):
    fake = use(monkeypatch, FakeMT5(send_results=[result(10019, comment="no money")]))
    outcome = execute(make_signal())
    assert not outcome.ok and outcome.retcode == 10019
    assert len(fake.sent_requests) == 1


def test_retry_exhausted_returns_last_failure(monkeypatch):
    fake = use(monkeypatch, FakeMT5(send_results=[result(10004)] * MAX_ATTEMPTS))
    outcome = execute(make_signal())
    assert not outcome.ok and outcome.retcode == 10004
    assert len(fake.sent_requests) == MAX_ATTEMPTS


def test_unknown_message_type(monkeypatch):
    use(monkeypatch, FakeMT5())
    outcome = execute(make_signal(message_type='boh'))
    assert not outcome.ok


def test_handler_exception_becomes_outcome(monkeypatch):
    class Broken(FakeMT5):
        def positions_get(self, **kw):
            raise RuntimeError("terminale esploso")

    use(monkeypatch, Broken())
    outcome = execute(make_signal(message_type='close', order_id='1', entry=0.0))
    assert not outcome.ok and "errore inatteso" in outcome.message
