from fastapi import APIRouter
from services.alert_service import AlertService
from services.ticker_service import TickerService
from models.schemas import PriceAlert

router = APIRouter(
    prefix="/alerts",
    tags=["Alerts"]
)

@router.post("")
def create_alert(alert: PriceAlert):
    """
    媛寃??뚮┝???ㅼ젙?⑸땲?? (?낅젰???곗빱/?대쫫 ?먮룞 蹂??
    """
    real_ticker = TickerService.resolve_ticker(alert.ticker)
    alert.ticker = real_ticker # 蹂?섎맂 ?곗빱濡????
    
    AlertService.add_user_alert(alert)
    return {"message": f"Alert set for {alert.ticker} at {alert.target_price}"}

@router.get("/check")
def check_alerts():
    """
    ?ㅼ젙???뚮┝ 議곌굔???뺤씤?섍퀬 ?몃━嫄곕맂 ?뚮┝??諛섑솚?⑸땲??
    (二쇨린?곸쑝濡??몄텧?섏뿬 ?뺤씤?섎뒗 ?⑸룄)
    """
    triggered = AlertService.check_user_alerts()
    return {"triggered_alerts": triggered}

@router.get("/pending")
def get_pending_alerts():
    """?湲?以묒씤 ?뚮┝ 議고쉶 諛???젣 (Polling??"""
    alerts = AlertService.get_pending_alerts()
    return {"alerts": alerts}
