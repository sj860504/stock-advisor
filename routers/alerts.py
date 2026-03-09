from fastapi import APIRouter
from services.notification.alert_service import AlertService
from models.schemas import PriceAlert, MessageResponse, TriggeredAlertsResponse, PendingAlertsResponse

router = APIRouter(
    prefix="/alerts",
    tags=["Alerts"]
)


@router.post("", response_model=MessageResponse)
def create_alert(alert: PriceAlert) -> MessageResponse:
    """Set price alert (auto-resolves ticker/stock name)."""
    AlertService.add_user_alert(alert)
    return MessageResponse(message=f"Alert set for {alert.ticker} at {alert.target_price}")


@router.get("/check", response_model=TriggeredAlertsResponse)
def check_alerts() -> TriggeredAlertsResponse:
    """Check configured alert conditions and return triggered alerts."""
    triggered = AlertService.check_user_alerts()
    return TriggeredAlertsResponse(triggered_alerts=triggered)


@router.get("/pending", response_model=PendingAlertsResponse)
def get_pending_alerts() -> PendingAlertsResponse:
    """Get/clear pending alerts (for polling)."""
    alerts = AlertService.get_pending_alerts()
    return PendingAlertsResponse(alerts=alerts)
