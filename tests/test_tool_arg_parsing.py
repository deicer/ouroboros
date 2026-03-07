import pytest

from ouroboros.tool_args import parse_tool_call_arguments


def test_parse_tool_call_arguments_accepts_trailing_text():
    parsed = parse_tool_call_arguments('{"dir":"."} trailing analysis')
    assert parsed == {"dir": "."}


def test_parse_tool_call_arguments_accepts_fenced_json():
    parsed = parse_tool_call_arguments("```json\n{\"path\":\"README.md\"}\n```")
    assert parsed == {"path": "README.md"}


def test_parse_tool_call_arguments_accepts_double_encoded_json():
    parsed = parse_tool_call_arguments('"{\\"cmd\\":[\\"pwd\\"]}"')
    assert parsed == {"cmd": ["pwd"]}


def test_parse_tool_call_arguments_rejects_non_object_json():
    with pytest.raises(ValueError):
        parse_tool_call_arguments('"just text"')
