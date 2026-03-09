import logging
from typing import Optional, List, Tuple
from utils.logger import get_logger

logger = get_logger("dcf_analyzer")


def _get_dcf_config() -> dict:
    """Retrieve DCF constants from Config/Settings. Uses Config defaults if absent."""
    from services.config.settings_service import SettingsService
    return {
        "equity_risk_premium": SettingsService.get_float("DCF_EQUITY_RISK_PREMIUM", 0.055),
        "discount_rate_floor": SettingsService.get_float("DCF_DISCOUNT_RATE_FLOOR", 0.06),
        "discount_rate_ceil": SettingsService.get_float("DCF_DISCOUNT_RATE_CEIL", 0.15),
        "default_discount_rate": SettingsService.get_float("DCF_DEFAULT_DISCOUNT_RATE", 0.10),
        "stage1_years": SettingsService.get_int("DCF_STAGE1_YEARS", 10),
    }


class DcfAnalyzer:
    """
    DCF (Discounted Cash Flow) calculation helper class.
    """

    @staticmethod
    def _validate_fcf(fcf_per_share: Optional[float]) -> bool:
        """FCF validation: must be positive."""
        return fcf_per_share is not None and fcf_per_share > 0

    @staticmethod
    def _compute_discount_rate(
        risk_free_rate: float,
        beta: float,
        manual_discount: Optional[float] = None,
        equity_risk_premium: float = 0.055,
        discount_rate_floor: float = 0.06,
        discount_rate_ceil: float = 0.15,
        default_discount_rate: float = 0.10,
    ) -> float:
        """
        CAPM-based discount rate calculation. Manual discount rate takes priority if provided.
        Discount rate is clamped per config bounds.
        """
        if manual_discount is not None:
            rate = manual_discount
        elif beta:
            rate = risk_free_rate + (beta * equity_risk_premium)
        else:
            rate = default_discount_rate
        return max(discount_rate_floor, min(discount_rate_ceil, rate))

    @staticmethod
    def _compute_stage1_discounted_fcfs(
        fcf_per_share: float,
        growth_rate: float,
        terminal_growth: float,
        discount_rate: float,
        years: int = 10,
    ) -> Tuple[List[float], float]:
        """
        Stage 1: Calculate yearly discounted FCF for the high-growth period.
        Returns: (list of discounted FCFs, FCF at end of year 10)
        """
        future_fcf: List[float] = []
        current_fcf = fcf_per_share
        for i in range(1, years + 1):
            year_growth = growth_rate - (growth_rate - terminal_growth) * (i / years)
            current_fcf = current_fcf * (1 + year_growth)
            discounted_fcf = current_fcf / ((1 + discount_rate) ** i)
            future_fcf.append(discounted_fcf)
        return future_fcf, current_fcf

    @staticmethod
    def _compute_discounted_terminal_value(
        final_fcf: float,
        terminal_growth: float,
        discount_rate: float,
        years: int = 10,
    ) -> float:
        """
        Stage 2: Calculate and discount terminal value.
        Formula: (Final FCF * (1 + g)) / (r - g), discounted by r^years.
        """
        terminal_value = (final_fcf * (1 + terminal_growth)) / (
            discount_rate - terminal_growth
        )
        return terminal_value / ((1 + discount_rate) ** years)

    @staticmethod
    def calculate_fair_value(
        fcf_per_share: float,
        growth_rate: float,
        beta: float,
        risk_free_rate: float = 0.04,
        terminal_growth: float = 0.03,
        manual_discount: Optional[float] = None,
    ) -> dict:
        """
        Calculate fair value using a two-stage growth model.
        (Migrated from DcfService logic to this helper)
        """
        if not DcfAnalyzer._validate_fcf(fcf_per_share):
            return {"value": 0.0, "error": "Invalid FCF"}

        cfg = _get_dcf_config()
        discount_rate = DcfAnalyzer._compute_discount_rate(
            risk_free_rate,
            beta,
            manual_discount,
            equity_risk_premium=cfg["equity_risk_premium"],
            discount_rate_floor=cfg["discount_rate_floor"],
            discount_rate_ceil=cfg["discount_rate_ceil"],
            default_discount_rate=cfg["default_discount_rate"],
        )
        years = cfg["stage1_years"]
        future_fcf, final_fcf = DcfAnalyzer._compute_stage1_discounted_fcfs(
            fcf_per_share, growth_rate, terminal_growth, discount_rate, years=years
        )
        discounted_terminal = DcfAnalyzer._compute_discounted_terminal_value(
            final_fcf, terminal_growth, discount_rate, years=years
        )
        fair_value = sum(future_fcf) + discounted_terminal

        return {
            "value": round(fair_value, 2),
            "discount_rate": round(discount_rate, 4),
            "growth_rate": round(growth_rate, 4),
        }
