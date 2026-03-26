"""Watchlist & strategy mode router."""
from fastapi import APIRouter, HTTPException

from models.schemas import (
    WatchlistResponse, WatchlistItem,
    WatchlistUpdateResponse,
    StrategyModeResponse, StrategyModeRequest,
)
from repositories.watchlist_repo import WatchlistRepo
from repositories.settings_repo import SettingsRepo

router = APIRouter(tags=["watchlist"])

_VALID_MODES = {"top100", "watchlist"}


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
        tickers=[WatchlistItem(ticker=row.ticker, added_at=row.added_at) for row in items],
    )


@router.post("/watchlist/{user_id}/{ticker}", response_model=WatchlistUpdateResponse)
def add_watchlist_ticker(user_id: str, ticker: str):
    normalized = _norm_ticker(ticker)
    if not normalized:
        raise HTTPException(status_code=400, detail="Invalid ticker")
    added = WatchlistRepo.add_ticker(user_id, normalized)
    return WatchlistUpdateResponse(
        status="added" if added else "already_exists",
        ticker=normalized,
    )


@router.delete("/watchlist/{user_id}/{ticker}", response_model=WatchlistUpdateResponse)
def remove_watchlist_ticker(user_id: str, ticker: str):
    normalized = _norm_ticker(ticker)
    removed = WatchlistRepo.remove_ticker(user_id, normalized)
    return WatchlistUpdateResponse(
        status="removed" if removed else "not_found",
        ticker=normalized,
    )


# ── Strategy Mode ──────────────────────────────────────────────────────────

@router.get("/watchlist/{user_id}/mode", response_model=StrategyModeResponse)
def get_strategy_mode(user_id: str):
    return StrategyModeResponse(
        kr_strategy_mode=SettingsRepo.get("kr_strategy_mode") or "top100",
        us_strategy_mode=SettingsRepo.get("us_strategy_mode") or "top100",
    )


@router.put("/watchlist/{user_id}/mode", response_model=StrategyModeResponse)
def set_strategy_mode(user_id: str, body: StrategyModeRequest):
    if body.market not in ("kr", "us"):
        raise HTTPException(status_code=400, detail="market must be 'kr' or 'us'")
    if body.mode not in _VALID_MODES:
        raise HTTPException(status_code=400, detail="mode must be 'top100' or 'watchlist'")
    key = f"{body.market}_strategy_mode"
    SettingsRepo.set(key, body.mode, description=f"{body.market.upper()} strategy mode")
    return StrategyModeResponse(
        kr_strategy_mode=SettingsRepo.get("kr_strategy_mode") or "top100",
        us_strategy_mode=SettingsRepo.get("us_strategy_mode") or "top100",
    )
