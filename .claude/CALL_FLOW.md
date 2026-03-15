# Call Flow Reference

**함수 호출 관계도 — 파라미터 & 반환 타입 명시**

> 상위 → 하위 호출 순서. 반환 타입은 `->` 뒤에 표기.

---

## 1. 전략 실행 흐름 (매 1분)

```
SchedulerService.run_trading_strategy() -> None
└── TradingStrategyService.run_strategy(user_id: str) -> None
      ├── _validate_preconditions(user_id: str) -> bool
      │     └── MarketHourService.is_strategy_window_open(allow_extended: bool) -> bool
      │
      ├── _update_target_universe(user_id: str, is_kr_open: bool, is_us_open: bool) -> set[str]
      │     └── MarketDataService.prune_states(keep_tickers: set) -> None
      │
      ├── _load_macro_and_assets() -> tuple[MacroDataSnapshot, float, float, float, float, float]
      │     │     # (macro_snapshot, exchange_rate, kr_total, us_total_krw, target_cash_kr, target_cash_us)
      │     └── MacroService.get_macro_data() -> dict
      │           ├── StockMetaRepo.get_30d_avg_regime_score() -> Optional[float]
      │           └── MacroService._get_market_regime(
      │                   vix, fear_greed, economic_indicators, us_10y_yield,
      │                   historical_avg_score: Optional[float]
      │               ) -> MarketRegimeSchema
      │                 ├── _calculate_all_regime_components(...) -> RegimeComponents
      │                 │     ├── _calc_technical_20(close: pd.Series, ndx_1m_hist) -> tuple[int, dict, dict]
      │                 │     │     └── _calc_ema_raw(close, current_price: float, ema_map: dict) -> int
      │                 │     ├── _calc_vix_20(vix: float, vix_1m_chg: Optional[float]) -> int
      │                 │     ├── _calc_fng_20(fear_greed: int) -> tuple[int, bool]
      │                 │     ├── _calc_econ_20(economic_indicators) -> int
      │                 │     ├── _calc_composite_20(...) -> tuple[int, dict]
      │                 │     ├── _calc_inflation_pressure(oil_1m_ret, economic_indicators) -> tuple[int, dict]
      │                 │     ├── _calc_growth_signal(tech_detail, vix, fear_greed, econ_20) -> tuple[int, dict]
      │                 │     └── _determine_economic_phase(inflation_pressure: int, growth_signal: int) -> tuple[str, int]
      │                 └── _assemble_regime_result(
      │                         close, ema_map, technical_20, tech_detail, vix_20, fng_20,
      │                         econ_20, other_20, other_scores, vix, vix_1m_chg, us_10y_yield,
      │                         yield_spread, btc_ret, dxy_ret, gold_ret,
      │                         bear_threshold: int, economic_phase: str, phase_modifier: int,
      │                         inflation_pressure: int, growth_signal: int, inflation_detail,
      │                         oil_ret: Optional[float], extreme_fear: bool,
      │                         historical_avg_score: Optional[float]
      │                     ) -> MarketRegimeSchema
      │
      ├── _load_user_state(user_id: str) -> tuple[dict, UserState]
      │     │     # (user_state_map, user_state)
      │     └── StrategyStateRepo.load(user_id: str) -> dict
      │           # raw dict → UserState(**raw) 변환
      │
      ├── _load_and_sync_portfolio(user_id: str) -> tuple[list[HoldingSchema], float, float]
      │     ├── PortfolioService.sync_with_kis(user_id: str) -> list[dict]
      │     ├── PortfolioService.load_portfolio(user_id: str) -> list[HoldingSchema]
      │     ├── PortfolioService.load_cash(user_id: str) -> float
      │     └── PortfolioService.get_usd_cash_balance() -> float
      │
      ├── _run_signals_and_execute(
      │       user_id: str, holdings: list[HoldingSchema], kr_cash: float, usd_cash: float,
      │       macro: MacroDataSnapshot, user_state: UserState
      │   ) -> tuple[bool, set[str]]
      │     ├── SignalService._collect_trading_signals(
      │     │       holdings, macro_data, user_state, kr_total, us_total_krw,
      │     │       cash_balance, target_cash_kr, target_cash_us, usd_cash, exchange_rate
      │     │   ) -> list[SignalSchema]
      │     │     ├── _determine_analysis_markets(allow_extended: bool) -> tuple[bool, bool]
      │     │     │     └── MarketHourService.is_kr/us_market_open() -> bool
      │     │     ├── _apply_hard_gates(
      │     │     │       ticker, ticker_state, holding, cash_balance, usd_cash,
      │     │     │       kr_total, us_total_krw, target_cash_kr, target_cash_us,
      │     │     │       macro: MacroDataSnapshot, exchange_rate
      │     │     │   ) -> bool  # True=차단
      │     │     │     └── _is_fear_market_exception(
      │     │     │             macro: MacroDataSnapshot, score: int
      │     │     │         ) -> bool
      │     │     └── calculate_score(
      │     │             ticker, state, holding, macro, user_state,
      │     │             cash_balance, market_cash_ratio, market_total_krw
      │     │         ) -> tuple[int, list[str], dict]
      │     │
      │     └── PositionService._execute_collected_signals(
      │             user_id, prepared_signals: list[SignalSchema], holdings: list[HoldingSchema],
      │             kr_total, us_total_krw, cash_balance, target_cash_kr, target_cash_us,
      │             usd_cash: float, user_state: UserState
      │         ) -> tuple[bool, set[str]]
      │           ├── _load_execution_config() -> ExecutionConfig
      │           │     └── SettingsService.get_float/get_int(key, default) -> float|int
      │           ├── _sort_signals_by_priority(signals: list[SignalSchema], split_orders: dict) -> list[SignalSchema]
      │           ├── _expire_split_orders(split_orders: dict) -> None
      │           │     └── SettingsService.get_int("STRATEGY_SPLIT_EXPIRE_DAYS", 5) -> int
      │           ├── _process_single_signal(
      │           │       sig: SignalSchema, cfg: ExecutionConfig,
      │           │       kr_total, us_total_krw, cash_balance, user_id,
      │           │       split_orders, sell_split_orders, sell_cooldown,
      │           │       add_buy_cooldown, panic_locks, trailing_high: dict
      │           │   ) -> tuple[bool, Optional[str], float, float]
      │           │     │       # (executed, ticker, spent_krw, spent_usd)
      │           │     ├── _unpack_signal(
      │           │     │       sig: SignalSchema, kr_total: float, us_total_krw: float
      │           │     │   ) -> UnpackedSignal
      │           │     ├── [forced_sell=True]
      │           │     │   └── _handle_forced_sell(
      │           │     │           ticker, holding, reason_str, user_id,
      │           │     │           split_orders, sell_split_orders
      │           │     │       ) -> TradeResult
      │           │     │         └── TradeExecutorService._execute_trade_v2(
      │           │     │                 ticker, side="sell", forced_qty=holding_qty, ...
      │           │     │             ) -> TradeResult(executed, spent_krw, spent_usd)
      │           │     ├── _update_trailing_high(
      │           │     │       ticker: str, price: float, trailing_high: dict
      │           │     │   ) -> None
      │           │     ├── _handle_trailing_stop(
      │           │     │       ticker, holding, trailing_high: dict,
      │           │     │       macro, user_id
      │           │     │   ) -> Optional[TradeResult]
      │           │     │     ├── _get_trailing_stop_pct(macro) -> float  # BULL→-7.0, else→-5.0
      │           │     │     └── _handle_forced_sell(...) -> TradeResult
      │           │     ├── _handle_profit_take_signal(
      │           │     │       ticker, holding, profit_pct, take_profit_pct,
      │           │     │       sell_cooldown, today, sell_split_orders, user_id
      │           │     │   ) -> bool
      │           │     │     └── _get_sell_split_qty(
      │           │     │             ticker, holding_qty: int, sell_split_orders: dict, today: str
      │           │     │         ) -> int
      │           │     ├── _handle_add_buy_signal(
      │           │     │       ticker, holding, profit_pct, stop_loss_pct,
      │           │     │       current_rsi, add_rsi_limit, add_score_limit,
      │           │     │       score, reason_str, add_buy_cooldown, today, split_orders,
      │           │     │       cash_balance, current_price, exchange_rate, market_total, user_id
      │           │     │   ) -> bool
      │           │     └── _handle_score_trade(
      │           │             ticker, holding, score, reason_str, profit_pct,
      │           │             buy_max, sell_min, take_profit_pct, stop_loss_pct,
      │           │             add_rsi_limit, add_score_limit, sell_cooldown,
      │           │             add_buy_cooldown, panic_locks, split_orders,
      │           │             sell_split_orders, today, cash_balance, current_price,
      │           │             exchange_rate, market_total, user_id
      │           │         ) -> TradeResult
      │           │           ├── _handle_buy_split(...) -> bool
      │           │           │     ├── _is_buy_cooldown_active(...) -> bool
      │           │           │     ├── _init_split_order(...) -> bool
      │           │           │     └── _execute_split_tranche(...) -> bool
      │           │           └── _handle_sell_signal(...) -> TradeResult
      │           │                 └── TradeExecutorService._execute_trade_v2(...) -> TradeResult
      │           └── _check_unmonitored_holdings(
      │                   prepared_signals, holdings, user_id,
      │                   sell_split_orders, split_orders, sell_cooldown,
      │                   add_buy_cooldown, today, cfg
      │               ) -> tuple[bool, set[str]]
      │                 ├── _handle_forced_sell(...) -> TradeResult   # 손절
      │                 └── _handle_profit_take_signal(...) -> bool   # 익절
      │
      └── _send_portfolio_report(
              user_id: str, before_snapshot: dict,
              executed_tickers: Optional[set]
          ) -> None
            ├── _load_latest_portfolio(user_id: str) -> tuple[list[HoldingSchema], float, dict]
            │     ├── PortfolioService.sync_with_kis(user_id: str) -> list[dict]
            │     ├── PortfolioService.load_portfolio(user_id: str) -> list[HoldingSchema]
            │     └── PortfolioService.get_last_balance_summary() -> dict
            ├── _filter_report_changes(
            │       before_snapshot: dict, after_snapshot: dict,
            │       executed_tickers: set, latest_holdings: list[HoldingSchema]
            │   ) -> tuple[set[str], list[HoldingSchema]]
            ├── ReportService.format_trade_result_report(...) -> str
            ├── ReportService.format_portfolio_report(...) -> str
            └── AlertService.send_slack_alert(message: str) -> bool
```

---

## 2. 주문 실행 흐름

```
TradeExecutorService._execute_trade_v2(
    ticker: str, side: str, reason: str, profit_pct: float,
    is_holding: bool, score: int, current_price: float,
    market_total: float, cash_balance: float,
    exchange_rate: float, holdings: list, user_id: str,
    forced_qty: int = 0
) -> TradeResult(executed: bool, spent_krw: float, spent_usd: float)
  ├── [side="buy"]
  │   └── _execute_buy_order(
  │           ticker, reason, score, current_price, ...
  │       ) -> TradeResult
  │         ├── _check_buy_cash_and_entry_conditions(...) -> bool
  │         │     ├── _is_cash_ratio_sufficient(...) -> bool
  │         │     └── _check_market_hours(ticker: str) -> bool
  │         ├── _compute_buy_market_totals(...) -> tuple
  │         ├── _calculate_buy_quantity(
  │         │       score, cash_balance, current_price,
  │         │       exchange_rate, is_kr_flag, market_total_krw, usd_cash_krw
  │         │   ) -> tuple[int, float, float]  # (qty, spent_krw, final_price)
  │         ├── KisService.send_order / send_overseas_order -> dict
  │         ├── OrderService.record_trade(...) -> TradeHistory
  │         └── _send_trade_alert(...) -> None
  │               └── AlertService.send_slack_alert(message: str) -> bool
  └── [side="sell"]
      └── _execute_sell_order(
              ticker, score, current_price, holdings, user_id,
              forced_qty: int = 0
          ) -> TradeResult
            ├── KisService.send_order / send_overseas_order -> dict
            ├── OrderService.record_trade(...) -> TradeHistory
            └── _send_trade_alert(...) -> None
```

---

## 3. DCF 계산 흐름

```
DcfService.calculate_dcf(ticker: str) -> float
└── FinancialService.get_dcf_data(ticker: str) -> Optional[DcfInputData]
      ├── _get_dcf_from_override(ticker: str) -> Optional[DcfInputData]
      ├── _dcf_from_eps_history(ticker: str) -> Optional[DcfInputData]
      │     ├── _build_yearly_eps_as_cashflow(ticker, years=5) -> list[dict]
      │     ├── _calc_cagr(series: list) -> float
      │     └── _calc_discount_rate_from_volatility(series: list) -> float
      ├── _dcf_from_yfinance(ticker: str) -> Optional[DcfInputData]
      │     └── YFinanceService.get_fundamentals(ticker, market_type) -> Optional[YFinanceFundamentals]
      │           # YFinanceFundamentals: {fcf_per_share, beta, growth_rate, target_mean_price, analyst_count}
      ├── _dcf_from_analyst_target(ticker: str) -> Optional[DcfInputData]
      │     # analyst_count < 3이면 None 반환 (skip)
      │     └── YFinanceService.get_fundamentals(...) -> Optional[YFinanceFundamentals]
      ├── _dcf_from_eps_per_fallback(ticker: str) -> Optional[DcfInputData]
      └── [선택된 DcfInputData에 대해]
          └── _apply_growth_rate_cap(ticker: str, dcf_input: DcfInputData) -> DcfInputData
                ├── StockMetaRepo.get_stock_meta(ticker: str) -> Optional[StockMeta]
                └── GROWTH_RATE_CAP_BY_SECTOR[sector] -> float  # 성장률 상한 적용

[analyst_count >= 5이면 blending]
└── YFinanceService.get_fundamentals(...) -> Optional[YFinanceFundamentals]
    # dcf_value * 0.7 + target_mean_price * 0.3
```

---

## 4. 시장 레짐 계산 흐름

```
MacroService.get_macro_data() -> dict
└── StockMetaRepo.get_30d_avg_regime_score() -> Optional[float]
    # get_market_regime_history(30) → regime_score 평균
└── _get_market_regime(
        vix, fear_greed, economic_indicators, us_10y_yield,
        historical_avg_score: Optional[float]
    ) -> MarketRegimeSchema
      └── _calculate_all_regime_components(
              close: pd.Series, vix, vix_1m_chg, fear_greed,
              economic_indicators, us_10y_yield, yield_spread,
              btc_ret, dxy_ret, gold_ret, oil_ret, ndx_1m_hist
          ) -> RegimeComponents
            ├── _calc_technical_20(close, ndx_1m_hist) -> tuple[int, dict, dict]
            │     # current_price = close.tail(20).mean() (20일 평균)
            ├── _calc_vix_20(vix, vix_1m_chg) -> int
            ├── _calc_fng_20(fear_greed: int) -> tuple[int, bool]
            │     # → (score: 0~20, extreme_fear: bool)
            │     # extreme_fear = fear_greed <= 20
            ├── _calc_econ_20(economic_indicators) -> int
            ├── _calc_composite_20(...) -> tuple[int, dict]
            ├── _calc_inflation_pressure(...) -> tuple[int, dict]
            ├── _calc_growth_signal(...) -> tuple[int, dict]
            └── _determine_economic_phase(
                    inflation_pressure: int, growth_signal: int
                ) -> tuple[str, int]
                  # ECONOMIC_PHASES modifier: Stagflation→-12, Deflation→-8,
                  # Inflation→-5, Reflation→+3, Goldilocks→+8
      └── _assemble_regime_result(
              ..., extreme_fear: bool, historical_avg_score: Optional[float]
          ) -> MarketRegimeSchema
            # blended_score = regime_score * 0.6 + historical_avg_score * 0.4
            # extreme_fear=True → status="Bear" 강제
            # blended_score >= 65 → "Bull"
            # blended_score <= bear_threshold → "Bear"
            # else → "Neutral"
```

---

## 5. 일일 시세 동기화 흐름 (매일 04:00)

```
DataService.sync_daily_market_data(limit: int = 100) -> None
├── get_top_krx_tickers(limit: int) -> list[str]
├── get_top_us_tickers(limit: int) -> list[str]
├── _get_holding_tickers() -> list[tuple[str, str]]
│     └── PortfolioRepo.load_holdings("sean") -> list[dict]
│           # 보유종목 중 top 리스트에 없는 것만 추가
└── [all_tickers 순회]
    └── _sync_ticker_market_data(ticker, market, token) -> None
```

---

## 6. 전략 상태 영속 흐름

```
StrategyStateRepo.load(user_id: str) -> dict
  # _USER_FIELDS: sell_cooldown, add_buy_cooldown, panic_locks,
  #               split_orders, sell_split_orders, trailing_high
  └── _deserialize_field(field: str, raw_json: str) -> dict
        # MODEL_MAP: add_buy_cooldown→BuyCooldownEntry,
        #            split_orders→SplitOrderState, sell_split_orders→SplitSellOrderState

StrategyStateRepo.save(user_id: str, user_state: dict) -> None
  └── _serialize_value(value: Any) -> Any
        # Pydantic model → dict 재귀 변환
```

---

## 7. 핵심 데이터 모델 타입

| 타입 | 필드 | 설명 |
|------|------|------|
| `TradeResult` | `executed: bool, spent_krw: float, spent_usd: float` | 주문 실행 결과 |
| `ExecutionConfig` | `buy_max, sell_min, take_profit_pct, stop_loss_pct, add_rsi_limit, add_score_limit, exchange_rate, today` | 실행 설정 스냅샷 |
| `UnpackedSignal` | `ticker, state, holding, score, reason_str, forced_sell: bool, profit_pct: float, market_total: float` | 신호 언패킹 결과 |
| `RegimeComponents` | `technical_20, vix_20, fng_20, econ_20, other_20, extreme_fear: bool, ema_map, tech_detail, ...` | 레짐 계산 컴포넌트 |
| `MacroDataSnapshot` | `regime_status, regime_score, vix, fear_greed, us_10y_yield, exchange_rate, ...` | 거시 데이터 스냅샷 |
| `SplitOrderState` | `total_qty, remaining_qty, split_count, start_date, entry_price` | 분할 매수 상태 |
| `SplitSellOrderState` | `total_qty, remaining_qty, splits_done, split_count, start_date, tranche_qty` | 분할 매도 상태 |
| `DcfInputData` | `fcf_per_share, beta, growth_rate, discount_rate, source, analyst_count: int` | DCF 입력값 |
| `YFinanceFundamentals` | `fcf_per_share, beta, growth_rate, target_mean_price, analyst_count: int` | yfinance 기초값 |

---

**Last Updated**: 2026-03-14
