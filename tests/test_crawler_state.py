from crawler.crawler_state import (
    load_last_message_id,
    save_last_message_id,
    load_initial_deposit,
    save_initial_deposit,
)


def test_roundtrip(tmp_path):
    path = str(tmp_path / "crawler_state.json")
    save_last_message_id(12345, path=path)
    assert load_last_message_id(path=path) == 12345


def test_overwrite(tmp_path):
    path = str(tmp_path / "crawler_state.json")
    save_last_message_id(1, path=path)
    save_last_message_id(2, path=path)
    assert load_last_message_id(path=path) == 2


def test_missing_file_returns_none(tmp_path):
    assert load_last_message_id(path=str(tmp_path / "missing.json")) is None


def test_corrupted_file_returns_none(tmp_path):
    path = tmp_path / "crawler_state.json"
    path.write_text("{non è json", encoding="utf-8")
    assert load_last_message_id(path=str(path)) is None


def test_empty_state_returns_none(tmp_path):
    path = tmp_path / "crawler_state.json"
    path.write_text("{}", encoding="utf-8")
    assert load_last_message_id(path=str(path)) is None


def test_initial_deposit_roundtrip(tmp_path):
    path = str(tmp_path / "crawler_state.json")
    save_initial_deposit(100000.0, path=path)
    assert load_initial_deposit(path=path) == 100000.0


def test_initial_deposit_missing_returns_none(tmp_path):
    path = str(tmp_path / "crawler_state.json")
    save_last_message_id(42, path=path)
    assert load_initial_deposit(path=path) is None


def test_state_keys_coexist(tmp_path):
    # Le due chiavi non devono sovrascriversi a vicenda
    path = str(tmp_path / "crawler_state.json")
    save_last_message_id(42, path=path)
    save_initial_deposit(100000.0, path=path)
    save_last_message_id(43, path=path)
    assert load_last_message_id(path=path) == 43
    assert load_initial_deposit(path=path) == 100000.0


def test_legacy_state_file_still_readable(tmp_path):
    # File di stato scritto dalle versioni precedenti (solo last_message_id)
    path = tmp_path / "crawler_state.json"
    path.write_text('{"last_message_id": 341}', encoding="utf-8")
    assert load_last_message_id(path=str(path)) == 341
    assert load_initial_deposit(path=str(path)) is None
