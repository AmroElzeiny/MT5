from pathlib import Path
import pytest
from bridge.response_extract import extract_json_object_text


def test_direct_json():
    obj, text = extract_json_object_text('{"ok":true}')
    assert obj == {"ok": True}
    assert text == '{"ok":true}'


def test_whole_fenced_json():
    obj, _ = extract_json_object_text('```json\n{"ok":true}\n```')
    assert obj["ok"] is True


def test_prose_is_not_guessed():
    with pytest.raises(Exception):
        extract_json_object_text('Answer: {"ok":true}')
