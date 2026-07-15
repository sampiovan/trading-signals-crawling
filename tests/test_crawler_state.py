from crawler.crawler_state import load_last_message_id, save_last_message_id


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
