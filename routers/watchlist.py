"""Watchlist & strategy mode router."""
from fastapi import APIRouter, HTTPException

from models.schemas import (
    WatchlistResponse, WatchlistItem,
    WatchlistUpdateResponse, WatchlistItemUpdateRequest,
    StrategyModeResponse, StrategyModeRequest,
)
from repositories.watchlist_repo import WatchlistRepo
from services.base.scheduler_service import SchedulerService
from services.config.settings_service import SettingsService

router = APIRouter(tags=["watchlist"])

_VALID_MODES = {"universe", "custom"}

_MODE_COMPAT = {"top100": "universe", "watchlist": "custom"}

def normalize_mode(raw: str) -> str:
    return _MODE_COMPAT.get(raw or "universe", raw or "universe")


def _norm_ticker(ticker: str) -> str:
    t = ticker.strip().upper()
    if t.isdigit() and len(t) < 6:
        t = t.zfill(6)
    return t


# ── Watchlist CRUD ─────────────────────────────────────────────────────────

@router.get("/watchlist/{user_id}", response_model=WatchlistResponse)
def get_watchlist(user_id: str):
    items = WatchlistRepo.get_items(user_id)
    return WatchlistResponse(
        user_id=user_id,
        tickers=[WatchlistItem(
            ticker=row.ticker, 
            added_at=row.added_at,
            target_buy_price=row.target_buy_price,
            target_sell_price=row.target_sell_price,
            memo=row.memo
        ) for row in items],
    )


@router.post("/watchlist/{user_id}/{ticker}", response_model=WatchlistUpdateResponse)
def add_watchlist_ticker(user_id: str, ticker: str):
    normalized = _norm_ticker(ticker)
    if not normalized:
        raise HTTPException(status_code=400, detail="Invalid ticker")
    added = WatchlistRepo.add_ticker(user_id, normalized)
    if added:
        SchedulerService.manage_subscriptions(force_refresh=True)
    return WatchlistUpdateResponse(
        status="added" if added else "already_exists",
        ticker=normalized,
    )


@router.patch("/watchlist/{user_id}/{ticker}", response_model=WatchlistUpdateResponse)
def update_watchlist_ticker(user_id: str, ticker: str, body: WatchlistItemUpdateRequest):
    normalized = _norm_ticker(ticker)
    if not normalized:
        raise HTTPException(status_code=400, detail="Invalid ticker")
    
    update_data = body.model_dump(exclude_unset=True)
    updated = WatchlistRepo.update_item(user_id, normalized, **update_data)
    
    return WatchlistUpdateResponse(
        status="updated" if updated else "not_found",
        ticker=normalized,
    )


@router.delete("/watchlist/{user_id}/{ticker}", response_model=WatchlistUpdateResponse)
def remove_watchlist_ticker(user_id: str, ticker: str):
    normalized = _norm_ticker(ticker)
    removed = WatchlistRepo.remove_ticker(user_id, normalized)
    if removed:
        SchedulerService.manage_subscriptions(force_refresh=True)
    return WatchlistUpdateResponse(
        status="removed" if removed else "not_found",
        ticker=normalized,
    )


# ── Strategy Mode ──────────────────────────────────────────────────────────

@router.get("/watchlist/{user_id}/mode", response_model=StrategyModeResponse)
def get_strategy_mode(user_id: str):
    kr_mode = normalize_mode(SettingsService.get_setting("kr_strategy_mode"))
    us_mode = normalize_mode(SettingsService.get_setting("us_strategy_mode"))
    return StrategyModeResponse(
        kr_strategy_mode=kr_mode,
        us_strategy_mode=us_mode,
    )


@router.put("/watchlist/{user_id}/mode", response_model=StrategyModeResponse)
def set_strategy_mode(user_id: str, body: StrategyModeRequest):
    if body.market not in ("kr", "us"):
        raise HTTPException(status_code=400, detail="market must be 'kr' or 'us'")
    
    mode = normalize_mode(body.mode)
    if mode not in _VALID_MODES:
        raise HTTPException(status_code=400, detail="mode must be 'universe' or 'custom'")
    
    # Store normalized mode (SettingsService invalidates cache so next read gets fresh value)
    key = f"{body.market}_strategy_mode"
    SettingsService.set_setting(key, mode)
    SchedulerService.manage_subscriptions(force_refresh=True)
    return StrategyModeResponse(
        kr_strategy_mode=normalize_mode(SettingsService.get_setting("kr_strategy_mode")),
        us_strategy_mode=normalize_mode(SettingsService.get_setting("us_strategy_mode")),
    )
