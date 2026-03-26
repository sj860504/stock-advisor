from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from contextlib import asynccontextmanager
from services.base.scheduler_service import SchedulerService
from services.kis.kis_ws_service import kis_ws_service
from routers import analysis, market, alerts, portfolio, reports, trading, auth as auth_router, logs as logs_router, watchlist as watchlist_router
from routers.auth import verify_token
import os
import asyncio
from services.strategy.trading_strategy_service import TradingStrategyService
from services.notification.alert_service import AlertService
from services.trading.portfolio_service import PortfolioService

@asynccontextmanager
async def lifespan(app: FastAPI):
    # On app startup
    AlertService.send_slack_alert("🚀 [System Alert] Sean's Stock Advisor server has started. Real-time monitoring and trading strategy are now active.")
    
    # Start scheduler (includes WebSocket service)
    SchedulerService.start()

    # Portfolio status notification
    try:
        user_id = "sean"
        PortfolioService.sync_with_kis(user_id)
        holdings = PortfolioService.load_portfolio(user_id)
        summary = PortfolioService.get_last_balance_summary()
        cash = PortfolioService.load_cash(user_id)
        from services.notification.report_service import ReportService
        from services.market.market_data_service import MarketDataService
        from services.market.market_hour_service import MarketHourService
        states = MarketDataService.get_all_states()
        is_kr_open = MarketHourService.is_kr_market_open()
        is_us_open = MarketHourService.is_us_market_open()
        msg = ReportService.format_portfolio_report(holdings, cash, states, summary, show_kr=is_kr_open, show_us=is_us_open)
        AlertService.send_slack_alert(msg)
    except Exception as e:
        AlertService.send_slack_alert(f"⚠️ Portfolio notification failed: {e}")
    
    yield
    
    # On app shutdown
    AlertService.send_slack_alert("🛑 [System Alert] Server has been shut down. All real-time monitoring and schedulers have stopped.")

app = FastAPI(
    title="Sean's Stock Advisor",
    description="Stock analysis and alert API based on KIS (Korea Investment & Securities) API and WebSocket",
    version="2.0.0",
    lifespan=lifespan
)

# ── Auth middleware ────────────────────────────────────────────────
_PUBLIC_PATHS = {"/api/auth/login", "/api/auth/verify", "/api/auth/logout"}

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    # Pass through non-/api/* paths (static files, root)
    if not path.startswith("/api/"):
        return await call_next(request)
    # Pass through public endpoints
    if path in _PUBLIC_PATHS:
        return await call_next(request)
    # Read token from cookie
    token = request.cookies.get("session", "")
    if not token:
        return JSONResponse(status_code=401, content={"detail": "Authentication required."})
    try:
        verify_token(token)
    except ValueError as e:
        return JSONResponse(status_code=401, content={"detail": str(e)})
    return await call_next(request)

# Static file serving
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/", response_class=FileResponse)
def serve_dashboard():
    """Dashboard main page."""
    index_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "Welcome to Sean's Stock Advisor API. Use /docs for documentation."}

# Register routers
app.include_router(auth_router.router, prefix="/api")
app.include_router(analysis.router, prefix="/api")
app.include_router(market.router, prefix="/api")
app.include_router(alerts.router, prefix="/api")
app.include_router(portfolio.router, prefix="/api")
app.include_router(reports.router, prefix="/api")
app.include_router(trading.router, prefix="/api")
app.include_router(logs_router.router, prefix="/api")
app.include_router(watchlist_router.router, prefix="/api")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
