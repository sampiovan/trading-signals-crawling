import pytest

import msg_parser
from msg_parser import (
    OrderNotFoundException,
    parse_message,
    parse_order_placement,
    parse_order_open,
    parse_order_modify,
    parse_move_sl_all,
    parse_move_sl_breakeven,
    parse_orders_multi_close,
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

MSG_MULTI_CLOSE_2 = (
    "📊AUD/NZD \n"
    "\n"
    "CHIUDERE MANUALMENTE DUE POSIZIONI DI CUI:\n"
    "\n"
    "UNA IN PROFITTO su           AUD/NZD   (1.21600) \n"
    "\n"
    "UNA IN PROFITTO su          AUD/NZD  (1.21403) \n"
    "\n"
    "\n"
    "TOTALE IN PROFITTO✅✅✅"
)

MSG_MULTI_CLOSE_4 = (
    "📊EUR/USD   -  AUD/NZD\n"
    "\n"
    "CHIUDERE MANUALMENTE QUATTRO POSIZIONI DI CUI:\n"
    "\n"
    "UNA IN PROFITTO su          EUR/USD   (1.14700) \n"
    "\n"
    "UNA IN PROFITTO su          AUD/NZD  (1.21700)\n"
    "\n"
    "una in perdita su                 AUD/NZD  (1.21300)\n"
    "\n"
    "una in perdita su                 AUD/NZD  (1.20961)\n"
    "\n"
    "\n"
    "TOTALE IN PARI O DI POCO IN PROFITTO✅"
)

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


# ----- parse_orders_multi_close -----

def test_multi_close_two_positions(monkeypatch):
    lookups = []

    def fake_lookup(asset, entry, signal_type):
        lookups.append((asset, entry))
        return f"t{len(lookups)}", f"m{len(lookups)}"

    monkeypatch.setattr(msg_parser, 'get_order_ticket', fake_lookup)
    signals = parse_orders_multi_close(MSG_MULTI_CLOSE_2)

    assert lookups == [('AUDNZD', '1.21600'), ('AUDNZD', '1.21403')]
    assert len(signals) == 2
    assert all(s['message_type'] == 'close' for s in signals)
    assert [s['order_id'] for s in signals] == ['t1', 't2']


def test_multi_close_four_positions_multi_asset(monkeypatch):
    lookups = []

    def fake_lookup(asset, entry, signal_type):
        lookups.append((asset, entry))
        return f"t{len(lookups)}", f"m{len(lookups)}"

    monkeypatch.setattr(msg_parser, 'get_order_ticket', fake_lookup)
    signals = parse_orders_multi_close(MSG_MULTI_CLOSE_4)

    # Tutte e 4 le posizioni, con l'asset giusto (anche "una in perdita" minuscolo)
    assert lookups == [
        ('EURUSD', '1.14700'),
        ('AUDNZD', '1.21700'),
        ('AUDNZD', '1.21300'),
        ('AUDNZD', '1.20961'),
    ]
    assert len(signals) == 4


def test_multi_close_partial_lookup_failure(monkeypatch):
    def fake_lookup(asset, entry, signal_type):
        # Solo la seconda posizione è nel registro
        if entry == '1.21403':
            return '222', '22222'
        return None, None

    monkeypatch.setattr(msg_parser, 'get_order_ticket', fake_lookup)
    signals = parse_orders_multi_close(MSG_MULTI_CLOSE_2)

    # Successo parziale: la posizione mancante è saltata, l'altra chiusa
    assert len(signals) == 1
    assert signals[0]['order_id'] == '222'


def test_multi_close_all_lookups_fail(monkeypatch):
    monkeypatch.setattr(msg_parser, 'get_order_ticket', lambda *a: (None, None))
    with pytest.raises(OrderNotFoundException):
        parse_orders_multi_close(MSG_MULTI_CLOSE_2)


def test_multi_close_not_captured_by_single_close(monkeypatch):
    # Prima del fix il parser single-close catturava il messaggio multi
    # chiudendo UNA sola posizione: il dispatcher deve produrre N segnali
    monkeypatch.setattr(msg_parser, 'load_order_registry', lambda: {})
    monkeypatch.setattr(msg_parser, 'get_order_ticket', lambda *a: ('9', '99999'))

    signals = parse_message(MSG_MULTI_CLOSE_4)
    assert len(signals) == 4


def test_single_close_still_works_via_dispatcher(monkeypatch):
    monkeypatch.setattr(msg_parser, 'load_order_registry', lambda: {})
    monkeypatch.setattr(msg_parser, 'get_order_ticket', lambda *a: ('9', '99999'))

    signals = parse_message(MSG_CLOSE)
    assert len(signals) == 1
    assert signals[0]['message_type'] == 'close'


# ----- parse_move_sl_all / parse_move_sl_breakeven -----

MSG_MOVE_SL_ALL = (
    "📊EUR/USD\n"
    "\n"
    "MODIFICARE IL VALORE DI STOP LOSS SU TUTTE LE OPERAZIONI IN CORSO SU EUR/USD a  0.90000\n"
    "\n"
    "🔸ATTENZIONE VISTO CHE OGGI É VENERDÍ E CI APPRESTIAMO ALLA CHIUSURA DEI MERCATI VALUTARI, "
    "PREFERIAMO SPOSTARE IL VALORE DELLO STOP LOSS, PER SICUREZZA. GRAZIE💪"
)

MSG_MOVE_SL_BREAKEVEN = (
    "GBP/USD Move Stop Loss to Breakeven o comunque in posizione di profitto a  1.33890✅\n"
    "\n"
    "Per i meno esperti ciò significa Spostare lo stop Loss appena sotto al punto di apertura "
    "così l'operazione è a rischio zero 👍 \n"
    "Al momento ci sono circa 25 Pips in profitto📉"
)

MSG_OPEN_GBP = (
    "Ordine Sell  GBP/USD    Aperto \n"
    "Prezzo di ingresso  1.34121"
)


def test_move_sl_all_positions():
    result = parse_move_sl_all(MSG_MOVE_SL_ALL)
    assert result['message_type'] == 'move_sl'
    assert result['asset'] == 'EURUSD'
    assert result['sl'] == '0.90000'
    assert result['order_id'] == ''


def test_move_sl_breakeven_without_reply_targets_all(monkeypatch):
    monkeypatch.setattr(msg_parser, 'get_order_ticket', lambda *a: (None, None))
    result = parse_move_sl_breakeven(MSG_MOVE_SL_BREAKEVEN)

    assert result['message_type'] == 'move_sl'
    assert result['asset'] == 'GBPUSD'
    assert result['sl'] == '1.33890'


def test_move_sl_breakeven_with_reply_targets_single_order(monkeypatch):
    lookups = []

    def fake_lookup(asset, entry, signal_type):
        lookups.append((asset, entry))
        return '424242', '31337'

    monkeypatch.setattr(msg_parser, 'get_order_ticket', fake_lookup)
    result = parse_move_sl_breakeven(MSG_MOVE_SL_BREAKEVEN, reply_text=MSG_OPEN_GBP)

    # Il reply (messaggio di apertura) identifica l'ordine esatto
    assert ('GBPUSD', '1.34121') in lookups
    assert result['message_type'] == 'modify'
    assert result['order_id'] == '424242'
    assert result['sl'] == '1.33890'
    assert result['entry'] == 0  # prezzo invariato


def test_move_sl_breakeven_reply_asset_mismatch_falls_back(monkeypatch):
    monkeypatch.setattr(msg_parser, 'get_order_ticket', lambda *a: ('1', '2'))
    reply_other_asset = "Ordine Buy  EUR/USD    Aperto \nPrezzo di ingresso  1.12500"
    result = parse_move_sl_breakeven(MSG_MOVE_SL_BREAKEVEN, reply_text=reply_other_asset)

    # Reply su asset diverso: fallback a tutte le posizioni sull'asset del messaggio
    assert result['message_type'] == 'move_sl'
    assert result['asset'] == 'GBPUSD'


def test_move_sl_via_dispatcher(monkeypatch):
    monkeypatch.setattr(msg_parser, 'load_order_registry', lambda: {})
    monkeypatch.setattr(msg_parser, 'get_order_ticket', lambda *a: (None, None))

    signals = parse_message(MSG_MOVE_SL_ALL)
    assert len(signals) == 1 and signals[0]['message_type'] == 'move_sl'

    signals = parse_message(MSG_MOVE_SL_BREAKEVEN)
    assert len(signals) == 1 and signals[0]['message_type'] == 'move_sl'


# ----- parse_message (dispatcher) -----

def test_parse_message_recognizes_each_type(monkeypatch):
    monkeypatch.setattr(msg_parser, 'load_order_registry', lambda: {})
    monkeypatch.setattr(msg_parser, 'get_order_ticket', lambda *a: ('777', '88888'))

    # parse_message restituisce sempre una lista di segnali
    for msg, expected_type in [
        (MSG_PLACEMENT, 'placement'),
        (MSG_OPEN, 'open'),
        (MSG_MODIFY, 'modify'),
        (MSG_CLOSE, 'close'),
        (MSG_CANCEL, 'cancel'),
    ]:
        signals = parse_message(msg)
        assert isinstance(signals, list) and len(signals) == 1
        assert signals[0]['message_type'] == expected_type


def test_parse_message_returns_none_for_non_signal(monkeypatch):
    monkeypatch.setattr(msg_parser, 'load_order_registry', lambda: {})
    assert parse_message(MSG_NOT_A_SIGNAL) is None
