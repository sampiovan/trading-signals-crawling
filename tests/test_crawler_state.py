import json

from crawler import crawler_state
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


# ----- scrittura atomica -----

def test_save_leaves_no_temp_file(tmp_path):
    path = str(tmp_path / "crawler_state.json")
    save_last_message_id(7, path=path)
    assert load_last_message_id(path=path) == 7
    assert list(tmp_path.iterdir()) == [tmp_path / "crawler_state.json"]


def test_failed_write_keeps_previous_state_intact(tmp_path, monkeypatch):
    # Il dump fallisce a metà: lo stato preesistente NON deve corrompersi
    # (file corrotto = catch-up che salta in silenzio i segnali persi)
    path = str(tmp_path / "crawler_state.json")
    save_last_message_id(41, path=path)

    def broken_dump(state, f):
        f.write('{"last_message')  # scrittura parziale
        raise OSError("disco pieno")
    monkeypatch.setattr(crawler_state.json, 'dump', broken_dump)
    save_last_message_id(42, path=path)  # non solleva: best effort loggato

    monkeypatch.undo()
    assert load_last_message_id(path=path) == 41  # stato vecchio intatto
    assert list(tmp_path.iterdir()) == [tmp_path / "crawler_state.json"]  # niente .tmp residui


def test_saved_file_is_valid_json(tmp_path):
    path = tmp_path / "crawler_state.json"
    save_last_message_id(99, path=str(path))
    state = json.loads(path.read_text(encoding="utf-8"))
    assert state == {"last_message_id": 99}
