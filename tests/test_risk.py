from types import SimpleNamespace

import pytest

from crawler import risk
from crawler.risk import compute_lot, normalize_volume


# EURUSD a 5 cifre: tick 0.00001 vale 1$ per lotto standard
EURUSD = SimpleNamespace(volume_min=0.01, volume_max=50.0, volume_step=0.01,
                         trade_tick_size=0.00001, trade_tick_value=1.0)

ACCOUNT = SimpleNamespace(equity=10000.0)


def make_signal(**overrides):
    signal = {
        'order_id': '', 'magic_number': '54321', 'message_type': 'placement',
        'signal_type': 'BUY LIMIT', 'asset': 'EURUSD',
        'entry': '1.12500', 'sl': '1.12000', 'tp': '1.20000', 'comment': ''
    }
    signal.update(overrides)
    return signal


def set_risk_config(monkeypatch, mode='FIXED', fixed_lot='0.01', risk_percent='1.0'):
    values = {'MODE': mode, 'FIXED_LOT': fixed_lot, 'RISK_PERCENT': risk_percent}
    monkeypatch.setattr(risk, 'load_config', lambda: None)
    monkeypatch.setattr(risk, 'get_setting',
                        lambda cfg, section, key, default='': values.get(key, default))


# ----- normalize_volume -----

def test_normalize_rounds_down_to_step():
    assert normalize_volume(0.237, EURUSD) == 0.23


def test_normalize_clamps_to_min_and_max():
    assert normalize_volume(0.001, EURUSD) == 0.01
    assert normalize_volume(999.0, EURUSD) == 50.0


def test_normalize_respects_bigger_step():
    chunky = SimpleNamespace(volume_min=0.1, volume_max=100.0, volume_step=0.1,
                             trade_tick_size=0.01, trade_tick_value=1.0)
    assert normalize_volume(0.37, chunky) == 0.3


# ----- MODE=FIXED (default) -----

def test_fixed_mode_returns_fixed_lot(monkeypatch):
    set_risk_config(monkeypatch, mode='FIXED', fixed_lot='0.05')
    assert compute_lot(make_signal(), EURUSD, ACCOUNT) == 0.05


def test_missing_config_defaults_to_fixed(monkeypatch):
    monkeypatch.setattr(risk, 'load_config', lambda: None)
    monkeypatch.setattr(risk, 'get_setting', lambda cfg, s, k, default='': default)
    assert compute_lot(make_signal(), EURUSD, ACCOUNT) == 0.01


def test_unknown_mode_falls_back_to_fixed(monkeypatch):
    set_risk_config(monkeypatch, mode='YOLO', fixed_lot='0.02')
    assert compute_lot(make_signal(), EURUSD, ACCOUNT) == 0.02


# ----- MODE=RISK_PERCENT -----

def test_risk_percent_sizing(monkeypatch):
    set_risk_config(monkeypatch, mode='RISK_PERCENT', risk_percent='1.0')
    # rischio = 1% di 10000 = 100$; SL a 50 pip (0.00500 = 500 tick da 1$) = 500$/lotto
    # lotto = 100/500 = 0.2
    signal = make_signal(entry='1.12500', sl='1.12000')
    assert compute_lot(signal, EURUSD, ACCOUNT) == 0.2


def test_risk_percent_scales_with_percentage(monkeypatch):
    set_risk_config(monkeypatch, mode='RISK_PERCENT', risk_percent='2.0')
    assert compute_lot(make_signal(), EURUSD, ACCOUNT) == 0.4


def test_risk_percent_clamps_to_volume_min(monkeypatch):
    set_risk_config(monkeypatch, mode='RISK_PERCENT', risk_percent='0.001')
    # rischio 0.1$ -> lotto teorico 0.0002 -> clampato a volume_min
    assert compute_lot(make_signal(), EURUSD, ACCOUNT) == 0.01


def test_risk_percent_jpy_style_symbol(monkeypatch):
    set_risk_config(monkeypatch, mode='RISK_PERCENT', risk_percent='1.0')
    # Simbolo stile JPY: tick 0.001 con valore ~0.65$
    usdjpy = SimpleNamespace(volume_min=0.01, volume_max=50.0, volume_step=0.01,
                             trade_tick_size=0.001, trade_tick_value=0.65)
    # SL a 0.5 (500 tick) -> 325$/lotto; 100$/325$ = 0.3076 -> 0.30
    signal = make_signal(asset='USDJPY', entry='145.500', sl='145.000')
    assert compute_lot(signal, usdjpy, ACCOUNT) == 0.30


@pytest.mark.parametrize("entry,sl", [('1.12500', ''), ('1.12500', 0), ('', '1.12000'), (0, 0)])
def test_risk_percent_without_sl_or_entry_falls_back(monkeypatch, entry, sl):
    set_risk_config(monkeypatch, mode='RISK_PERCENT', fixed_lot='0.03')
    assert compute_lot(make_signal(entry=entry, sl=sl), EURUSD, ACCOUNT) == 0.03


def test_risk_percent_without_account_falls_back(monkeypatch):
    set_risk_config(monkeypatch, mode='RISK_PERCENT', fixed_lot='0.03')
    assert compute_lot(make_signal(), EURUSD, None) == 0.03


def test_risk_percent_with_broken_symbol_data_falls_back(monkeypatch):
    set_risk_config(monkeypatch, mode='RISK_PERCENT', fixed_lot='0.03')
    broken = SimpleNamespace(volume_min=0.01, volume_max=50.0, volume_step=0.01,
                             trade_tick_size=0.00001, trade_tick_value=0.0)
    assert compute_lot(make_signal(), broken, ACCOUNT) == 0.03
