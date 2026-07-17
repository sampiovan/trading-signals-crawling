from types import SimpleNamespace

import pytest

from crawler import mt5_client
from crawler import order_lookup
from crawler.order_lookup import get_order_ticket, pip_size

BUY, SELL, BUY_LIMIT, SELL_LIMIT = 0, 1, 2, 3


def position(ticket, symbol, price_open, ptype=BUY, magic=11111, comment=''):
    return SimpleNamespace(ticket=ticket, symbol=symbol, price_open=price_open,
                           type=ptype, magic=magic, comment=comment)


class FakeMT5:
    def __init__(self, positions=(), orders=()):
        self._positions = list(positions)
        self._orders = list(orders)

    def positions_get(self, symbol=None):
        return tuple(p for p in self._positions if symbol is None or p.symbol == symbol)

    def orders_get(self, symbol=None):
        return tuple(o for o in self._orders if symbol is None or o.symbol == symbol)


@pytest.fixture(autouse=True)
def identity_symbols(monkeypatch):
    monkeypatch.setattr(mt5_client, 'resolve_symbol', lambda asset: asset)


def use(monkeypatch, fake):
    monkeypatch.setattr(order_lookup, 'mt5', fake)
    return fake


# ----- pip_size -----

def test_pip_size():
    assert pip_size("EURUSD") == 0.0001
    assert pip_size("USDJPY") == 0.01
    assert pip_size("eurjpy") == 0.01


# ----- get_order_ticket su posizioni live -----

def test_lookup_exact_match_on_position(monkeypatch):
    use(monkeypatch, FakeMT5(positions=[position(900001, "EURUSD", 1.12500, BUY, 11111)]))
    assert get_order_ticket("EURUSD", "1.12500", "BUY") == ("900001", "11111")


def test_lookup_finds_pending_orders_too(monkeypatch):
    use(monkeypatch, FakeMT5(orders=[position(900002, "EURUSD", 1.10000, BUY_LIMIT, 22222)]))
    assert get_order_ticket("EURUSD", "1.10000", "BUY LIMIT") == ("900002", "22222")


def test_lookup_within_tolerance(monkeypatch):
    use(monkeypatch, FakeMT5(positions=[position(900001, "EURUSD", 1.12500, BUY)]))
    # 1.5 pip: dentro la tolleranza di 2 pip
    assert get_order_ticket("EURUSD", "1.12515", "BUY")[0] == "900001"
    # 3 pip: fuori
    assert get_order_ticket("EURUSD", "1.12530", "BUY") == (None, None)


def test_lookup_jpy_pair_uses_bigger_pip(monkeypatch):
    use(monkeypatch, FakeMT5(positions=[position(900003, "USDJPY", 145.500, SELL, 33333)]))
    assert get_order_ticket("USDJPY", "145.51", "SELL") == ("900003", "33333")
    assert get_order_ticket("USDJPY", "145.53", "SELL") == (None, None)


def test_lookup_picks_closest_match(monkeypatch):
    use(monkeypatch, FakeMT5(positions=[
        position(1, "EURUSD", 1.12500, BUY, 11111),
        position(2, "EURUSD", 1.12510, BUY, 22222),
    ]))
    # 1.12511 è più vicino alla seconda posizione
    assert get_order_ticket("EURUSD", "1.12511", "BUY") == ("2", "22222")


def test_lookup_filters_by_signal_type(monkeypatch):
    use(monkeypatch, FakeMT5(orders=[position(1, "EURUSD", 1.12500, BUY_LIMIT)]))
    # Tipo diverso allo stesso prezzo: nessun match
    assert get_order_ticket("EURUSD", "1.12500", "SELL LIMIT") == (None, None)
    # Tipo non indicato (es. messaggi di chiusura): il filtro non si applica
    assert get_order_ticket("EURUSD", "1.12500", "")[0] == "1"


def test_lookup_filters_by_symbol(monkeypatch):
    use(monkeypatch, FakeMT5(positions=[position(1, "EURUSD", 1.12500, BUY)]))
    assert get_order_ticket("GBPUSD", "1.12500", "BUY") == (None, None)


def test_lookup_matches_reopened_position_by_comment(monkeypatch):
    # Dopo un cut&reopen il price_open reale è LONTANO dal prezzo del canale,
    # ma il commento "@prezzo" lo conserva: il lookup deve ritrovarla
    use(monkeypatch, FakeMT5(positions=[
        position(900010, "GBPUSD", 1.32100, SELL, 55555, comment="@1.3390 (-120)"),
    ]))
    assert get_order_ticket("GBPUSD", "1.33900", "SELL") == ("900010", "55555")


def test_lookup_comment_match_beats_price_match(monkeypatch):
    # Una posizione col prezzo "giusto" ma commento diverso NON deve vincere
    # su quella riaperta che porta il commento del segnale
    use(monkeypatch, FakeMT5(positions=[
        position(1, "EURUSD", 1.12500, BUY, 11111, comment="@1.1300"),
        position(2, "EURUSD", 1.19000, BUY, 22222, comment="@1.1250 (-80)"),
    ]))
    assert get_order_ticket("EURUSD", "1.12500", "BUY") == ("2", "22222")


def unresolvable_symbols(monkeypatch):
    def boom(asset):
        raise ValueError("simbolo inesistente")
    monkeypatch.setattr(mt5_client, 'resolve_symbol', boom)


def test_lookup_unresolvable_symbol_returns_none(monkeypatch):
    use(monkeypatch, FakeMT5())
    unresolvable_symbols(monkeypatch)
    assert get_order_ticket("XXXYYY", "1.0", "") == (None, None)


def test_lookup_unresolvable_symbol_falls_back_to_comment(monkeypatch):
    # Il caso reale: il canale scrive "GPS/USD" per GBP/USD. Il simbolo non
    # si risolve, ma il commento "@prezzo" (1.34946 -> @1.3495) identifica
    # la posizione in modo univoco su tutto il conto
    use(monkeypatch, FakeMT5(positions=[
        position(900020, "GBPUSD", 1.35340, SELL, 99599, comment="@1.3495 (-82)"),
    ]))
    unresolvable_symbols(monkeypatch)
    assert get_order_ticket("GPSUSD", "1.34946", "") == ("900020", "99599")


def test_lookup_comment_fallback_requires_unique_match(monkeypatch):
    # Stesso prezzo di commento su due simboli diversi: ambiguo, si scarta
    use(monkeypatch, FakeMT5(positions=[
        position(1, "GBPUSD", 1.35340, SELL, 111, comment="@1.3495"),
        position(2, "EURUSD", 1.34960, BUY, 222, comment="@1.3495"),
    ]))
    unresolvable_symbols(monkeypatch)
    assert get_order_ticket("GPSUSD", "1.34946", "") == (None, None)


def test_lookup_comment_fallback_uses_real_symbol_pip(monkeypatch):
    # Refuso su una coppia JPY ("USDJPI"): il prezzo del commento va
    # arrotondato col pip del simbolo REALE del candidato (2 decimali),
    # non con l'euristica sull'asset sbagliato (4 decimali)
    use(monkeypatch, FakeMT5(positions=[
        position(900030, "USDJPY", 145.700, SELL, 44444, comment="@145.50 (-30)"),
    ]))
    unresolvable_symbols(monkeypatch)
    assert get_order_ticket("USDJPI", "145.503", "") == ("900030", "44444")
