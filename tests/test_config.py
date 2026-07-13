import pytest

import config as config_module
from config import load_config, reset_config


VALID_CONFIG = """
[telegram]
YOUR_API_ID = 123456
YOUR_API_HASH = abcdef0123456789
SESSION_NAME = test_session
CHANNEL_ENTITY = @canale

[paths]
MT4_FILES_FOLDER = C:\\MT4\\Files
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
    broken = VALID_CONFIG.replace("MT4_FILES_FOLDER = C:\\MT4\\Files", "MT4_FILES_FOLDER = ")
    with pytest.raises(ValueError, match=r"\[paths\] MT4_FILES_FOLDER"):
        load_config(_write(tmp_path, broken))


def test_missing_section_lists_all_keys(tmp_path):
    only_telegram = VALID_CONFIG.split("[paths]")[0]
    with pytest.raises(ValueError, match=r"\[paths\] MT4_FILES_FOLDER"):
        load_config(_write(tmp_path, only_telegram))


def test_required_keys_match_example_config():
    # Le chiavi richieste devono esistere in config.example.ini (documentazione viva)
    import configparser
    example = configparser.ConfigParser()
    assert example.read("config.example.ini", encoding="utf-8")
    for section, keys in config_module.REQUIRED_KEYS.items():
        for key in keys:
            assert example.has_option(section, key), f"[{section}] {key} manca in config.example.ini"
