import pytest

import order_registry
from order_registry import load_order_registry, get_order_ticket, pip_size


HEADER = "timestamp,asset,signal_type,entry,magic,ticket"


@pytest.fixture
def registry_file(tmp_path, monkeypatch):
    """Crea un order_registry.csv temporaneo e ci punta il modulo."""
    path = tmp_path / "order_registry.csv"

    def write(rows):
        path.write_text("\n".join([HEADER] + rows) + "\n", encoding="utf-8")
        load_order_registry()

    monkeypatch.setattr(order_registry, "get_registry_path", lambda: str(path))
    return write


# ----- pip_size -----

def test_pip_size():
    assert pip_size("EURUSD") == 0.0001
    assert pip_size("USDJPY") == 0.01
    assert pip_size("eurjpy") == 0.01


# ----- load_order_registry -----

def test_load_registry_indexes_by_magic(registry_file):
    registry_file([
        "2026-07-10 10:00:00,EURUSD,BUY LIMIT,1.12500,11111,900001",
        "2026-07-10 11:00:00,GBPUSD,SELL STOP,1.27000,22222,900002",
    ])
    assert set(order_registry.ORDER_REGISTRY.keys()) == {"11111", "22222"}
    assert order_registry.ORDER_REGISTRY["11111"]["ticket"] == "900001"


def test_load_registry_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(order_registry, "get_registry_path",
                        lambda: str(tmp_path / "missing.csv"))
    assert load_order_registry() == {}


# ----- get_order_ticket -----

def test_lookup_exact_match(registry_file):
    registry_file(["2026-07-10 10:00:00,EURUSD,BUY LIMIT,1.12500,11111,900001"])
    assert get_order_ticket("EURUSD", "1.12500", "BUY LIMIT") == ("900001", "11111")


def test_lookup_within_tolerance(registry_file):
    registry_file(["2026-07-10 10:00:00,EURUSD,BUY LIMIT,1.12500,11111,900001"])
    # 1.5 pip di distanza: dentro la tolleranza di 2 pip
    assert get_order_ticket("EURUSD", "1.12515", "BUY LIMIT") == ("900001", "11111")
    # 3 pip di distanza: fuori tolleranza
    assert get_order_ticket("EURUSD", "1.12530", "BUY LIMIT") == (None, None)


def test_lookup_jpy_pair_uses_bigger_pip(registry_file):
    registry_file(["2026-07-10 10:00:00,USDJPY,SELL LIMIT,145.500,33333,900003"])
    # 1 pip JPY = 0.01: 145.51 è a 1 pip, match
    assert get_order_ticket("USDJPY", "145.51", "SELL LIMIT") == ("900003", "33333")
    # 3 pip JPY: fuori tolleranza
    assert get_order_ticket("USDJPY", "145.53", "SELL LIMIT") == (None, None)


def test_lookup_picks_closest_match(registry_file):
    registry_file([
        "2026-07-10 10:00:00,EURUSD,BUY LIMIT,1.12500,11111,900001",
        "2026-07-10 10:05:00,EURUSD,BUY LIMIT,1.12510,22222,900002",
    ])
    # 1.12511 è più vicino al secondo ordine: deve vincere lui, non il primo
    assert get_order_ticket("EURUSD", "1.12511", "BUY LIMIT") == ("900002", "22222")


def test_lookup_filters_by_signal_type(registry_file):
    registry_file(["2026-07-10 10:00:00,EURUSD,BUY LIMIT,1.12500,11111,900001"])
    # Tipo diverso allo stesso prezzo: nessun match
    assert get_order_ticket("EURUSD", "1.12500", "SELL LIMIT") == (None, None)
    # Tipo non indicato (es. messaggi di chiusura): il filtro non si applica
    assert get_order_ticket("EURUSD", "1.12500", "") == ("900001", "11111")


def test_lookup_filters_by_asset(registry_file):
    registry_file(["2026-07-10 10:00:00,EURUSD,BUY LIMIT,1.12500,11111,900001"])
    assert get_order_ticket("GBPUSD", "1.12500", "BUY LIMIT") == (None, None)
