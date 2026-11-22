"""Institutional Intelligence research system.

Command-line tools (``python -m intel.upstox_ingest``, ``intel.daily``, ``intel.alerts`` …)
read ``backend/.env`` here, exactly as the web server does, so tokens and keys saved there
(UPSTOX_ACCESS_TOKEN, TELEGRAM_*, ANTHROPIC_API_KEY, DATABASE_URL) are seen. Values already
exported in the shell win; tests skip the file via APP_SKIP_DOTENV=1.
"""
try:
    from app.config import load_settings as _load_settings
    _load_settings()
except ImportError:  # running without the app package on the path
    pass
