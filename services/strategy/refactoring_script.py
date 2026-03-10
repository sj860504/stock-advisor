import os

BACKUP_FILE = '/Users/a10941/workspace/007_private/003_quant/services/strategy/trading_strategy_service.py.bak'
TARGET_FILE = '/Users/a10941/workspace/007_private/003_quant/services/strategy/trading_strategy_service.py'

# 1. New _execute_trade_v2 and its helpers
CODE_EXECUTE_TRADE = """    @classmethod
    def _check_market_hours(cls, ticker: str) -> bool:
        \"\"\"시장 운영 시간 체크\"\"\"
        allow_extended = SettingsService.get_int("STRATEGY_ALLOW_EXTENDED_HOURS", 1) == 1
        return MarketHourService.is_kr_market_open(allow_extended=allow_extended) if ticker.isdigit() else MarketHourService.is_us_market_open(allow_extended=allow_extended)

    @classmethod
    def _is_cash_ratio_sufficient(cls, ticker: str, holdings: list, cash_balance: float, total_assets: float, exchange_rate: float, target_cash_ratio_kr: float, target_cash_ratio_us: float, macro: dict) -> bool:
        \"\"\"목표 현금 비중 조건 충족 여부 검사\"\"\"
        is_kr_ticker = ticker.isdigit()
        regime_status = (macro or {}).get('market_regime', {}).get('status', 'Neutral').upper()
        target_cash_ratio = target_cash_ratio_kr if is_kr_ticker else target_cash_ratio_us
        
        if target_cash_ratio is None:
            target_cash_ratio = cls._get_target_cash_ratio('KR' if is_kr_ticker else 'US', regime_status)
        
        if is_kr_ticker:
            kr_holdings = [h for h in (holdings or []) if str(h.get('ticker', '')).isdigit()]
            kr_market_value = sum(cls._get_holding_value(h) for h in kr_holdings if h.get("quantity", 0) > 0)
            kr_total = kr_market_value + cash_balance
            cash_ratio = cash_balance / kr_total if kr_total > 0 else 0
        else:
            from services.trading.portfolio_service import PortfolioService
            us_holdings = [h for h in (holdings or []) if not str(h.get('ticker', '')).isdigit()]
            us_market_value_krw = sum(cls._get_holding_value(h) * exchange_rate for h in us_holdings if h.get("quantity", 0) > 0)
            usd_cash = PortfolioService.get_usd_cash_balance()
            us_cash_krw = usd_cash * exchange_rate
            us_total = us_market_value_krw + us_cash_krw
            cash_ratio = us_cash_krw / us_total if us_total > 0 else 0
            
        return cash_ratio <= target_cash_ratio and not cls._is_panic_market(macro or {})

    @classmethod
    def _calculate_buy_quantity(cls, score: int, total_assets: float, cash_balance: float, current_price: float, exchange_rate: float, is_kr: bool) -> tuple:
        \"\"\"투자 비중에 따른 매수 수량 및 필요 소요 자금(원화) 계산\"\"\"
        per_trade_ratio = SettingsService.get_float("STRATEGY_PER_TRADE_RATIO", 0.05)
        split_count = SettingsService.get_int("STRATEGY_SPLIT_COUNT", 3)
        buy_threshold = SettingsService.get_int("STRATEGY_BUY_THRESHOLD", 75)
        
        multiplier = 2.0 if score >= 90 else (1.5 if score >= 80 else 1.0)
        target_invest_krw = total_assets * per_trade_ratio * multiplier
        one_time_invest_krw = target_invest_krw / split_count
        actual_invest_krw = min(one_time_invest_krw, cash_balance)
        
        final_price = current_price if is_kr else current_price * exchange_rate
        quantity = int(actual_invest_krw // final_price) if final_price > 0 else 0
        
        if quantity == 0 and score >= buy_threshold and cash_balance >= final_price:
            from utils.logger import get_logger
            logger = get_logger("strategy_service")
            logger.info("💡 소액 자산 보정: 최소 수량(1주) 확보를 위해 비중 상향 조정 집행")
            quantity = 1
            
        return quantity, quantity * final_price, final_price

    @classmethod
    def _send_trade_alert(cls, ticker: str, side: str, score: int, current_price: float, change_rate: float, trade_qty: int, profit_pct: float, holding: dict, executed: bool):
        meta = StockMetaService.get_stock_meta(ticker)
        name = holding.get("name") if holding and holding.get("name") else (meta.name_ko or meta.name_en or "" if meta else "")
        emoji = "🔵" if side == "buy" else "🔴"
        msg = f"{emoji} **[{side.upper()}] {ticker}, {name}, 점수: {score}, 가격: {current_price:,.2f}, 등락률: {change_rate:.2f}%, 수량: {trade_qty}주"
        if side == "sell": msg += f", 수익율: {profit_pct:.2f}%"
        if executed: AlertService.send_slack_alert(msg)

    @classmethod
    def _execute_trade_v2(
        cls, ticker: str, side: str, reason: str, profit_pct: float, is_holding: bool, score: int, current_price: float, total_assets: float, cash_balance: float, exchange_rate: float, holdings: list = None, user_id: str = "sean", holding: dict = None, macro: dict = None, target_cash_ratio_kr: float = None, target_cash_ratio_us: float = None
    ) -> bool:
        \"\"\"분할 매수/매도 실행 로직\"\"\"
        from utils.logger import get_logger
        logger = get_logger("strategy_service")
        logger.info(f"📢 시그널 [{side.upper()}] {ticker} - 사유: {reason}")
        
        if not cls._check_market_hours(ticker):
            logger.info(f"⏭️ {ticker} 시장 비개장. 주문 스킵.")
            return False

        executed = False
        trade_qty = 0
        is_kr = ticker.isdigit()
        state = MarketDataService.get_state(ticker)
        change_rate = getattr(state, 'change_rate', 0.0)

        if side == 'buy':
            if is_holding:
                add_position_below = SettingsService.get_float("STRATEGY_ADD_POSITION_BELOW", -5.0)
                if profit_pct > add_position_below:
                    logger.info(f"⏭️ {ticker} 추가매수 조건 미충족. 주문 스킵.")
                    return False

            if cls._is_cash_ratio_sufficient(ticker, holdings, cash_balance, total_assets, exchange_rate, target_cash_ratio_kr, target_cash_ratio_us, macro):
                logger.info(f"⏭️ {ticker} 현금비중 조건으로 인해 매수 스킵.")
                return False
                
            quantity, est_krw, final_price = cls._calculate_buy_quantity(score, total_assets, cash_balance, current_price, exchange_rate, is_kr)
            if quantity > 0:
                from services.trading.portfolio_service import PortfolioService
                holdings = holdings or PortfolioService.load_portfolio(user_id)
                kr_holdings = [h for h in holdings if str(h.get('ticker', '')).isdigit()]
                us_holdings = [h for h in holdings if not str(h.get('ticker', '')).isdigit()]
                kr_market_value = sum(cls._get_holding_value(h) for h in kr_holdings if h.get("quantity", 0) > 0)
                us_market_value_krw = sum(cls._get_holding_value(h) * exchange_rate for h in us_holdings if h.get("quantity", 0) > 0)
                usd_cash = PortfolioService.get_usd_cash_balance()
                kr_assets = kr_market_value + cash_balance
                us_assets_krw = us_market_value_krw + (usd_cash * exchange_rate)
                
                ok, limit_reasons = cls._passes_allocation_limits(ticker, est_krw, holdings, total_assets, cash_balance, holding, kr_assets, us_assets_krw)
                if not ok:
                    logger.info(f"⏭️ {ticker} 비중 제한 매수 스킵: {', '.join(limit_reasons)}")
                    return False

                trade_qty = quantity
                logger.info(f"⚖️ {ticker} 분할 매수 예정 ({quantity}주)")
                
                order_result = KisService.send_order(ticker, quantity, 0, "buy") if is_kr else KisService.send_overseas_order(ticker, quantity, round(float(current_price), 2), "buy")
                if order_result.get("status") == "success":
                    OrderService.record_trade(ticker, "buy", quantity, final_price, "Strategy execution", "v3_strategy")
                    executed = True
                else: logger.error(f"주문 실패: {order_result}")
            else:
                logger.warning(f"⚠️ {ticker} 잔고 부족 (필요: {final_price:,.0f}원)")
                return False

        elif side == "sell":
            from services.trading.portfolio_service import PortfolioService
            portfolio = holdings or PortfolioService.load_portfolio(user_id)
            current_holding = next((h for h in portfolio if h['ticker'] == ticker), None)
            if not current_holding: return False
            
            holding_qty = current_holding.get('quantity', 0)
            split_count = SettingsService.get_int("STRATEGY_SPLIT_COUNT", 3)
            
            if score <= 10: 
                sell_qty, msg = holding_qty, "전량 매도(손절)"
            else:
                sell_qty, msg = max(1, int(holding_qty / split_count)), "분할 매도(익절)"
            
            trade_qty = sell_qty
            order_result = KisService.send_order(ticker, sell_qty, 0, "sell") if is_kr else KisService.send_overseas_order(ticker, sell_qty, round(float(current_price), 2), "sell")
            if order_result.get("status") == "success":
                OrderService.record_trade(ticker, "sell", sell_qty, current_price, msg, "v3_strategy")
                executed = True
            else: logger.error(f"주문 실패: {order_result}")

        cls._send_trade_alert(ticker, side, score, current_price, change_rate, trade_qty, profit_pct, holding, executed)
        return executed
"""

# 2. New run_strategy and its helpers
CODE_RUN_STRATEGY = """    @classmethod
    def _update_target_universe(cls, user_id: str) -> set:
        \"\"\"Top 100 변경 감지 및 유니버스 정리\"\"\"
        from utils.logger import get_logger
        logger = get_logger("strategy_service")
        def _norm_ticker(t):
            t = str(t or "").strip().upper()
            if not t: return ""
            if t.isdigit() and len(t) < 6: t = t.zfill(6)
            return t
        
        kr_tickers = [_norm_ticker(t) for t in DataService.get_top_krx_tickers(limit=100)]
        us_tickers = [_norm_ticker(t) for t in DataService.get_top_us_tickers(limit=100)]
        portfolio = PortfolioService.load_portfolio(user_id)
        holdings = [_norm_ticker(h.get('ticker')) for h in portfolio]
        
        kr_holdings = [t for t in holdings if t and t.isdigit() and len(t) == 6]
        us_holdings = [t for t in holdings if t and t.isalpha()]
        
        all_kr = list(set([t for t in kr_tickers if t and t.isdigit() and len(t) == 6] + kr_holdings))
        all_us = list(set([t for t in us_tickers if t and t.isalpha()] + us_holdings))
        target_universe = set(all_kr + all_us)
        
        MarketDataService.prune_states(target_universe)
        logger.info(f"🧹 Top 100 변경 감지: 현재 유니버스 {len(target_universe)}개 (KR={len(all_kr)}, US={len(all_us)})")
        return target_universe

    @classmethod
    def _calculate_total_assets(cls, holdings: list, cash_balance: float, macro_data: dict) -> tuple:
        \"\"\"총 자산 및 시장 국면별 현금 비중 목표 계산\"\"\"
        from utils.logger import get_logger
        logger = get_logger("strategy_service")
        usd_cash = PortfolioService.get_usd_cash_balance()
        exchange_rate = MacroService.get_exchange_rate()
        
        kr_holdings = [h for h in holdings if str(h.get('ticker', '')).isdigit()]
        us_holdings = [h for h in holdings if not str(h.get('ticker', '')).isdigit()]
        
        kr_market_value = sum(h.get('current_price', 0) * h.get('quantity', 0) for h in kr_holdings)
        us_market_value_usd = sum(h.get('current_price', 0) * h.get('quantity', 0) for h in us_holdings)
        us_market_value_krw = us_market_value_usd * exchange_rate
        usd_cash_krw = usd_cash * exchange_rate
        
        total_assets = kr_market_value + us_market_value_krw + cash_balance + usd_cash_krw
        
        regime_status = macro_data.get('market_regime', {}).get('status', 'Neutral').upper()
        target_cash_kr = cls._get_target_cash_ratio('KR', regime_status)
        target_cash_us = cls._get_target_cash_ratio('US', regime_status)
        logger.info(f"💰 시장 국면: {regime_status} → 한국 현금비중 목표: {target_cash_kr:.1%}, 미국 현금비중 목표: {target_cash_us:.1%}")
        
        return total_assets, target_cash_kr, target_cash_us

    @classmethod
    def _collect_trading_signals(cls, holdings: list, macro_data: dict, user_state: dict, total_assets: float, cash_balance: float, target_cash_kr: float, target_cash_us: float) -> list:
        \"\"\"시장 상태를 확인하고 유효한 매매 시그널을 수집\"\"\"
        from utils.logger import get_logger
        logger = get_logger("strategy_service")
        allow_extended = SettingsService.get_int("STRATEGY_ALLOW_EXTENDED_HOURS", 1) == 1
        is_kr_open = MarketHourService.is_kr_market_open(allow_extended=allow_extended)
        is_us_open = MarketHourService.is_us_market_open(allow_extended=allow_extended)
        
        analyze_kr = not is_us_open
        analyze_us = not is_kr_open
        logger.info(f"📊 시장 상태: KR개장={is_kr_open}, US개장={is_us_open} → KR분석={analyze_kr}, US분석={analyze_us}")
        
        all_states = MarketDataService.get_all_states()
        prepared_signals = []
        
        for ticker, ticker_state in list(all_states.items()):
            is_kr_ticker = ticker.isdigit()
            if (is_kr_ticker and not analyze_kr) or (not is_kr_ticker and not analyze_us):
                continue
            if not getattr(ticker_state, 'is_ready', False):
                continue
            
            holding = next((h for h in holdings if h['ticker'] == ticker), None)
            market_cash_ratio = target_cash_kr if is_kr_ticker else target_cash_us
            score, reasons = cls.calculate_score(ticker, ticker_state, holding, macro_data, user_state, total_assets, cash_balance, market_cash_ratio=market_cash_ratio)
            
            prepared_signals.append({"ticker": ticker, "state": ticker_state, "holding": holding, "score": score, "reasons": reasons})
            
        logger.info(f"📊 Signal collection complete. {len(prepared_signals)} stocks ready.")
        return prepared_signals

    @classmethod
    def _execute_collected_signals(cls, user_id: str, prepared_signals: list, holdings: list, total_assets: float, cash_balance: float, target_cash_kr: float, target_cash_us: float, macro_data: dict) -> bool:
        \"\"\"수집된 시그널을 기반으로 실제 주문 집행\"\"\"
        from utils.logger import get_logger
        logger = get_logger("strategy_service")
        buy_max = SettingsService.get_int("STRATEGY_BUY_THRESHOLD_MAX", 30)
        sell_min = SettingsService.get_int("STRATEGY_SELL_THRESHOLD_MIN", 70)
        take_profit_pct = SettingsService.get_float("STRATEGY_TAKE_PROFIT_PCT", 3.0)
        exchange_rate = MacroService.get_exchange_rate()
        
        trade_executed = False
        for sig in prepared_signals:
            ticker, state, holding = sig['ticker'], sig['state'], sig['holding']
            score, reasons = sig['score'], sig['reasons']
            reason_str = ", ".join(reasons)
            stock_name = getattr(state, "name", "") or (holding.get("name") if holding else "")
            logger.info(f"🔍 Evaluated {ticker} ({stock_name}): Score={score}, RSI={getattr(state, 'rsi', 0):.1f}, Reasons=[{reason_str}]")
            
            profit_pct = 0.0
            if holding:
                buy_price = getattr(holding, 'buy_price', 0) if not isinstance(holding, dict) else holding.get('buy_price', 0)
                ref_price = getattr(holding, 'current_price', 0) if not isinstance(holding, dict) else float(holding.get("current_price") or getattr(state, 'current_price', 0))
                if buy_price > 0: profit_pct = (ref_price - buy_price) / buy_price * 100

            # 익절 우선
            if holding and profit_pct >= take_profit_pct:
                executed = cls._execute_trade_v2(ticker, "sell", f"익절권({profit_pct:.2f}%)", profit_pct, True, score, getattr(state, 'current_price', 0), total_assets, cash_balance, exchange_rate, holdings=holdings, user_id=user_id, holding=holding, macro=macro_data, target_cash_ratio_kr=target_cash_kr, target_cash_ratio_us=target_cash_us)
                trade_executed = trade_executed or bool(executed)
                continue

            # 매수/매도 로직
            if score <= buy_max and not holding:
                executed = cls._execute_trade_v2(ticker, "buy", f"점수 {score} [{reason_str}]", profit_pct, False, score, getattr(state, 'current_price', 0), total_assets, cash_balance, exchange_rate, holdings=holdings, user_id=user_id, holding=holding, macro=macro_data, target_cash_ratio_kr=target_cash_kr, target_cash_ratio_us=target_cash_us)
                trade_executed = trade_executed or bool(executed)
            elif score >= sell_min and holding:
                executed = cls._execute_trade_v2(ticker, "sell", f"점수 {score} [{reason_str}]", profit_pct, True, score, getattr(state, 'current_price', 0), total_assets, cash_balance, exchange_rate, holdings=holdings, user_id=user_id, holding=holding, macro=macro_data, target_cash_ratio_kr=target_cash_kr, target_cash_ratio_us=target_cash_us)
                trade_executed = trade_executed or bool(executed)
                
        return trade_executed

    @classmethod
    def _send_portfolio_report(cls, user_id: str, before_snapshot: dict):
        \"\"\"매매 전후 잔고를 비교하여 변동이 있으면 포트폴리오 리포트 전송\"\"\"
        from utils.logger import get_logger
        logger = get_logger("strategy_service")
        try:
            from services.notification.report_service import ReportService
            PortfolioService.sync_with_kis(user_id)
            latest_holdings = PortfolioService.load_portfolio(user_id)
            summary = PortfolioService.get_last_balance_summary()
            latest_cash = float(summary.get("prvs_rcdl_excc_amt") or PortfolioService.load_cash(user_id) or 0)
            
            after_snapshot = {h["ticker"]: h.get("quantity", 0) for h in latest_holdings}
            if before_snapshot == after_snapshot:
                logger.info("ℹ️ 체결 변경 없음. 포트폴리오 리포트 전송 스킵.")
                return
                
            states = MarketDataService.get_all_states()
            msg = ReportService.format_portfolio_report(latest_holdings, latest_cash, states, summary)
            AlertService.send_slack_alert(msg)
        except Exception as e:
            logger.warning(f"⚠️ 포트폴리오 리포트 전송 실패: {e}")

    @classmethod
    def run_strategy(cls, user_id: str = "sean"):
        \"\"\"전체 전략 실행 루프\"\"\"
        from utils.logger import get_logger
        logger = get_logger("strategy_service")
        if not cls.is_enabled():
            logger.debug(f"⏳ Trading Strategy is currently DISABLED. Skipping analysis.")
            return

        logger.info(f"🚀 Running Trading Strategy for {user_id}...")
        
        target_universe = cls._update_target_universe(user_id)
        
        holdings = PortfolioService.sync_with_kis(user_id)
        before_snapshot = {h["ticker"]: h.get("quantity", 0) for h in holdings}
        macro_data = MacroService.get_macro_data()
        cash_balance = PortfolioService.load_cash(user_id)
        
        state = cls._load_state()
        user_state = state.setdefault(user_id, {})
        if 'panic_locks' not in user_state: user_state['panic_locks'] = {}
        
        total_assets, target_cash_kr, target_cash_us = cls._calculate_total_assets(holdings, cash_balance, macro_data)
        
        prepared_signals = cls._collect_trading_signals(holdings, macro_data, user_state, total_assets, cash_balance, target_cash_kr, target_cash_us)
        
        trade_executed = cls._execute_collected_signals(user_id, prepared_signals, holdings, total_assets, cash_balance, target_cash_kr, target_cash_us, macro_data)
        
        try:
            tick_executed = cls._run_tick_trade(user_id, holdings, total_assets, cash_balance)
            trade_executed = trade_executed or bool(tick_executed)
        except Exception as e:
            logger.warning(f"⚠️ Tick trading process error: {e}")
            
        cls._save_state(state)
        logger.info("✅ 전략 실행 및 매매 판단 완료.")

        if trade_executed:
            cls._send_portfolio_report(user_id, before_snapshot)
"""

# 3. New _run_tick_trade and its helpers
CODE_TICK_TRADE = """    @classmethod
    def _evaluate_tick_sell_conditions(cls, ticker: str, holding: dict, state, pnl_pct: float, tp_pct: float, sl_pct: float, trade_state: dict) -> bool:
        \"\"\"틱매매 매도 조건 확인 및 실행\"\"\"
        hold_qty = int(holding.get("quantity", 0))
        if hold_qty > 0 and (pnl_pct >= tp_pct or pnl_pct <= sl_pct):
            result = KisService.send_order(ticker, hold_qty, 0, "sell")
            if result.get("status") == "success":
                reason = "Tick TP" if pnl_pct >= tp_pct else "Tick SL"
                OrderService.record_trade(ticker, "sell", hold_qty, getattr(state, 'current_price', 0), reason, "tick_strategy")
                AlertService.send_slack_alert(f"🔴 **[SELL] {ticker} 틱매매 청산 수량: {hold_qty}주 수익율: {pnl_pct:.2f}%")
                trade_state.update({"second_done": False, "last_sell_price": float(getattr(state, 'current_price', 0))})
                return True
        return False

    @classmethod
    def _evaluate_tick_buy_conditions(cls, ticker: str, tranche: float, state, holding: dict, pnl_pct: float, add_pct: float, trade_state: dict, low_1h: float, entry_pct: float) -> bool:
        \"\"\"틱매매 매수(초기/추가) 조건 확인 및 실행\"\"\"
        current_price = getattr(state, 'current_price', 0)
        qty = int(tranche // current_price) if current_price > 0 else 0
        if qty <= 0: return False

        if holding and not trade_state.get("second_done") and pnl_pct <= add_pct:
            result = KisService.send_order(ticker, qty, 0, "buy")
            if result.get("status") == "success":
                OrderService.record_trade(ticker, "buy", qty, current_price, "Tick Add", "tick_strategy")
                AlertService.send_slack_alert(f"🔵 **[BUY] {ticker} 틱매매 추가매수 수량: {qty}주")
                trade_state["second_done"] = True
                return True
        elif not holding:
            last_sell = trade_state.get("last_sell_price")
            reentry = float(last_sell) * (1 + entry_pct / 100.0) if last_sell else None
            trigger = float(current_price) <= reentry if reentry else float(current_price) <= low_1h * 1.001
            if trigger:
                result = KisService.send_order(ticker, qty, 0, "buy")
                if result.get("status") == "success":
                    reason = "Tick ReEntry" if reentry else "Tick Entry (1h low)"
                    OrderService.record_trade(ticker, "buy", qty, current_price, reason, "tick_strategy")
                    AlertService.send_slack_alert(f"🔵 **[BUY] {ticker} 틱매매 신규진입 수량: {qty}주")
                    trade_state["second_done"] = False
                    return True
        return False

    @classmethod
    def _run_tick_trade(cls, user_id: str, holdings: list, total_assets: float, cash_balance: float) -> bool:
        \"\"\"하루 1종목 틱매매 (진입/청산/유지)\"\"\"
        if SettingsService.get_int("STRATEGY_TICK_ENABLED", 0) != 1: return False
        ticker = (SettingsService.get_setting("STRATEGY_TICK_TICKER", "005930") or "").strip().upper()
        if not ticker: return False

        MarketDataService.register_ticker(ticker)
        state = MarketDataService.get_state(ticker)
        if not state or getattr(state, 'current_price', 0) <= 0: return False
        current_price = getattr(state, 'current_price', 0)

        allow_ext = SettingsService.get_int("STRATEGY_ALLOW_EXTENDED_HOURS", 1) == 1
        if (ticker.isdigit() and not MarketHourService.is_kr_market_open(allow_extended=allow_ext)) or \
           (not ticker.isdigit() and not MarketHourService.is_us_market_open(allow_extended=allow_ext)): return False

        tick_state = cls._load_state()
        user_state = tick_state.setdefault(user_id, {})
        today = datetime.now().strftime("%Y-%m-%d")
        trade_state = user_state.get("tick_trade", {"date": today, "second_done": False, "last_sell_price": None, "price_window": []})
        if trade_state.get("date") != today:
            trade_state = {"date": today, "second_done": False, "last_sell_price": None, "price_window": []}

        now_ts = datetime.now().timestamp()
        pw = [p for p in trade_state.get("price_window", []) if p[0] >= now_ts - 3600]
        pw.append([now_ts, float(current_price)])
        trade_state["price_window"] = pw
        low_1h = min((p[1] for p in pw), default=float(current_price))

        holding = next((h for h in holdings if h["ticker"] == ticker), None)
        close_min = SettingsService.get_int("STRATEGY_TICK_CLOSE_MINUTES", 5)
        
        if holding and cls._is_near_market_close(ticker, close_min):
            qty = int(holding.get("quantity", 0))
            if qty > 0 and KisService.send_order(ticker, qty, 0, "sell").get("status") == "success":
                OrderService.record_trade(ticker, "sell", qty, current_price, "Tick EOD", "tick_strategy")
                AlertService.send_slack_alert(f"🔴 **[SELL] {ticker} 틱매매 장마감 청산: {qty}주")
                trade_state.update({"second_done": False, "last_sell_price": float(current_price)})
                user_state["tick_trade"] = trade_state
                cls._save_state(tick_state)
                return True
            return False

        tranche = min(cash_balance, max(0.0, total_assets * SettingsService.get_float("STRATEGY_TICK_CASH_RATIO", 0.2))) / 2
        buy_price = float(holding.get("buy_price", 1)) if holding and float(holding.get("buy_price", 1)) > 0 else 1.0
        pnl_pct = (current_price - buy_price) / buy_price * 100 if holding else 0

        executed = cls._evaluate_tick_sell_conditions(ticker, holding, state, pnl_pct, SettingsService.get_float("STRATEGY_TICK_TAKE_PROFIT_PCT", 1.0), SettingsService.get_float("STRATEGY_TICK_STOP_LOSS_PCT", -5.0), trade_state) if holding else False
        if not executed and tranche > 0:
            executed = cls._evaluate_tick_buy_conditions(ticker, tranche, state, holding, pnl_pct, SettingsService.get_float("STRATEGY_TICK_ADD_PCT", -3.0), trade_state, low_1h, SettingsService.get_float("STRATEGY_TICK_ENTRY_PCT", -1.0))

        user_state["tick_trade"] = trade_state
        cls._save_state(tick_state)
        return executed
"""

with open(BACKUP_FILE, 'r') as f:
    lines = f.readlines()

new_lines = []
skip = False

# We replace the chunks directly. For this, we can just find exact signatures and do it.
# To be absolutely sure, we'll iterate with indices because signature formats might be tricky.

idx = 0
while idx < len(lines):
    line = lines[idx]

    # Chunk 1: _run_tick_trade
    if "@classmethod" in line and idx + 1 < len(lines) and "def _run_tick_trade" in lines[idx+1]:
        # we found _run_tick_trade start
        new_lines.append(CODE_TICK_TRADE + "\\n")
        # skip until return executed inside _run_tick_trade
        while idx < len(lines):
            if "        return executed" in lines[idx] and idx > 380 and idx < 420:
                idx += 1
                break
            idx += 1
        continue

    # Chunk 2: run_strategy
    if "@classmethod" in line and idx + 1 < len(lines) and "def run_strategy" in lines[idx+1]:
        new_lines.append(CODE_RUN_STRATEGY + "\\n")
        # skip until we see logger.warning(f"⚠️ 포트폴리오 리포트 전송 실패: {e}")
        while idx < len(lines):
            if "포트폴리오 리포트 전송 실패" in lines[idx]:
                idx += 1
                break
            idx += 1
        continue

    # Chunk 3: _execute_trade_v2
    if "@classmethod" in line and idx + 1 < len(lines) and "def _execute_trade_v2" in lines[idx+1]:
        new_lines.append(CODE_EXECUTE_TRADE + "\\n")
        # skip until return executed at the end of _execute_trade_v2
        while idx < len(lines):
            if "        return executed" in lines[idx] and idx > 1150 and idx < 1220:
                idx += 1
                break
            idx += 1
        continue

    new_lines.append(line)
    idx += 1

with open(TARGET_FILE, 'w') as f:
    f.writelines(new_lines)
print("Refactoring complete.")
