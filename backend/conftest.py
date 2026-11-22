import os

# Never load the developer's real backend/.env (with live tokens) into tests.
# Must run before any test module imports app.main.
os.environ["APP_SKIP_DOTENV"] = "1"

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """No test may reach Telegram or touch the real intel database."""
    for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_ALERT_KINDS"):
        monkeypatch.delenv(var, raising=False)  # never message a real chat from tests
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'intel_test.db'}")
    from intel import db
    db.reset_engines()
    yield
    db.reset_engines()
