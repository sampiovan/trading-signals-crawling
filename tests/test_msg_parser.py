import pytest

import msg_parser
from msg_parser import (
    OrderNotFoundException,
    parse_message,
    parse_order_placement,
    parse_order_open,
    parse_order_modify,
    parse_order_close,
    parse_order_cancel,
)


# ----- Messaggi di esempio (dal formato reale del canale) -----

MSG_PLACEMENT = (
    "📈BUY LIMIT  EUR/USD\n"
    " Prezzo 1.12500  (di apertura)\n"
    " \n"
    " Stop Loss   🔴 1.08500\n"
    " \n"
    " Take Profit  🟢  1.20000"
)

MSG_OPEN = (
    "Ordine Buy  EUR/USD    Aperto \n"
    "Prezzo di ingresso  1.12500"
)

MSG_MODIFY = (
    "(BUY LIMIT EUR/USD) - MODIFICARE IL PREZZO DI INGRESSO DA 1.12500 A  1.13000"
    "  mantenendo uguale Stop loss e Take Profit 👍✅"
)

MSG_CLOSE = (
    "📊EUR/USD\n"
    "\n"
    "CHIUDERE MANUALMENTE UNA POSIZIONE IN PROFITTO SU EUR/USD  (1.12500)  ✅✅✅"
)

MSG_CANCEL = "ANNULLARE BUY LIMIT EUR/USD non più valido (1.12500)✅"

MSG_NOT_A_SIGNAL = "Buongiorno a tutti! Oggi mercati laterali, restiamo flat."


# ----- parse_order_placement -----

def test_placement_parsed():
    result = parse_order_placement(MSG_PLACEMENT)
    assert result is not None
    assert result['message_type'] == 'placement'
    assert result['signal_type'] == 'BUY LIMIT'
    assert result['asset'] == 'EURUSD'
    assert result['entry'] == '1.12500'
    assert result['sl'] == '1.08500'
    assert result['tp'] == '1.20000'
    assert result['order_id'] == ''
    # Il magic number viene generato al placement (stringa di 5 cifre)
    assert result['magic_number'].isdigit() and len(result['magic_number']) == 5


def test_placement_ignores_other_messages():
    assert parse_order_placement(MSG_NOT_A_SIGNAL) is None
    assert parse_order_placement(MSG_OPEN) is None


# ----- parse_order_open -----

def test_open_with_known_pending(monkeypatch):
    calls = {}

    def fake_lookup(asset, entry, signal_type):
        calls['args'] = (asset, entry, signal_type)
        return '123456', '54321'

    monkeypatch.setattr(msg_parser, 'get_order_ticket', fake_lookup)
    result = parse_order_open(MSG_OPEN)

    assert calls['args'] == ('EURUSD', '1.12500', 'BUY')
    assert result['message_type'] == 'open'
    assert result['order_id'] == '123456'
    assert result['magic_number'] == '54321'


def test_open_without_registry_match_is_direct_market_order(monkeypatch):
    monkeypatch.setattr(msg_parser, 'get_order_ticket', lambda *a: (None, None))
    result = parse_order_open(MSG_OPEN)

    # Nessuna eccezione: order_id vuoto segnala all'EA un ordine diretto a mercato
    assert result['message_type'] == 'open'
    assert result['order_id'] == ''
    assert result['magic_number'].isdigit() and len(result['magic_number']) == 5


# ----- parse_order_modify -----

def test_modify_looks_up_old_price_and_returns_new(monkeypatch):
    calls = {}

    def fake_lookup(asset, entry, signal_type):
        calls['args'] = (asset, entry, signal_type)
        return '111', '22222'

    monkeypatch.setattr(msg_parser, 'get_order_ticket', fake_lookup)
    result = parse_order_modify(MSG_MODIFY)

    # La lookup avviene sul VECCHIO prezzo, il segnale porta il NUOVO
    assert calls['args'] == ('EURUSD', '1.12500', 'BUY LIMIT')
    assert result['message_type'] == 'modify'
    assert result['entry'] == '1.13000'
    assert result['order_id'] == '111'


def test_modify_raises_when_order_missing(monkeypatch):
    monkeypatch.setattr(msg_parser, 'get_order_ticket', lambda *a: (None, None))
    with pytest.raises(OrderNotFoundException):
        parse_order_modify(MSG_MODIFY)


# ----- parse_order_close -----

def test_close_parsed(monkeypatch):
    calls = {}

    def fake_lookup(asset, entry, signal_type):
        calls['args'] = (asset, entry, signal_type)
        return '333', '44444'

    monkeypatch.setattr(msg_parser, 'get_order_ticket', fake_lookup)
    result = parse_order_close(MSG_CLOSE)

    assert calls['args'] == ('EURUSD', '1.12500', '')
    assert result['message_type'] == 'close'
    assert result['order_id'] == '333'


def test_close_raises_when_order_missing(monkeypatch):
    monkeypatch.setattr(msg_parser, 'get_order_ticket', lambda *a: (None, None))
    with pytest.raises(OrderNotFoundException):
        parse_order_close(MSG_CLOSE)


# ----- parse_order_cancel -----

def test_cancel_parsed(monkeypatch):
    calls = {}

    def fake_lookup(asset, entry, signal_type):
        calls['args'] = (asset, entry, signal_type)
        return '555', '66666'

    monkeypatch.setattr(msg_parser, 'get_order_ticket', fake_lookup)
    result = parse_order_cancel(MSG_CANCEL)

    assert calls['args'] == ('EURUSD', '1.12500', 'BUY LIMIT')
    assert result['message_type'] == 'cancel'
    assert result['order_id'] == '555'


# ----- parse_message (dispatcher) -----

def test_parse_message_recognizes_each_type(monkeypatch):
    monkeypatch.setattr(msg_parser, 'load_order_registry', lambda: {})
    monkeypatch.setattr(msg_parser, 'get_order_ticket', lambda *a: ('777', '88888'))

    assert parse_message(MSG_PLACEMENT)['message_type'] == 'placement'
    assert parse_message(MSG_OPEN)['message_type'] == 'open'
    assert parse_message(MSG_MODIFY)['message_type'] == 'modify'
    assert parse_message(MSG_CLOSE)['message_type'] == 'close'
    assert parse_message(MSG_CANCEL)['message_type'] == 'cancel'


def test_parse_message_returns_none_for_non_signal(monkeypatch):
    monkeypatch.setattr(msg_parser, 'load_order_registry', lambda: {})
    assert parse_message(MSG_NOT_A_SIGNAL) is None
