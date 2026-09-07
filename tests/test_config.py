import pytest

from kobobridge.config import Config, ConfigError
from kobobridge.state import ReadingStateStore


def test_the_environment_describes_the_bridge():
    config = Config.from_env(
        {
            "KOBOBRIDGE_ABS_URL": "http://abs.local:13378/",
            "KOBOBRIDGE_ABS_TOKEN": "abc",
            "KOBOBRIDGE_PUBLIC_URL": "https://books.example.org/",
        }
    )
    assert config.abs_url == "http://abs.local:13378"
    assert config.public_url == "https://books.example.org"
    assert config.library_id is None
    assert len(config.device_token) > 20, "a generated token must not be guessable"


def test_a_missing_address_says_what_to_set():
    with pytest.raises(ConfigError) as caught:
        Config.from_env({"KOBOBRIDGE_ABS_TOKEN": "abc"})
    assert "KOBOBRIDGE_ABS_URL" in str(caught.value)


def test_a_missing_token_says_where_to_find_one():
    with pytest.raises(ConfigError) as caught:
        Config.from_env({"KOBOBRIDGE_ABS_URL": "http://abs.local"})
    assert "API token" in str(caught.value)


def test_an_address_without_a_scheme_is_refused():
    with pytest.raises(ConfigError):
        Config.from_env({"KOBOBRIDGE_ABS_URL": "abs.local", "KOBOBRIDGE_ABS_TOKEN": "abc"})


def test_two_runs_generate_two_different_tokens():
    env = {"KOBOBRIDGE_ABS_URL": "http://abs.local", "KOBOBRIDGE_ABS_TOKEN": "abc"}
    assert Config.from_env(env).device_token != Config.from_env(env).device_token


def test_a_pinned_token_survives(tmp_path):
    env = {
        "KOBOBRIDGE_ABS_URL": "http://abs.local",
        "KOBOBRIDGE_ABS_TOKEN": "abc",
        "KOBOBRIDGE_DEVICE_TOKEN": "pinned",
    }
    assert Config.from_env(env).device_token == "pinned"


def test_reading_state_survives_a_restart(tmp_path):
    path = str(tmp_path / "nested" / "reading-state.json")
    store = ReadingStateStore(path=path)
    store.put("book-uuid", {"CurrentBookmark": {"ProgressPercent": 12}})
    reopened = ReadingStateStore(path=path)
    assert reopened.get("book-uuid")["CurrentBookmark"]["ProgressPercent"] == 12


def test_a_corrupt_state_file_is_ignored_rather_than_fatal(tmp_path):
    path = tmp_path / "reading-state.json"
    path.write_text("{ not json", encoding="utf-8")
    assert len(ReadingStateStore(path=str(path))) == 0


def test_an_in_memory_store_writes_nothing(tmp_path):
    store = ReadingStateStore(path=None)
    store.put("book-uuid", {"a": 1})
    assert store.get("book-uuid") == {"a": 1}
    assert list(tmp_path.iterdir()) == []
