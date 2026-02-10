from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
from stock_advisor.services.scheduler_service import SchedulerService
from stock_advisor.routers import analysis, market, alerts, portfolio, reports
import os


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 앱 시작 시 스케줄러 실행
    SchedulerService.start()
    yield
    # 앱 종료 시 정리

app = FastAPI(
    title="Sean's Stock Advisor", 
    description="FinanceDataReader + yfinance 기반 주식 분석 및 알림 API",
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
app.include_router(analysis.router)
app.include_router(market.router)
app.include_router(alerts.router)
app.include_router(portfolio.router)
app.include_router(reports.router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
