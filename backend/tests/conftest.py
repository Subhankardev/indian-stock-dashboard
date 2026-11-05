import pytest

from app.alerts import routes as alert_routes


@pytest.fixture(autouse=True)
def isolated_alerts(tmp_path, monkeypatch):
    """No test may touch the real alert file, start the background scanner or reach Telegram."""
    monkeypatch.setenv("ALERTS_FILE", str(tmp_path / "alerts.json"))
    monkeypatch.setenv("ALERTS_ENABLED", "0")
    for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_ALERT_KINDS"):
        monkeypatch.delenv(var, raising=False)  # never message a real chat from tests
    alert_routes.reset_scanner(None)
    yield
    alert_routes.reset_scanner(None)
