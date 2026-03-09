import logging
from typing import Optional
from utils.logger import get_logger
from models.schemas import AnalyzedFinancialMetrics

logger = get_logger("financial_analyzer")


class FinancialAnalyzer:
    """
    Helper class that analyzes raw collected data to produce financial metrics.
    """

    @staticmethod
    def analyze_domestic_metrics(raw_data) -> Optional[AnalyzedFinancialMetrics]:
        """
        Analyze domestic stock raw data and return standard metrics (based on FHKST01010100).
        raw_data: KisFinancialsResponse DTO or dict with output/raw keys.
        """
        output = (
            getattr(raw_data, "output", None)
            if hasattr(raw_data, "output") else None
        ) or (raw_data.get("output") if isinstance(raw_data, dict) else None) or (
            raw_data.get("raw") if isinstance(raw_data, dict) else None
        ) or {}
        if not output:
            return None

        try:
            prpr = float(output.get("stck_prpr", 0) or 0)
            lstn = float(output.get("lstn_stkn", 0) or 0)
            return AnalyzedFinancialMetrics(
                per=float(output.get("per", 0) or 0),
                pbr=float(output.get("pbr", 0) or 0),
                roe=0.0,
                eps=float(output.get("eps", 0) or 0),
                bps=float(output.get("bps", 0) or 0),
                dividend_yield=0.0,
                current_price=prpr,
                market_cap=lstn * prpr,
            )
        except (ValueError, TypeError):
            return None

    @staticmethod
    def analyze_overseas_metrics(raw_data) -> Optional[AnalyzedFinancialMetrics]:
        """
        Analyze overseas stock raw data and return standard metrics (based on HHDFS70200200).
        raw_data: KisFinancialsResponse DTO or dict with output/raw keys.
        """
        output = (
            getattr(raw_data, "output", None)
            if hasattr(raw_data, "output") else None
        ) or (raw_data.get("output") if isinstance(raw_data, dict) else None) or (
            raw_data.get("raw") if isinstance(raw_data, dict) else None
        ) or {}
        if not output:
            return None

        try:
            return AnalyzedFinancialMetrics(
                per=float(output.get("per", 0) or 0),
                pbr=float(output.get("pbr", 0) or 0),
                roe=float(output.get("roe", 0) or 0),
                eps=float(output.get("eps", 0) or 0),
                bps=float(output.get("bps", 0) or 0),
                dividend_yield=float(output.get("yield", 0) or 0),
                current_price=float(output.get("last", 0) or 0),
                market_cap=float(output.get("tomv", 0) or 0),
            )
        except (ValueError, TypeError):
            return None

    @staticmethod
    def analyze_dcf_inputs(domestic_data: dict = None, overseas_data: dict = None) -> dict:
        """
        Extract input data for DCF calculation.
        - Extracts as much as possible from KIS quotation data.
        """
        result = {
            "fcf_per_share": None,
            "beta": 1.0,
            "growth_rate": 0.05
        }
        
        if domestic_data:
            output = domestic_data.get('output', {})
            # For domestic stocks, use EPS as FCF proxy (simplified),
            # Fallback in case the actual financial statement API is unavailable
            result["fcf_per_share"] = float(output.get('eps', 0) or 0) 
            
        if overseas_data:
            output = overseas_data.get('output', {})
            result["fcf_per_share"] = float(output.get('eps', 0) or 0)
            
        return result
