import os

replacements = {
    "from services.base.file_service": "from services.base.file_service",
    "from services.base.scheduler_service": "from services.base.scheduler_service",
    "from services.kis.kis_service": "from services.kis.kis_service",
    "from services.kis.kis_ws_service": "from services.kis.kis_ws_service",
    "from services.kis.fetch.kis_fetcher": "from services.kis.fetch.kis_fetcher",
    "from services.market.data_service": "from services.market.data_service",
    "from services.market.market_data_service": "from services.market.market_data_service",
    "from services.market.market_overview_service": "from services.market.market_overview_service",
    "from services.market.stock_meta_service": "from services.market.stock_meta_service",
    "from services.market.ticker_service": "from services.market.ticker_service",
    "from services.market.macro_service": "from services.market.macro_service",
    "from services.market.news_service": "from services.market.news_service",
    "from services.analysis.analysis_service": "from services.analysis.analysis_service",
    "from services.analysis.indicator_service": "from services.analysis.indicator_service",
    "from services.analysis.dcf_service": "from services.analysis.dcf_service",
    "from services.analysis.stock_ranking_service": "from services.analysis.stock_ranking_service",
    "from services.analysis.financial_service": "from services.analysis.financial_service",
    "from services.analysis.analyzer": "from services.analysis.analyzer",
    "from services.strategy.trading_strategy_service": "from services.strategy.trading_strategy_service",
    "from services.strategy.scanner_service": "from services.strategy.scanner_service",
    "from services.strategy.backtest_service": "from services.strategy.backtest_service",
    "from services.strategy.simulation_service": "from services.strategy.simulation_service",
    "from services.trading.execution_service": "from services.trading.execution_service",
    "from services.trading.portfolio_service": "from services.trading.portfolio_service",
    "from services.notification.alert_service": "from services.notification.alert_service",
    "from services.notification.report_service": "from services.notification.report_service"
}

def update_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    changed = False
    for old, new in replacements.items():
        if old in content:
            content = content.replace(old, new)
            changed = True
    
    if changed:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"✅ Updated: {filepath}")

def main():
    for root, dirs, files in os.walk('.'):
        for file in files:
            if file.endswith('.py'):
                update_file(os.path.join(root, file))

if __name__ == "__main__":
    main()
