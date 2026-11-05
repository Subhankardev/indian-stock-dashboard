import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app


@pytest.fixture
def clean(monkeypatch):
    monkeypatch.delenv("APP_SKIP_DOTENV", raising=False)
    for name in list(config.SETTINGS) + ["SOME_OTHER"]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "_sources", {})
    monkeypatch.setattr(config, "_loaded_from", None)
    return monkeypatch


def write(tmp_path, text):
    p = tmp_path / ".env"
    p.write_text(text)
    return p


def test_values_from_file_are_applied(tmp_path, clean):
    import os
    p = write(tmp_path, "TELEGRAM_BOT_TOKEN='123456:ABCDEFGHIJ-xyz9'\nTELEGRAM_CHAT_ID=7844852392\n"
                        "# comment\nexport DATA_SOURCE=upstox\nSOME_OTHER=1\n")
    applied = config.load_env_file(p)
    assert os.environ["TELEGRAM_CHAT_ID"] == "7844852392" and os.environ["DATA_SOURCE"] == "upstox"
    assert applied["TELEGRAM_BOT_TOKEN"] == "123456:ABCDEFGHIJ-xyz9"
    d = config.describe()["settings"]
    assert d["TELEGRAM_CHAT_ID"] == {"set": True, "value": "7844852392", "source": ".env",
                                     "help": config.SETTINGS["TELEGRAM_CHAT_ID"][1]}
    assert d["TELEGRAM_BOT_TOKEN"]["value"] == "1234…xyz9", "secrets are masked"
    assert d["UPSTOX_ACCESS_TOKEN"] == {**d["UPSTOX_ACCESS_TOKEN"], "set": False, "source": "not set"}


def test_shell_wins_and_is_reported(tmp_path, clean):
    import os
    clean.setenv("TELEGRAM_CHAT_ID", "111")
    config.load_env_file(write(tmp_path, "TELEGRAM_CHAT_ID=222\n"))
    assert os.environ["TELEGRAM_CHAT_ID"] == "111"
    assert config.describe()["settings"]["TELEGRAM_CHAT_ID"]["source"] == "shell (overrides .env)"


def test_empty_values_count_as_unset(tmp_path, clean):
    import os
    config.load_env_file(write(tmp_path, "UPSTOX_ACCESS_TOKEN=\nTELEGRAM_CHAT_ID=   \n"))
    assert "UPSTOX_ACCESS_TOKEN" not in os.environ and "TELEGRAM_CHAT_ID" not in os.environ


def test_missing_file_and_test_guard(tmp_path, clean):
    assert config.load_env_file(tmp_path / "nope.env") == {}
    clean.setenv("APP_SKIP_DOTENV", "1")
    assert config.load_env_file(write(tmp_path, "TELEGRAM_CHAT_ID=1\n")) == {}


def test_config_endpoint_never_leaks_secrets(clean):
    clean.setenv("UPSTOX_ACCESS_TOKEN", "eyJhbGciOiJIUzI1NiJ9.SECRETPART.sig1234")
    body = TestClient(app).get("/api/config").json()
    assert "SECRETPART" not in str(body)
    assert body["settings"]["UPSTOX_ACCESS_TOKEN"]["set"] is True
    assert body["settings"]["UPSTOX_ACCESS_TOKEN"]["source"] == "shell"
