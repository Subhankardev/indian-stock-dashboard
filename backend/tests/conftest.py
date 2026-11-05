import pytest

from app.alerts import routes as alert_routes


@pytest.fixture(autouse=True)
def isolated_alerts(tmp_path, monkeypatch):
    """No test may touch the real alert file or start the background scanner."""
    monkeypatch.setenv("ALERTS_FILE", str(tmp_path / "alerts.json"))
    monkeypatch.setenv("ALERTS_ENABLED", "0")
    alert_routes.reset_scanner(None)
    yield
    alert_routes.reset_scanner(None)
