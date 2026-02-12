from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
from services.base.scheduler_service import SchedulerService
from services.kis.kis_ws_service import kis_ws_service
from routers import analysis, market, alerts, portfolio, reports, trading
import os
import asyncio
from services.strategy.trading_strategy_service import TradingStrategyService # 추가
from services.notification.alert_service import AlertService

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 앱 시작 시
    AlertService.send_slack_alert("🚀 [시스템 알림] Sean's Stock Advisor 서버가 시작되었습니다. 실시간 감시 및 매매 전략 가동을 시작합니다.")
    
    # 스케줄러 실행 (웹소켓 서비스 포함)
    SchedulerService.start()
    
    yield
    
    # 앱 종료 시
    AlertService.send_slack_alert("🛑 [시스템 알림] 서버가 종료되었습니다. 모든 실시간 감시 및 스케줄러가 중단됩니다.")

app = FastAPI(
    title="Sean's Stock Advisor", 
    description="한국투자증권(KIS) API 및 WebSocket 기반 주식 분석 및 알림 API",
    version="2.0.0",
    lifespan=lifespan
)

# 정적 파일 서빙
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/", response_class=FileResponse)
def serve_dashboard():
    """대시보드 메인 페이지"""
    index_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "Welcome to Sean's Stock Advisor API. Use /docs for documentation."}

# 라우터 등록
app.include_router(analysis.router, prefix="/api")
app.include_router(market.router, prefix="/api")
app.include_router(alerts.router, prefix="/api")
app.include_router(portfolio.router, prefix="/api")
app.include_router(reports.router, prefix="/api")
app.include_router(trading.router, prefix="/api")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
