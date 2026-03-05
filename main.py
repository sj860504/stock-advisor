from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, Response
from contextlib import asynccontextmanager
from services.base.scheduler_service import SchedulerService
from services.kis.kis_ws_service import kis_ws_service
from routers import analysis, market, alerts, portfolio, reports, trading, auth as auth_router, logs as logs_router
from routers.auth import verify_token
import os
import asyncio
from services.strategy.trading_strategy_service import TradingStrategyService # 추가
from services.notification.alert_service import AlertService
from services.trading.portfolio_service import PortfolioService

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """애플리케이션 lifespan 이벤트 핸들러.

    Startup:
        - Slack 서버 시작 알림 전송
        - 스케줄러 서비스 시작 (웹소켓 포함)
        - KIS 포트폴리오 동기화 및 현황 Slack 알림

    Shutdown:
        - Slack 서버 종료 알림 전송
    """
    # 앱 시작 시
    AlertService.send_slack_alert("🚀 [시스템 알림] Sean's Stock Advisor 서버가 시작되었습니다. 실시간 감시 및 매매 전략 가동을 시작합니다.")
    
    # 스케줄러 실행 (웹소켓 서비스 포함)
    SchedulerService.start()

    # 포트폴리오 현황 알림
    try:
        user_id = "sean"
        PortfolioService.sync_with_kis(user_id)
        holdings = PortfolioService.load_portfolio(user_id)
        summary = PortfolioService.get_last_balance_summary()
        cash = PortfolioService.load_cash(user_id)
        from services.notification.report_service import ReportService
        from services.market.market_data_service import MarketDataService
        states = MarketDataService.get_all_states()
        msg = ReportService.format_portfolio_report(holdings, cash, states, summary)
        AlertService.send_slack_alert(msg)
    except Exception as e:
        AlertService.send_slack_alert(f"⚠️ 포트폴리오 알림 실패: {e}")
    
    yield
    
    # 앱 종료 시
    AlertService.send_slack_alert("🛑 [시스템 알림] 서버가 종료되었습니다. 모든 실시간 감시 및 스케줄러가 중단됩니다.")

app = FastAPI(
    title="Sean's Stock Advisor",
    description="한국투자증권(KIS) API 및 WebSocket 기반 주식 분석 및 알림 API",
    version="2.0.0",
    lifespan=lifespan
)

# ── 인증 미들웨어 ────────────────────────────────────────────────
_PUBLIC_PATHS = {"/api/auth/login", "/api/auth/verify"}

@app.middleware("http")
async def auth_middleware(request: Request, call_next) -> Response:
    """HTTP 요청에 대한 인증 미들웨어.

    처리 흐름:
        1. /api/* 가 아닌 경로(정적 파일, 루트 등)는 인증 없이 통과
        2. _PUBLIC_PATHS에 포함된 공개 엔드포인트는 인증 없이 통과
        3. Authorization 헤더에서 Bearer 토큰을 추출하여 검증
        4. 토큰이 없거나 유효하지 않으면 401 Unauthorized 응답 반환
    """
    path = request.url.path
    # /api/* 가 아닌 경로(정적 파일, 루트)는 통과
    if not path.startswith("/api/"):
        return await call_next(request)
    # 공개 엔드포인트 통과
    if path in _PUBLIC_PATHS:
        return await call_next(request)
    # Authorization 헤더 검증
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse(status_code=401, content={"detail": "인증이 필요합니다."})
    token = auth_header[7:]
    try:
        verify_token(token)
    except ValueError as e:
        return JSONResponse(status_code=401, content={"detail": str(e)})
    return await call_next(request)

# 정적 파일 서빙
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/", response_class=FileResponse)
def serve_dashboard() -> FileResponse | dict:
    """대시보드 메인 페이지"""
    index_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "Welcome to Sean's Stock Advisor API. Use /docs for documentation."}

# 라우터 등록
app.include_router(auth_router.router, prefix="/api")
app.include_router(analysis.router, prefix="/api")
app.include_router(market.router, prefix="/api")
app.include_router(alerts.router, prefix="/api")
app.include_router(portfolio.router, prefix="/api")
app.include_router(reports.router, prefix="/api")
app.include_router(trading.router, prefix="/api")
app.include_router(logs_router.router, prefix="/api")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)