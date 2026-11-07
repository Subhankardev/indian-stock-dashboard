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


# --------------------------------------------------------- token cleaning -- #
REAL = "eyJ0eXAi.eyJzdWIi.UV-vFHLs_kUw"


@pytest.mark.parametrize("pasted", [
    REAL,
    "​".join(REAL[i:i + 5] for i in range(0, len(REAL), 5)),  # zero-width spaces from a chat app
    "­".join([REAL[:10], REAL[10:]]),                         # soft hyphen
    f"'{REAL}",                                                     # unbalanced quote
    f"‘{REAL}’",                                          # curly quotes
    f"  {REAL[:12]}\n{REAL[12:]}  ",                                # wrapped onto two lines
    "﻿" + REAL,                                                # BOM
])
def test_clean_token_removes_copy_paste_junk(pasted):
    assert config.clean_token(pasted) == REAL


def test_clean_token_empty_and_problem_detection():
    assert config.clean_token("  '' ") is None and config.clean_token(None) is None
    assert config.token_problem(REAL) is None
    assert "U+00E9" in config.token_problem("tokén")


def test_tokens_from_env_file_are_cleaned_and_reported(tmp_path, clean):
    import os
    dirty = "​".join(REAL[i:i + 4] for i in range(0, len(REAL), 4))
    p = tmp_path / ".env"
    p.write_text(f"UPSTOX_ACCESS_TOKEN='{dirty}\nTELEGRAM_CHAT_ID= 7844852392 \n", encoding="utf-8")
    clean.setattr(config, "_cleaned", {})
    config.load_settings(p)
    assert os.environ["UPSTOX_ACCESS_TOKEN"] == REAL and os.environ["TELEGRAM_CHAT_ID"] == "7844852392"
    s = config.describe()["settings"]["UPSTOX_ACCESS_TOKEN"]
    assert "fixed automatically" in s["note"] and "problem" not in s


def test_upstox_client_cleans_token_and_refuses_non_ascii():
    from app.upstox_data import UpstoxClient, UpstoxError
    assert UpstoxClient(access_token="​" + REAL + "​").token == REAL
    with pytest.raises(UpstoxError, match="non-ASCII"):
        UpstoxClient(access_token="tokén")._headers()
