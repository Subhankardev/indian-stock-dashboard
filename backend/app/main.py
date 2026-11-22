# Load backend/.env before anything reads its settings.
from app.config import describe as describe_config, load_settings

load_settings()

from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from app import upstox_data
from app.option_analytics import analyze_chain, synthetic_chain
from contextlib import asynccontextmanager

from app.live import routes as live_routes
from app import upstox_auth
from intel.api import router as intel_router


@asynccontextmanager
async def lifespan(app):
    async with live_routes.lifespan(app):
        yield


app = FastAPI(title="Institutional Intelligence & Stock Selection API", lifespan=lifespan)
app.include_router(intel_router)
app.include_router(live_routes.router)
app.include_router(upstox_auth.router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/options/{symbol}")
def option_analysis(
    symbol: str,
    expiry: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="YYYY-MM-DD; default nearest"),
    source: Literal["upstox", "synthetic"] = "upstox",
    window: int = Query(12, ge=0, le=100, description="Strikes either side of ATM in `rows` (0 = all)"),
    spot: float = Query(1000.0, gt=0, description="Spot price for source=synthetic"),
):
    """Option chain analytics: PCR, max pain, OI walls, ATM IV / straddle, OI build-ups."""
    try:
        if source == "synthetic":
            chain = synthetic_chain(symbol, spot)
        else:
            chain = upstox_data.client().option_chain(symbol, expiry)
    except upstox_data.UpstoxError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch {source} option chain for {symbol}: {exc}")
    if not chain["rows"]:
        raise HTTPException(status_code=404, detail=f"Empty option chain for {symbol} {chain.get('expiry')}")
    return analyze_chain(chain, window=window)

@app.get("/", include_in_schema=False)
def root():
    """Landing page for the API (the dashboard itself runs at http://localhost:3000)."""
    from fastapi.responses import HTMLResponse
    return HTMLResponse("""<!doctype html><meta charset="utf-8"><title>Backend running</title>
<body style="font-family:-apple-system,sans-serif;max-width:640px;margin:40px auto;line-height:1.6">
<h2>✅ Backend is running</h2>
<p>This is the API server. The dashboard runs separately at
<a href="http://localhost:3000">http://localhost:3000</a> (start it with <code>npm start</code> in <code>frontend/</code>).</p>
<ul>
<li><a href="/api/upstox/login">Log in with Upstox</a> (daily token)</li>
<li><a href="/api/upstox/status">Upstox token status</a></li>
<li><a href="/api/intel/status">Data freshness</a></li>
<li><a href="/api/config">Settings the backend sees</a> (secrets masked)</li>
<li><a href="/docs">API documentation</a></li>
</ul></body>""")


@app.get("/api/config")
def config_status():
    """Which settings the backend sees and where each came from (secrets masked)."""
    return describe_config()
