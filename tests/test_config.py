import pytest

from crawler import config as config_module
from crawler.config import load_config, reset_config, get_mt5_setting


VALID_CONFIG = """
[telegram]
YOUR_API_ID = 123456
YOUR_API_HASH = abcdef0123456789
SESSION_NAME = test_session
CHANNEL_ENTITY = @canale

[mt5]
TERMINAL_PATH =
SYMBOL_SUFFIX = .m

[risk]
MODE = BALANCE
FIXED_LOT = 0.01
RISK_PERCENT = 1.0
INITIAL_DEPOSIT = 100000
DAILY_LOSS_PERCENT = 5
AVAILABLE_PERCENT = 10
BALANCE_STEP = 1000
LOT_PER_STEP = 0.01

[guard]
ENABLED = true
CUT_LOSS_PERCENT = 2.5
INTERVAL_SECONDS = 60
MIN_AGE_SECONDS = 300
SPREAD_FACTOR = 2
NEWS_BLACKOUT = true
NEWS_BLACKOUT_MINUTES = 30
"""


@pytest.fixture(autouse=True)
def clean_config_cache():
    reset_config()
    yield
    reset_config()


def _write(tmp_path, content):
    path = tmp_path / "config.ini"
    path.write_text(content, encoding="utf-8")
    return str(path)


def test_valid_config_loads(tmp_path):
    cfg = load_config(_write(tmp_path, VALID_CONFIG))
    assert cfg['telegram']['SESSION_NAME'] == 'test_session'


def test_config_is_cached(tmp_path):
    path = _write(tmp_path, VALID_CONFIG)
    assert load_config(path) is load_config(path)


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="config.example.ini"):
        load_config(str(tmp_path / "missing.ini"))


def test_missing_key_raises_with_key_name(tmp_path):
    broken = VALID_CONFIG.replace("SESSION_NAME = test_session\n", "")
    with pytest.raises(ValueError, match=r"\[telegram\] SESSION_NAME"):
        load_config(_write(tmp_path, broken))


def test_empty_value_raises(tmp_path):
    broken = VALID_CONFIG.replace("CHANNEL_ENTITY = @canale", "CHANNEL_ENTITY = ")
    with pytest.raises(ValueError, match=r"\[telegram\] CHANNEL_ENTITY"):
        load_config(_write(tmp_path, broken))


def test_missing_section_lists_all_keys(tmp_path):
    no_telegram = "[mt5]\nTERMINAL_PATH =\n"
    with pytest.raises(ValueError, match=r"\[telegram\] YOUR_API_ID"):
        load_config(_write(tmp_path, no_telegram))


def test_mt5_settings_are_optional(tmp_path):
    # La sezione [mt5] può mancare del tutto (config in stile v1): default vuoti
    no_mt5 = VALID_CONFIG.replace("[mt5]\nTERMINAL_PATH =\nSYMBOL_SUFFIX = .m\n\n", "")
    cfg = load_config(_write(tmp_path, no_mt5))
    assert get_mt5_setting(cfg, 'TERMINAL_PATH') == ''
    assert get_mt5_setting(cfg, 'SYMBOL_SUFFIX', default='') == ''


def test_missing_risk_key_raises(tmp_path):
    broken = VALID_CONFIG.replace("LOT_PER_STEP = 0.01\n", "")
    with pytest.raises(ValueError, match=r"\[risk\] LOT_PER_STEP"):
        load_config(_write(tmp_path, broken))


def test_missing_guard_key_raises(tmp_path):
    broken = VALID_CONFIG.replace("SPREAD_FACTOR = 2\n", "")
    with pytest.raises(ValueError, match=r"\[guard\] SPREAD_FACTOR"):
        load_config(_write(tmp_path, broken))


def test_mt5_settings_read_when_present(tmp_path):
    cfg = load_config(_write(tmp_path, VALID_CONFIG))
    assert get_mt5_setting(cfg, 'SYMBOL_SUFFIX') == '.m'


def test_required_keys_match_example_config():
    # Le chiavi richieste devono esistere in config.example.ini (documentazione viva)
    import configparser
    example = configparser.ConfigParser()
    assert example.read("config.example.ini", encoding="utf-8")
    for section, keys in config_module.REQUIRED_KEYS.items():
        for key in keys:
            assert example.has_option(section, key), f"[{section}] {key} manca in config.example.ini"
