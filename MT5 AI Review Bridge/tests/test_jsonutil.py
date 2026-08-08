import pytest
from bridge.jsonutil import strict_json_object


def test_strict_object():
    assert strict_json_object('{"a":1}') == {"a": 1}


def test_duplicate_key_rejected():
    with pytest.raises(ValueError):
        strict_json_object('{"a":1,"a":2}')


def test_trailing_rejected():
    with pytest.raises(ValueError):
        strict_json_object('{"a":1} prose')
