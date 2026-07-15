from types import SimpleNamespace

import pytest

from crawler import mt5_client
from crawler.mt5_client import connect, resolve_symbol, Mt5ConnectionError

HEDGING = 2  # valore di ACCOUNT_MARGIN_MODE_RETAIL_HEDGING
NETTING = 0


class FakeMT5:
    ACCOUNT_MARGIN_MODE_RETAIL_HEDGING = HEDGING

    def __init__(self, margin_mode=HEDGING, init_ok=True, account_ok=True, symbols=None):
        self._margin_mode = margin_mode
        self._init_ok = init_ok
        self._account_ok = account_ok
        self._symbols = symbols if symbols is not None else {}
        self.init_path = 'NOT_CALLED'
        self.selected = []
        self.shutdown_called = False

    def initialize(self, path=None):
        self.init_path = path
        return self._init_ok

    def account_info(self):
        if not self._account_ok:
            return None
        return SimpleNamespace(login=123, server="Demo-Server", equity=10000.0,
                               margin_mode=self._margin_mode)

    def shutdown(self):
        self.shutdown_called = True

    def last_error(self):
        return (-1, "errore finto")

    def symbol_info(self, symbol):
        return self._symbols.get(symbol)

    def symbol_select(self, symbol, enable):
        self.selected.append(symbol)
        return True


@pytest.fixture
def no_config(monkeypatch):
    """Config neutro: nessun TERMINAL_PATH né SYMBOL_SUFFIX."""
    monkeypatch.setattr(mt5_client, 'load_config', lambda: None)
    monkeypatch.setattr(mt5_client, 'get_mt5_setting', lambda cfg, key, default='': '')


def test_connect_ok_on_hedging_account(monkeypatch, no_config):
    fake = FakeMT5(margin_mode=HEDGING)
    monkeypatch.setattr(mt5_client, 'mt5', fake)
    account = connect()
    assert account.login == 123
    assert fake.init_path is None  # senza TERMINAL_PATH: initialize() senza argomenti


def test_connect_uses_terminal_path_when_set(monkeypatch):
    fake = FakeMT5()
    monkeypatch.setattr(mt5_client, 'mt5', fake)
    monkeypatch.setattr(mt5_client, 'load_config', lambda: None)
    monkeypatch.setattr(mt5_client, 'get_mt5_setting',
                        lambda cfg, key, default='': r'C:\MT5\terminal64.exe' if key == 'TERMINAL_PATH' else '')
    connect()
    assert fake.init_path == r'C:\MT5\terminal64.exe'


def test_connect_rejects_netting_account(monkeypatch, no_config):
    fake = FakeMT5(margin_mode=NETTING)
    monkeypatch.setattr(mt5_client, 'mt5', fake)
    with pytest.raises(Mt5ConnectionError, match="HEDGING"):
        connect()
    assert fake.shutdown_called  # connessione chiusa dopo il check fallito


def test_connect_fails_when_initialize_fails(monkeypatch, no_config):
    monkeypatch.setattr(mt5_client, 'mt5', FakeMT5(init_ok=False))
    with pytest.raises(Mt5ConnectionError, match="initialize"):
        connect()


def test_connect_fails_without_logged_account(monkeypatch, no_config):
    monkeypatch.setattr(mt5_client, 'mt5', FakeMT5(account_ok=False))
    with pytest.raises(Mt5ConnectionError, match="conto"):
        connect()


def test_connect_fails_without_package(monkeypatch):
    monkeypatch.setattr(mt5_client, 'mt5', None)
    with pytest.raises(Mt5ConnectionError, match="MetaTrader5"):
        connect()


def test_resolve_symbol_applies_suffix_and_selects(monkeypatch):
    fake = FakeMT5(symbols={'EURUSD.m': SimpleNamespace(visible=False)})
    monkeypatch.setattr(mt5_client, 'mt5', fake)
    monkeypatch.setattr(mt5_client, 'load_config', lambda: None)
    monkeypatch.setattr(mt5_client, 'get_mt5_setting',
                        lambda cfg, key, default='': '.m' if key == 'SYMBOL_SUFFIX' else '')
    assert resolve_symbol('EURUSD') == 'EURUSD.m'
    assert fake.selected == ['EURUSD.m']  # non visibile: selezionato nel Market Watch


def test_resolve_symbol_visible_needs_no_select(monkeypatch, no_config):
    fake = FakeMT5(symbols={'EURUSD': SimpleNamespace(visible=True)})
    monkeypatch.setattr(mt5_client, 'mt5', fake)
    assert resolve_symbol('EURUSD') == 'EURUSD'
    assert fake.selected == []


def test_resolve_symbol_unknown_raises(monkeypatch, no_config):
    monkeypatch.setattr(mt5_client, 'mt5', FakeMT5(symbols={}))
    with pytest.raises(ValueError, match="GBPUSD"):
        resolve_symbol('GBPUSD')
