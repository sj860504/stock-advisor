'use strict';

const API  = '/api';
const USER = 'sean';
let tradeMode  = 'buy';
let macroCache = null;

// ─── Auth ─────────────────────────────────────────────────────────────────
function showLogin(msg = '') {
    document.getElementById('login-overlay').classList.add('show');
    document.getElementById('login-error').textContent = msg;
    document.getElementById('login-id').value = '';
    document.getElementById('login-pw').value = '';
    setTimeout(() => document.getElementById('login-id').focus(), 100);
}
function hideLogin() { document.getElementById('login-overlay').classList.remove('show'); }

async function doLogin() {
    const id  = document.getElementById('login-id').value.trim();
    const pw  = document.getElementById('login-pw').value;
    const btn = document.getElementById('login-btn');
    if (!id || !pw) { document.getElementById('login-error').textContent = '아이디와 비밀번호를 입력하세요.'; return; }
    btn.disabled = true; btn.textContent = '로그인 중...';
    try {
        const res = await fetch(API + '/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ username: id, password: pw }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || '로그인 실패');
        hideLogin();
        initApp();
    } catch (e) {
        document.getElementById('login-error').textContent = e.message;
    } finally {
        btn.disabled = false; btn.textContent = '로그인';
    }
}

async function logout() {
    await fetch(API + '/auth/logout', { method: 'POST', credentials: 'include' });
    if (_priceStream) { _priceStream.close(); _priceStream = null; }
    showLogin();
}

// ─── Tab Management ───────────────────────────────────────────────────────
function switchTab(el, tabId) {
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    el.classList.add('active');
    document.querySelectorAll('.page-section').forEach(p => p.classList.remove('active'));
    document.getElementById(tabId).classList.add('active');
    const loaders = {
        dashboard: () => { fetchBalance(); fetchMacroBar(); fetchRegimeScore(); fetchSectorWeightsMini(); fetchPortfolioFull(); },
        history:   () => { fetchHistory(); },
        market:    () => { fetchTop20(); fetchSignals(); },
        trading:   () => { fetchWaitingList(); fetchStrategyStatus(); },
        macro:     () => { fetchMacro(); fetchEconCalendar(); },
        settings:  () => { fetchSettings(); fetchAlerts(); },
        logs:      () => { fetchLogs(); },
        watchlist: () => { fetchWatchlist(); fetchStrategyMode(); },
    };
    loaders[tabId]?.();
}

// ─── Helpers ──────────────────────────────────────────────────────────────
const fmt     = (v, d = 2) => v == null ? '-' : Number(v).toFixed(d);
const fmtCurr = (v, sym = '$') => v == null ? '-' : sym + Number(v).toLocaleString(undefined, {
    minimumFractionDigits:  sym === '$' ? 2 : 0,
    maximumFractionDigits:  sym === '$' ? 2 : 0,
});
const fmtPct  = (v, sign = true) => v == null ? '-' : (sign && v > 0 ? '+' : '') + fmt(v) + '%';
const cls     = (v) => v >= 0 ? 'up' : 'down';
const badge   = (v) => v >= 0
    ? `<span class="badge b-up">${fmtPct(v)}</span>`
    : `<span class="badge b-down">${fmtPct(v)}</span>`;
const isKr    = (d) => {
    const m = String(d.market || '').toLowerCase();
    if (m === 'kr') return true;
    if (m === 'us') return false;
    return /^\d{6}$/.test(String(d.ticker || ''));
};
const getSym  = (d) => isKr(d) ? '₩' : '$';
const fmtKrw  = (v) => v == null ? '-' : '₩' + Math.round(Number(v)).toLocaleString();

async function apiFetch(path, opts = {}) {
    const r = await fetch(API + path, { ...opts, credentials: 'include', headers: opts.headers || {} });
    if (r.status === 401) { showLogin('세션이 만료되었습니다. 다시 로그인해 주세요.'); throw new Error('unauthorized'); }
    if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        let msg = err.detail || r.statusText;
        if (Array.isArray(msg)) msg = msg.map(m => m.msg || JSON.stringify(m)).join(', ');
        throw new Error(msg);
    }
    return r.json();
}

// ─── Toast ────────────────────────────────────────────────────────────────
function showToast(msg, ok = true, duration = 3200) {
    const container = document.getElementById('toast-container');
    const t = document.createElement('div');
    t.className = `toast ${ok ? 'ok' : 'err'}`;
    t.textContent = msg;
    container.appendChild(t);
    setTimeout(() => {
        t.style.transition = 'opacity .3s, transform .3s';
        t.style.opacity = '0';
        t.style.transform = 'translateX(110%)';
        setTimeout(() => t.remove(), 320);
    }, duration);
}

function setUpdated() {
    document.getElementById('last-updated').textContent = '갱신: ' + new Date().toLocaleTimeString('ko-KR');
}

// ─── Strategy Status ──────────────────────────────────────────────────────
async function fetchStrategyStatus() {
    try {
        const data = await apiFetch('/trading/waiting-list');
        const enabled = data?.enabled ?? data?.strategy_enabled ?? false;
        updateStrategyChip(enabled);
    } catch (e) {}
}

function updateStrategyChip(enabled) {
    const chip = document.getElementById('strategy-chip');
    const dot  = document.getElementById('strategy-dot');
    const big  = document.getElementById('strategy-status-big');
    chip.textContent = enabled ? '전략 ON' : '전략 OFF';
    chip.className   = 'status-chip ' + (enabled ? 'chip-on' : 'chip-off');
    if (dot) { dot.className = 'status-dot ' + (enabled ? 'on' : 'off'); }
    if (big) { big.textContent = enabled ? '▶ 실행 중' : '⏹ 중지됨'; big.style.color = enabled ? 'var(--bull)' : 'var(--bear)'; }
}

async function startStrategy() {
    try { await apiFetch('/trading/start'); updateStrategyChip(true);  showToast('전략이 시작되었습니다'); }
    catch (e) { showToast(e.message, false); }
}
async function stopStrategy() {
    try { await apiFetch('/trading/stop');  updateStrategyChip(false); showToast('전략이 중지되었습니다'); }
    catch (e) { showToast(e.message, false); }
}

// ─── Sort / Search Infrastructure ─────────────────────────────────────────
const sortState   = {};
const searchState = {};

const sortKeys = {
    'portfolio-tbody': [
        d => (d.ticker||'').toLowerCase(),
        d => d.buy_price ?? d.avg_price ?? 0,
        d => d.price ?? d.current_price ?? 0,
        d => d.quantity ?? 0,
        d => d.current_value || ((d.price??0)*(d.quantity||0)),
        d => d.return_pct ?? d.profit_pct ?? 0,
        d => d.dcf_fair ?? 0,
        d => d.rsi ?? 0,
    ],
    'top20-tbody': [
        ([t])   => t.toLowerCase(),
        ([,i])  => (i.name||'').toLowerCase(),
        ([,i])  => i.price ?? 0,
        ([,i])  => i.change_pct ?? 0,
        ([,i])  => i.rsi ?? 0,
        ([,i])  => i.score ?? 0,
    ],
    'history-tbody': [
        d => d.created_at || d.timestamp || d.date || '',
        d => (d.ticker||'').toLowerCase(),
        d => (d.action||d.order_type||''),
        d => d.price ?? 0,
        d => d.quantity ?? 0,
        d => d.amount ?? ((d.price||0)*(d.quantity||0)),
        d => d.profit_pct ?? 0,
    ],
};

function handleSort(tableId, colIndex, renderFn) {
    const st = sortState[tableId];
    if (st && st.col === colIndex) st.dir = st.dir === 'asc' ? 'desc' : 'asc';
    else sortState[tableId] = { col: colIndex, dir: 'asc' };
    const tbody = document.getElementById(tableId);
    if (tbody) {
        const allThs = tbody.closest('table').querySelectorAll('thead th');
        allThs.forEach(th => th.classList.remove('sort-asc', 'sort-desc'));
        const targetTh = allThs[colIndex];
        if (targetTh) targetTh.classList.add('sort-' + sortState[tableId].dir);
    }
    renderFn();
}

function applySortToData(tableId, data) {
    const st = sortState[tableId];
    if (!st) return data;
    const keys = sortKeys[tableId];
    if (!keys || !keys[st.col]) return data;
    const keyFn = keys[st.col];
    const dir   = st.dir === 'asc' ? 1 : -1;
    return [...data].sort((a, b) => {
        const va = keyFn(a), vb = keyFn(b);
        if (va < vb) return -1 * dir;
        if (va > vb) return  1 * dir;
        return 0;
    });
}

// ─── Dashboard filter ─────────────────────────────────────────────────────
let dashMarketFilter = 'all';

// ─── KPI Cards ────────────────────────────────────────────────────────────
function renderKpiCards(data) {
    const analysis = data?.analysis || {};
    const kr       = analysis.kr  || {};
    const us       = analysis.us  || {};
    const summary  = analysis.summary || {};
    const exRate   = data?.exchange_rate || 1350;

    const total   = data.total_eval || 0;
    const totalKrwStr = '₩' + Math.round(total).toLocaleString();
    const retPct  = summary.profit_pct || 0;

    const krCash  = kr.cash  || 0;
    const usdCash = us.cash_usd || 0;

    const row = document.getElementById('kpi-row');
    if (!row) return;
    row.innerHTML = `
        <div class="kpi-card">
            <div class="kpi-label">총 평가액</div>
            <div class="kpi-value">${totalKrwStr}</div>
            <div class="kpi-delta ${retPct >= 0 ? 'bull' : 'bear'}">${fmtPct(retPct)} 수익률</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">총 수익</div>
            <div class="kpi-value ${retPct >= 0 ? 'up' : 'down'}">₩${Math.round(summary.total_profit || 0).toLocaleString()}</div>
            <div class="kpi-delta">실현+미실현</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">KRW 현금</div>
            <div class="kpi-value">₩${Math.round(krCash).toLocaleString()}</div>
            <div class="kpi-delta">${total > 0 ? ((krCash/total*100).toFixed(1)) : '0.0'}% 비중</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">USD 현금</div>
            <div class="kpi-value">$${usdCash.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})}</div>
            <div class="kpi-delta">${total > 0 ? ((usdCash*exRate/total*100).toFixed(1)) : '0.0'}% 비중</div>
        </div>
    `;
}

// ─── Portfolio ────────────────────────────────────────────────────────────
let _portfolioDataCache = null;

async function fetchPortfolioFull() {
    try {
        const resp   = await apiFetch('/portfolio/' + USER + '/full-report');
        const data   = resp.holdings || resp;
        const exRate = resp.exchange_rate || 1350;
        const cooldown = resp.cooldown || { buy: {}, sell: {} };
        
        // Inject cooldown data into each holding for easier rendering
        data.forEach(h => {
            h.has_buy_cooldown = !!cooldown.buy[h.ticker];
            h.has_sell_cooldown = !!cooldown.sell[h.ticker];
        });

        _portfolioDataCache = data;

        let valKr = 0, valUsUsd = 0;
        data.forEach(d => {
            const price = d.price ?? d.current_price ?? 0;
            const val   = d.current_value || (price * (d.quantity || 0));
            if (isKr(d)) valKr += val;
            else valUsUsd += val;
        });
        const valUsKrw = valUsUsd * exRate;
        const totalVal = valKr + valUsKrw;
        const krPct    = totalVal > 0 ? (valKr / totalVal * 100).toFixed(1) : 0;
        const usPct    = totalVal > 0 ? (valUsKrw / totalVal * 100).toFixed(1) : 0;

        const portSumm = document.getElementById('port-asset-summary');
        if (portSumm) portSumm.innerHTML = `
            <div style="background:var(--raised);border:1px solid var(--border);border-radius:6px;padding:6px 12px;display:flex;gap:16px;font-size:.83rem">
                <div><span style="color:var(--sub)">총 자산(주식) </span><b class="mono">₩${Math.round(totalVal).toLocaleString()}</b></div>
                <div><span style="color:var(--sub)">🇰🇷 </span><b class="mono">₩${Math.round(valKr).toLocaleString()}</b> <span style="color:var(--sub)">(${krPct}%)</span></div>
                <div><span style="color:var(--sub)">🇺🇸 </span><b class="mono">$${Math.round(valUsUsd).toLocaleString()}</b> <span style="color:var(--sub)">(${usPct}%)</span></div>
            </div>`;

        renderPortfolioTable(data);
        setUpdated();
    } catch (e) { console.error(e); }
}

async function syncPortfolio() {
    showToast('KIS 동기화 중...');
    try {
        const res = await apiFetch('/portfolio/' + USER + '/sync', { method: 'POST' });
        await fetchBalance();
        await fetchPortfolioFull();
        showToast(res.message || '동기화 완료');
    } catch (e) { showToast(e.message, false); }
}

const SECTOR_OPTIONS = [
    { value: 'other',     label: '── 미분류/ETF ──' },
    { value: 'tech',      label: '💻 기술주' },
    { value: 'value',     label: '🏥 가치주' },
    { value: 'financial', label: '🏦 금융주' },
];

function renderPortfolioTable(data) {
    const tbody = document.getElementById('portfolio-tbody');
    if (!data?.length) { tbody.innerHTML = '<tr><td colspan="10" class="empty">보유 종목 없음</td></tr>'; return; }
    if (dashMarketFilter === 'kr') data = data.filter(d =>  isKr(d));
    if (dashMarketFilter === 'us') data = data.filter(d => !isKr(d));
    const q = searchState['portfolio-tbody'];
    if (q) data = data.filter(d => (d.ticker||'').toLowerCase().includes(q) || (d.name||'').toLowerCase().includes(q));
    if (!data.length) { tbody.innerHTML = '<tr><td colspan="10" class="empty">해당 종목 없음</td></tr>'; return; }
    data = applySortToData('portfolio-tbody', data);
    tbody.innerHTML = data.map(d => {
        const ret    = d.return_pct ?? d.profit_pct;
        const rsi    = d.rsi;
        const rsiCls = rsi < 30 ? 'b-up' : rsi > 70 ? 'b-down' : 'b-gray';
        const s      = getSym(d);
        const retColor   = ret >= 0 ? 'var(--bull)' : 'var(--bear)';
        const barWidth   = Math.min(Math.abs(ret ?? 0) * 2, 100);
        return `<tr>
            <td><span class="ticker-cell">${d.ticker}</span><span class="sub-text">${d.name || ''}</span></td>
            <td class="mono">${fmtCurr(d.buy_price ?? d.avg_price, s)}</td>
            <td class="mono">${fmtCurr(d.price ?? d.current_price, s)}</td>
            <td class="mono">${fmt(d.quantity, 0)}</td>
            <td class="mono">${fmtCurr(d.current_value || (d.price * (d.quantity || 0)), s)}<span class="${cls(d.profit_loss || 0)} sub-text">${d.profit_loss ? (d.profit_loss > 0 ? '+' : '') + fmtCurr(d.profit_loss, s) : ''}</span></td>
            <td class="pnl-cell">
                <div class="pnl-bar" style="width:${barWidth}%;background:${retColor}"></div>
                <span class="pnl-text" style="color:${retColor}">${ret != null ? (ret >= 0 ? '+' : '') + fmt(ret) + '%' : '-'}</span>
            </td>
            <td class="mono" style="font-size:.82rem">${d.dcf_fair ? fmtCurr(d.dcf_fair, s) + '<span class="sub-text">' + fmtPct(d.dcf_upside) + '</span>' : '-'}</td>
            <td><span class="badge ${rsiCls}">${fmt(rsi, 1)}</span></td>
            <td>
                <select style="width:110px;padding:3px 6px;font-size:.75rem"
                    onchange="updateSector('${d.ticker}', this.value)">
                    ${SECTOR_OPTIONS.map(o => `<option value="${o.value}"${o.value === (d.sector || 'other') ? ' selected' : ''}>${o.label}</option>`).join('')}
                </select>
            </td>
            <td class="text-center">
                ${d.has_buy_cooldown ? `<button class="btn btn-outline btn-sm" style="padding:2px 6px;font-size:11px;color:var(--sub)" onclick="resetTickerCooldown('${d.ticker}', 'buy')">매수 <i class="fas fa-times" style="font-size:9px;color:var(--bear)"></i></button>` : ''}
                ${d.has_sell_cooldown ? `<button class="btn btn-outline btn-sm" style="padding:2px 6px;font-size:11px;color:var(--sub)" onclick="resetTickerCooldown('${d.ticker}', 'sell')">매도 <i class="fas fa-times" style="font-size:9px;color:var(--bear)"></i></button>` : ''}
                ${!d.has_buy_cooldown && !d.has_sell_cooldown ? '-' : ''}
            </td>
            <td style="white-space:nowrap">
                <button class="btn btn-outline btn-sm" style="margin-right:4px" onclick="openDcfSetModal('${d.ticker}','${s}')">DCF</button>
                <button class="btn btn-danger btn-sm" style="margin-right:4px" onclick="openSellModal('${d.ticker}')">매도</button>
                <button class="btn btn-outline btn-sm" style="color:var(--bear);border-color:var(--bear)" onclick="deleteHolding('${d.ticker}')">삭제</button>
            </td>
        </tr>`;
    }).join('');
}

async function updateSector(ticker, sector) {
    try {
        await apiFetch(`/portfolio/${USER}/${encodeURIComponent(ticker)}/sector?sector=${encodeURIComponent(sector)}`, { method: 'PATCH' });
        showToast(`${ticker} 섹터 → ${sector}`);
        fetchSectorWeightsMini();
    } catch (e) { showToast(e.message, false); }
}

async function deleteHolding(ticker) {
    if (!confirm(`${ticker} 보유 종목을 삭제하시겠습니까?`)) return;
    try {
        await apiFetch('/portfolio/' + USER + '/' + ticker, { method: 'DELETE' });
        showToast('삭제됨');
        fetchPortfolioFull();
    } catch (e) { showToast(e.message, false); }
}

// ─── Trade History ─────────────────────────────────────────────────────────
let _historyDataCache = null;

async function fetchHistory() {
    const action = document.getElementById('hist-type').value;
    const market = document.getElementById('hist-market').value;
    const date   = document.getElementById('hist-date').value;
    let url = '/trading/history?limit=100';
    if (action) url += '&action=' + action;
    if (market) url += '&market=' + market;
    if (date)   url += '&date=' + date;
    try {
        const data = await apiFetch(url);
        _historyDataCache = data;
        renderHistoryTable();
    } catch (e) { showToast(e.message, false); }
}

function renderHistoryTable() {
    const tbody = document.getElementById('history-tbody');
    let data = _historyDataCache;
    if (!data?.length) { tbody.innerHTML = '<tr><td colspan="7" class="empty">내역 없음</td></tr>'; return; }
    const q = searchState['history-tbody'];
    if (q) data = data.filter(d => (d.ticker||'').toLowerCase().includes(q) || (d.name||'').toLowerCase().includes(q));
    if (!data.length) { tbody.innerHTML = '<tr><td colspan="7" class="empty">해당 종목 없음</td></tr>'; return; }
    data = applySortToData('history-tbody', data);
    tbody.innerHTML = data.map(d => {
        const isBuy   = (d.action || d.order_type || '').toLowerCase() === 'buy';
        const s       = isKr(d) ? '₩' : '$';
        const retBadge = d.profit_pct != null
            ? `${badge(d.profit_pct)}<span class="${cls(d.profit || 0)} sub-text">${d.profit > 0 ? '+' : ''}${fmtCurr(d.profit, s)}</span>`
            : '-';
        return `<tr>
            <td class="mono" style="font-size:.8rem;white-space:nowrap">${(d.created_at || d.timestamp || d.date || '').slice(0, 16)}</td>
            <td><span class="ticker-cell">${d.ticker}</span><span class="sub-text">${d.name || ''}</span></td>
            <td><span class="badge ${isBuy ? 'b-up' : 'b-down'}">${isBuy ? 'BUY' : 'SELL'}</span></td>
            <td class="mono">${fmtCurr(d.price, s)}</td>
            <td class="mono">${fmt(d.quantity, 0)}</td>
            <td class="mono">${fmtCurr(d.amount ?? ((d.price || 0) * (d.quantity || 0)), s)}</td>
            <td>${retBadge}</td>
        </tr>`;
    }).join('');
}

// ─── Balance ───────────────────────────────────────────────────────────────
let _balanceDataCache = null;

async function fetchBalance() {
    const el = document.getElementById('balance-content');
    el.innerHTML = '<div class="empty" style="font-size:.86rem;padding:10px 0">조회 중...</div>';
    try {
        const data = await apiFetch('/trading/balance');
        _balanceDataCache = data;
        renderBalanceContent(data);
        renderKpiCards(data);
    } catch (e) { el.innerHTML = `<div class="empty" style="color:var(--bear);padding:10px 0">조회 실패: ${e.message}</div>`; }
}

function renderBalanceContent(data) {
    const el = document.getElementById('balance-content');
    if (!data) return;
    const analysis = data.analysis || {};
    const kr = analysis.kr || {};
    const us = analysis.us || {};
    const summary = analysis.summary || {};

    let dispTotal = 0, dispCash = 0, dispStock = 0, dispRet = 0;
    let dispTotalStr = '', dispCashStr = '', dispStockStr = '';

    if (dashMarketFilter === 'kr') {
        dispTotal = kr.total || 0; dispCash = kr.cash || 0; dispStock = kr.current || 0; dispRet = kr.profit_pct || 0;
        dispTotalStr = '₩' + Math.round(dispTotal).toLocaleString();
        dispCashStr  = '₩' + Math.round(dispCash).toLocaleString();
        dispStockStr = '₩' + Math.round(dispStock).toLocaleString();
    } else if (dashMarketFilter === 'us') {
        dispTotal = us.total_krw || 0; dispCash = us.cash_krw || 0; dispStock = us.current_krw || 0; dispRet = us.profit_pct || 0;
        const usTotalUsd = (us.current_usd || 0) + (us.cash_usd || 0);
        dispTotalStr = '$' + usTotalUsd.toLocaleString(undefined, {minimumFractionDigits:2,maximumFractionDigits:2});
        dispCashStr  = '$' + (us.cash_usd  || 0).toLocaleString(undefined, {minimumFractionDigits:2,maximumFractionDigits:2});
        dispStockStr = '$' + (us.current_usd||0).toLocaleString(undefined, {minimumFractionDigits:2,maximumFractionDigits:2});
    } else {
        dispTotal = data.total_eval || 0; dispCash = (kr.cash||0)+(us.cash_krw||0); dispStock = summary.total_current||0; dispRet = summary.profit_pct||0;
        dispTotalStr = '₩' + Math.round(dispTotal).toLocaleString();
        dispCashStr  = '₩' + Math.round(dispCash).toLocaleString();
        dispStockStr = '₩' + Math.round(dispStock).toLocaleString();
    }

    const cashPct  = dispTotal > 0 ? (dispCash  / dispTotal * 100) : 0;
    const stockPct = dispTotal > 0 ? (dispStock / dispTotal * 100) : 0;
    const pending  = data.pending || {};
    const pendingCount = pending.count || 0;
    const pendingOrders = pending.orders || [];

    let pendingHtml = '';
    if (pendingCount > 0) {
        const { buy_krw=0, buy_usd=0, sell_krw=0, sell_usd=0 } = pending;
        let summary2 = [];
        if (buy_krw  > 0) summary2.push(`매수 ₩${Math.round(buy_krw).toLocaleString()}`);
        if (buy_usd  > 0) summary2.push(`매수 $${buy_usd.toFixed(2)}`);
        if (sell_krw > 0) summary2.push(`매도 ₩${Math.round(sell_krw).toLocaleString()}`);
        if (sell_usd > 0) summary2.push(`매도 $${sell_usd.toFixed(2)}`);
        const items = pendingOrders.map(o => {
            const isKrT = /^\d{6}$/.test(o.ticker);
            const priceStr = isKrT ? `₩${Math.round(o.price).toLocaleString()}` : `$${o.price.toFixed(2)}`;
            return `<span style="margin-right:8px">${o.order_type==='buy'?'🔵':'🔴'}${o.ticker} ${o.quantity}주 @${priceStr} (${o.timestamp})</span>`;
        }).join('');
        pendingHtml = `<div style="border:1px solid rgba(255,215,64,.3);border-radius:6px;padding:6px 12px;margin:4px 0;font-size:.8rem">
            <div style="color:var(--warn);font-weight:600;margin-bottom:4px">⏳ 미체결 ${pendingCount}건 (${summary2.join(' / ')})</div>
            <div style="color:var(--sub);line-height:1.6">${items}</div>
        </div>`;
    }

    el.innerHTML = `<div style="border:1px solid var(--border);border-radius:6px;padding:8px 12px;margin:4px 0;font-size:.84rem;display:flex;justify-content:space-around">
        <div style="text-align:center"><div style="color:var(--sub);margin-bottom:2px">전체자산</div><b class="mono">${dispTotalStr}</b> <span style="font-size:.78rem">${badge(dispRet)}</span></div>
        <div style="width:1px;background:var(--border)"></div>
        <div style="text-align:center"><div style="color:var(--sub);margin-bottom:2px">현금</div><b class="mono">${dispCashStr}</b> <span style="font-size:.78rem;color:var(--sub)">(${fmtPct(cashPct,false)})</span></div>
        <div style="width:1px;background:var(--border)"></div>
        <div style="text-align:center"><div style="color:var(--sub);margin-bottom:2px">보유주식</div><b class="mono">${dispStockStr}</b> <span style="font-size:.78rem;color:var(--sub)">(${fmtPct(stockPct,false)})</span></div>
    </div>${pendingHtml}`;
}

// ─── Sector Weights ────────────────────────────────────────────────────────
const SECTOR_LABELS = { tech: '기술주', value: '가치주', financial: '금융주' };
const SECTOR_ICONS  = { tech: '💻', value: '🏥', financial: '🏦' };

let sectorMarketTab      = 'kr';
let sectorTargetOverrides = {};
let _sectorDataCache     = null;

function switchSectorTab(market) {
    sectorMarketTab = market;
    sectorTargetOverrides = {};
    document.querySelectorAll('.sector-mkt-tab').forEach(b => b.classList.remove('active'));
    const tab = document.getElementById('sector-tab-' + market);
    if (tab) tab.classList.add('active');
    if (_sectorDataCache) {
        const el = document.getElementById('sector-weights-content');
        if (el) el.innerHTML = renderSectorWeightsFull(_sectorDataCache[market] || {}, market);
    } else fetchSectorWeights();
}

async function fetchSectorWeights() {
    const el = document.getElementById('sector-weights-content');
    if (el) el.innerHTML = '<div class="empty">Loading...</div>';
    try {
        const data = await apiFetch('/analysis/sector-weights?user_id=' + USER);
        _sectorDataCache = data;
        const mktData = data?.[sectorMarketTab] || {};
        if (el) el.innerHTML = renderSectorWeightsFull(mktData, sectorMarketTab);
        renderSectorMini(data?.kr || {});
    } catch (e) {
        if (el) el.innerHTML = `<div class="empty" style="color:var(--bear)">오류: ${e.message}</div>`;
    }
}

async function fetchSectorWeightsMini() {
    try {
        const data = await apiFetch('/analysis/sector-weights?user_id=' + USER);
        _sectorDataCache = data;
        renderSectorMini(data?.kr || {});
    } catch (e) {}
}

function renderSectorMini(mktData) {
    const el = document.getElementById('sector-mini-content');
    if (!el) return;
    const weights = mktData?.weights || {};
    el.innerHTML = ['tech','value','financial'].map(g => {
        const w   = weights[g] || {};
        const cur = (w.weight ?? w.current ?? 0) * 100;
        const tgt = (w.target ?? 0) * 100;
        const dev = (w.dev ?? 0) * 100;
        const barColor = Math.abs(dev) < 5 ? 'var(--bull)' : dev > 0 ? 'var(--bear)' : 'var(--warn)';
        return `<div style="margin-bottom:8px">
            <div style="display:flex;justify-content:space-between;font-size:.77rem;margin-bottom:2px">
                <span>${SECTOR_ICONS[g]} ${SECTOR_LABELS[g]}</span>
                <span style="color:${barColor};font-weight:700;font-family:var(--mono)">${cur.toFixed(1)}%
                    <span style="color:var(--sub);font-weight:400">(목표 ${tgt.toFixed(0)}%)</span></span>
            </div>
            <div class="sector-mini-bar">
                <div class="sector-mini-fill" style="width:${Math.min(cur,100)}%;background:${barColor}"></div>
                <div style="position:absolute;top:0;left:${Math.min(tgt,100)}%;width:2px;height:100%;background:rgba(255,255,255,.4)"></div>
            </div>
        </div>`;
    }).join('');
}

function renderSectorWeightsFull(mktData, market) {
    const weights   = mktData?.weights || {};
    const underweight = mktData?.underweight || [];
    const overweight  = mktData?.overweight  || [];
    const currency  = mktData?.currency || (market === 'us' ? 'USD' : 'KRW');
    const totalVal  = mktData?.total || 0;

    const otherW   = weights['other'] || {};
    const otherCur = (otherW.weight ?? otherW.current ?? 0) * 100;
    const otherWarning = otherCur > 10
        ? `<div style="background:rgba(255,215,64,.08);border:1px solid rgba(255,215,64,.25);border-radius:6px;padding:10px 12px;margin-bottom:12px;font-size:.8rem">
            ⚠️ <b style="color:var(--warn)">${otherCur.toFixed(1)}%</b>가 미분류(기타)로 집계되어 섹터 비중이 부정확합니다.</div>`
        : '';

    const weightRows = ['tech','value','financial'].map(g => {
        const w = weights[g] || {};
        const cur = (w.weight ?? w.current ?? 0) * 100;
        const overrideKey = market + '_' + g;
        const tgt = sectorTargetOverrides[overrideKey] != null ? sectorTargetOverrides[overrideKey] : (w.target ?? 0) * 100;
        const dev = cur - tgt;
        const isOk = Math.abs(dev) < 5;
        const isOver = dev > 5;
        const barColor = isOk ? 'var(--bull)' : isOver ? 'var(--bear)' : 'var(--warn)';
        const devBadge = isOk ? `<span class="badge b-up">적정</span>`
            : isOver ? `<span class="badge b-down">+${dev.toFixed(1)}% 초과</span>`
            : `<span class="badge b-warn">${dev.toFixed(1)}% 부족</span>`;
        return `<div class="sector-row">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
                <div>
                    <span style="font-weight:700;font-size:.93rem">${SECTOR_ICONS[g]} ${SECTOR_LABELS[g]}</span>
                    <span style="color:var(--sub);font-size:.77rem;margin-left:8px">현재 <b class="mono">${cur.toFixed(1)}%</b></span>
                </div>
                <div style="display:flex;align-items:center;gap:10px">
                    ${devBadge}
                    <span class="mono" style="font-size:.95rem;font-weight:800;color:${barColor}">${cur.toFixed(1)}%</span>
                </div>
            </div>
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
                <span style="font-size:.77rem;color:var(--sub);white-space:nowrap">목표:</span>
                <input type="range" min="0" max="100" step="1" value="${Math.round(tgt)}" style="flex:1;height:6px;cursor:pointer"
                    oninput="updateSectorTarget('${market}','${g}',this.value)" id="sector-slider-${market}-${g}">
                <span id="sector-tgt-val-${market}-${g}" class="mono" style="font-size:.83rem;font-weight:700;min-width:36px;text-align:right">${Math.round(tgt)}%</span>
            </div>
            <div class="sector-bar-outer">
                <div class="sector-bar-fill2" style="width:${Math.min(cur,100)}%;background:${barColor}"></div>
                <div class="sector-target-line" style="left:${Math.min(tgt,100)}%"></div>
            </div>
        </div>`;
    }).join('');

    const renderHoldingList = (list) => list.length
        ? list.map(h => {
            const label = (h.name && h.name !== h.ticker) ? h.name : (h.ticker ?? h);
            return `<span title="${h.ticker??''}" style="display:inline-block;padding:2px 8px;background:var(--raised);border-radius:4px;font-size:.78rem;margin:2px">${label}</span>`;
        }).join('')
        : '<span style="color:var(--sub);font-size:.8rem">없음</span>';

    const sym = currency === 'USD' ? '$' : '₩';
    const totalLabel = totalVal ? `<div style="font-size:.78rem;color:var(--sub);margin-bottom:12px">총 주식 평가액: ${sym}${Math.round(totalVal).toLocaleString()}</div>` : '';

    return `${totalLabel}${otherWarning}${weightRows}
        <div style="margin-top:12px;display:flex;gap:8px;justify-content:flex-end">
            <button class="btn btn-outline btn-sm" onclick="resetSectorTargets()">초기화</button>
            <button class="btn btn-primary btn-sm" onclick="saveSectorTargets('${market}')">목표 저장</button>
        </div>
        <div style="margin-top:14px;display:grid;grid-template-columns:1fr 1fr;gap:12px">
            <div><div class="section-title" style="margin-bottom:6px">매수 우선 (부족 섹터)</div><div>${renderHoldingList(underweight)}</div></div>
            <div><div class="section-title" style="margin-bottom:6px">매도 고려 (초과 섹터)</div><div>${renderHoldingList(overweight)}</div></div>
        </div>`;
}

function updateSectorTarget(market, group, val) {
    sectorTargetOverrides[market + '_' + group] = parseInt(val);
    const lbl = document.getElementById('sector-tgt-val-' + market + '-' + group);
    if (lbl) lbl.textContent = val + '%';
}
function resetSectorTargets() { sectorTargetOverrides = {}; fetchSectorWeights(); }

async function saveSectorTargets(market) {
    market = market || sectorMarketTab;
    const marketEntries = Object.entries(sectorTargetOverrides).filter(([k]) => k.startsWith(market + '_'));
    if (!marketEntries.length) { showToast('변경된 목표 없음', false); return; }
    try {
        await Promise.all(marketEntries.map(([k, v]) => {
            const group = k.replace(market + '_', '');
            return apiFetch('/trading/settings', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ key: `SECTOR_TARGET_${market.toUpperCase()}_${group.toUpperCase()}`, value: String(v / 100) }),
            });
        }));
        showToast('섹터 목표 비중 저장됨');
        sectorTargetOverrides = {};
        fetchSectorWeights();
    } catch (e) { showToast(e.message, false); }
}

// ─── Economic Calendar ─────────────────────────────────────────────────────
async function fetchEconCalendar() {
    const el = document.getElementById('econ-calendar-content');
    if (el) el.innerHTML = '<div class="empty">Loading...</div>';
    try {
        const data = await apiFetch('/market/calendar/weekly?days=7');
        if (!el) return;
        if (!data?.length) { el.innerHTML = '<div class="empty">이번 주 예정 없음</div>'; return; }
        el.innerHTML = data.map(ev => {
            const isPast = ev.is_past;
            const weight = ev.total_weight;
            const weightColor = weight >= 6 ? 'var(--bear)' : weight >= 3 ? 'var(--warn)' : 'var(--sub)';
            return `<div style="padding:10px 0;border-bottom:1px solid var(--border);${isPast ? 'opacity:.5' : ''}">
                <div style="display:flex;justify-content:space-between;align-items:flex-start">
                    <div>
                        <div style="font-size:.8rem;font-weight:600">${(ev.names || []).join(', ')}</div>
                        <div style="font-size:.72rem;color:var(--sub);margin-top:2px">${ev.date_kst||ev.date} ${ev.time_kst||'-'} KST</div>
                    </div>
                    <div style="display:flex;align-items:center;gap:6px">
                        <span style="font-size:.72rem;font-weight:700;color:${weightColor}">중요도 ${weight}</span>
                        ${isPast ? '<span class="badge b-gray">완료</span>' : '<span class="badge b-up">예정</span>'}
                    </div>
                </div>
            </div>`;
        }).join('');
    } catch (e) { if (el) el.innerHTML = `<div class="empty" style="color:var(--bear)">오류: ${e.message}</div>`; }
}

// ─── Macro Bar & Regime ────────────────────────────────────────────────────
async function fetchMacroBar() {
    try {
        const data = await apiFetch('/market/macro');
        macroCache = data;
        renderMacroBar(data);
        renderRegimeScore(data.market_regime);
    } catch (e) { console.error(e); }
}

async function fetchRegimeScore() {
    if (macroCache) { renderRegimeScore(macroCache.market_regime); return; }
    await fetchMacroBar();
}

function renderMacroBar(data) {
    const r      = data.market_regime || {};
    const vix    = data.vix;
    const yield10y = data.us_10y_yield;
    const btc    = (data.crypto || {}).BTC;
    const gold   = (data.commodities || {}).Gold;
    const oil    = (data.commodities || {}).Oil;
    const fg     = data.fear_greed;
    const od     = (r.components?.other_detail) || {};

    const chips = [
        { label: 'Regime',      val: (r.status||'-') + ' ' + (r.regime_score != null ? r.regime_score + '점' : ''), cls: r.status==='Bull'?'up':r.status==='Bear'?'down':'' },
        { label: 'SPX vs MA200',val: fmtPct(r.diff_pct, true), cls: cls(r.diff_pct||0) },
        { label: 'US 10Y',      val: yield10y ? yield10y + '%' : '-', cls: '' },
        { label: 'VIX',         val: vix ?? '-', cls: vix>25?'down':vix<15?'up':'' },
        { label: 'Fear&Greed',  val: fg != null ? fg + '/100' : '-', cls: fg>60?'up':fg<40?'down':'' },
        { label: 'Fwd P/E',     val: od.forward_pe != null ? od.forward_pe.toFixed(1)+'x' : '-', sub: od.avg_5y_pe != null ? 'avg '+od.avg_5y_pe.toFixed(1)+'x' : null, cls: od.forward_pe_raw > 0 ? 'up' : od.forward_pe_raw < 0 ? 'down' : '' },
        { label: 'BTC',         val: btc?.price ? '$'+Number(btc.price).toLocaleString() : '-', sub: btc?.change!=null?fmtPct(btc.change,true):null, cls: cls(btc?.change||0) },
        { label: 'Gold',        val: gold?.price ? '$'+fmt(gold.price) : '-', sub: gold?.change!=null?fmtPct(gold.change,true):null, cls: cls(gold?.change||0) },
        { label: 'Oil (WTI)',   val: oil?.price  ? '$'+fmt(oil.price)  : '-', sub: oil?.change!=null?fmtPct(oil.change,true):null,  cls: cls(oil?.change||0)  },
    ];
    document.getElementById('macro-bar').innerHTML = chips.map(c => `
        <div class="macro-chip">
            <div class="mc-label">${c.label}</div>
            <div class="mc-val ${c.cls}">${c.val}</div>
            ${c.sub ? `<div class="mc-sub ${c.cls}">${c.sub}</div>` : ''}
        </div>`).join('');
}

function renderRegimeScore(r) {
    if (!r) return;
    const score  = r.regime_score ?? 50;
    const status = r.status ?? 'Neutral';
    const comp   = r.components || {};
    const color  = status==='Bull'?'var(--bull)':status==='Bear'?'var(--bear)':'var(--warn)';
    const od     = comp.other_detail    || {};
    const td     = comp.technical_detail || {};
    const bearThr = r.bear_threshold ?? 40;
    const bearThrColor = bearThr>=48?'var(--bear)':bearThr>=44?'var(--warn)':'var(--sub)';
    const bearPersistLabel = bearThr>=48?'2개월 연속 Bear':bearThr>=44?'1개월 전 Bear':'정상';

    const techDetails = [
        td.spx_1m_ret!=null ? `1M ${fmtPct(td.spx_1m_ret)}` : '',
        td.spx_2w_ret!=null ? `2W ${fmtPct(td.spx_2w_ret)}` : '',
        td.spx_from_ath!=null ? `ATH대비 ${fmtPct(td.spx_from_ath)}` : '',
    ].filter(Boolean).join(' | ');

    const items = [
        { name:'기술 (EMA)', key:'technical', detail: techDetails },
        { name:'VIX',         key:'vix',       detail: od.vix_1m_chg!=null?`1M ${fmtPct(od.vix_1m_chg)}`:'' },
        { name:'Fear&Greed',  key:'fear_greed',detail: '' },
        { name:'경제지표',    key:'economic',  detail: '' },
        { name:'기타 (금리·BTC·DXY·Gold·P/E)', key:'other', detail: [
            od.us_10y_yield!=null?`10Y ${od.us_10y_yield}%`:'',
            od.yield_spread_10y2y!=null?`스프레드 ${fmtPct(od.yield_spread_10y2y)}`:'',
            od.btc_1m_ret!=null?`BTC ${fmtPct(od.btc_1m_ret)}`:'',
            od.dxy_1m_ret!=null?`DXY ${fmtPct(od.dxy_1m_ret)}`:'',
            od.gold_1m_ret!=null?`Gold ${fmtPct(od.gold_1m_ret)}`:'',
            od.forward_pe!=null?`P/E ${od.forward_pe.toFixed(1)}x`:'',
        ].filter(Boolean).join(' | ')},
    ];

    document.getElementById('regime-score-val').textContent = score;
    document.getElementById('regime-score-val').style.color = color;
    document.getElementById('regime-status-badge').innerHTML =
        `<span class="badge ${status==='Bull'?'b-up':status==='Bear'?'b-down':'b-warn'}" style="font-size:.83rem;padding:5px 12px">${status}</span>`;

    document.getElementById('regime-components').innerHTML = items.map(item => {
        const val = comp[item.key] ?? 10;
        const pct = val / 20 * 100;
        const barColor = val>=14?'var(--bull)':val<=7?'var(--bear)':'var(--warn)';
        return `<div class="score-row">
            <div>
                <div style="font-size:.84rem;font-weight:600">${item.name}</div>
                ${item.detail ? `<div style="font-size:.71rem;color:var(--sub)">${item.detail}</div>` : ''}
            </div>
            <div style="display:flex;align-items:center;gap:10px">
                <div class="score-bar-wrap"><div class="score-bar-fill" style="width:${pct}%;background:${barColor}"></div></div>
                <span class="mono" style="font-size:.86rem;font-weight:700;min-width:28px;text-align:right;color:${barColor}">${val}</span>
                <span style="font-size:.71rem;color:var(--sub)">/20</span>
            </div>
        </div>`;
    }).join('') + `<div style="margin-top:10px;padding:8px;background:var(--raised);border-radius:6px;display:flex;justify-content:space-between;align-items:center">
        <span style="font-size:.74rem;color:var(--sub)">Bear 임계값</span>
        <span class="mono" style="font-size:.78rem;font-weight:700;color:${bearThrColor}">${bearThr}점 (${bearPersistLabel})</span>
    </div>`;
}

// ─── Macro Tab ─────────────────────────────────────────────────────────────
async function fetchMacro() {
    try {
        const data = await apiFetch('/market/macro');
        macroCache = data;
        renderMacroDetail(data);
        renderEconTable(data.economic_indicators);
    } catch (e) { console.error(e); }
}

function renderMacroDetail(data) {
    const r = data.market_regime || {};
    const comp = r.components || {};
    const od   = comp.other_detail    || {};
    const td2  = comp.technical_detail || {};
    const score  = r.regime_score ?? 50;
    const status = r.status ?? 'Neutral';
    const color  = status==='Bull'?'var(--bull)':status==='Bear'?'var(--bear)':'var(--warn)';
    const bearThr2 = r.bear_threshold ?? 40;
    const bearThrColor2 = bearThr2>=48?'var(--bear)':bearThr2>=44?'var(--warn)':'var(--sub)';
    const bearPersistLabel2 = bearThr2>=48?'2개월 연속 Bear':bearThr2>=44?'1개월 전 Bear':'정상';

    document.getElementById('macro-regime-detail').innerHTML = `
        <div style="text-align:center;padding:12px 0">
            <div class="mono" style="font-size:3rem;font-weight:800;color:${color}">${score}</div>
            <div style="font-size:.95rem;color:var(--sub)">/ 100점 &nbsp;|&nbsp; Bear 임계값: <b class="mono" style="color:${bearThrColor2}">${bearThr2}</b></div>
            <span class="badge ${status==='Bull'?'b-up':status==='Bear'?'b-down':'b-warn'}" style="font-size:.86rem;padding:5px 14px;margin-top:8px;display:inline-block">${status}</span>
            ${bearThr2>40?`<div style="font-size:.72rem;color:${bearThrColor2};margin-top:5px">⚠️ Bear 지속성: ${bearPersistLabel2}</div>`:''}
        </div>
        <hr class="divider">
        <div style="font-size:.8rem;color:var(--sub);margin-bottom:8px">구성 요소 (각 0~20점)</div>
        ${['technical','vix','fear_greed','economic','other'].map(k => {
            const v = comp[k]??10;
            const barColor = v>=14?'var(--bull)':v<=7?'var(--bear)':'var(--warn)';
            const names = {technical:'기술 EMA',vix:'VIX',fear_greed:'Fear&Greed',economic:'경제지표',other:'기타'};
            const subDetail = k==='technical'?[
                td2.spx_1m_ret!=null?`1M ${fmtPct(td2.spx_1m_ret)}`:'',
                td2.spx_2w_ret!=null?`2W ${fmtPct(td2.spx_2w_ret)}`:'',
                td2.spx_from_ath!=null?`ATH대비 ${fmtPct(td2.spx_from_ath)}`:'',
            ].filter(Boolean).join(' | '):'';
            return `<div style="padding:6px 0">
                <div style="display:flex;justify-content:space-between;align-items:center">
                    <span style="font-size:.84rem">${names[k]}</span>
                    <div style="display:flex;align-items:center;gap:8px">
                        <div style="width:80px;height:6px;background:var(--raised);border-radius:3px">
                            <div style="width:${v/20*100}%;height:100%;border-radius:3px;background:${barColor}"></div>
                        </div>
                        <b class="mono" style="color:${barColor};min-width:20px;text-align:right">${v}</b>
                    </div>
                </div>
                ${subDetail?`<div style="font-size:.7rem;color:var(--sub);margin-top:2px">${subDetail}</div>`:''}
            </div>`;
        }).join('')}
        <hr class="divider">
        <div style="font-size:.82rem">
            ${od.us_10y_yield!=null?`<div class="score-row"><span style="color:var(--sub)">10Y 금리</span><b class="mono">${od.us_10y_yield}%</b></div>`:''}
            ${od.yield_spread_10y2y!=null?`<div class="score-row"><span style="color:var(--sub)">10Y-2Y 스프레드</span><b class="mono ${cls(od.yield_spread_10y2y)}">${fmtPct(od.yield_spread_10y2y)}</b></div>`:''}
            ${od.vix_1m_chg!=null?`<div class="score-row"><span style="color:var(--sub)">VIX 1개월 변화</span><b class="mono ${cls(-od.vix_1m_chg)}">${fmtPct(od.vix_1m_chg)}</b></div>`:''}
            ${od.btc_1m_ret!=null?`<div class="score-row"><span style="color:var(--sub)">BTC 1개월</span><b class="mono ${cls(od.btc_1m_ret)}">${fmtPct(od.btc_1m_ret)}</b></div>`:''}
            ${od.dxy_1m_ret!=null?`<div class="score-row"><span style="color:var(--sub)">DXY 1개월</span><b class="mono ${cls(-od.dxy_1m_ret)}">${fmtPct(od.dxy_1m_ret)}</b></div>`:''}
            ${od.gold_1m_ret!=null?`<div class="score-row"><span style="color:var(--sub)">Gold 1개월</span><b class="mono ${cls(-od.gold_1m_ret)}">${fmtPct(od.gold_1m_ret)}</b></div>`:''}
            ${od.forward_pe!=null?`
            <hr style="border:none;border-top:1px solid var(--divider);margin:6px 0">
            <div class="score-row"><span style="color:var(--sub)">S&P500 Forward P/E</span><b class="mono">${od.forward_pe.toFixed(1)}x</b></div>
            <div class="score-row"><span style="color:var(--sub)">5Y 평균 P/E</span><b class="mono">${(od.avg_5y_pe??18.5).toFixed(1)}x</b></div>
            <div class="score-row"><span style="color:var(--sub)">밸류에이션 편차</span><b class="mono ${od.forward_pe_raw>0?'up':od.forward_pe_raw<0?'down':''}">${od.forward_pe_deviation!=null?fmtPct(od.forward_pe_deviation*100):'-'} (${od.forward_pe_raw>0?'+':''}${od.forward_pe_raw??0}점)</b></div>`:''}
        </div>`;

    document.getElementById('macro-indicators').innerHTML = `
        <div class="score-row"><span style="color:var(--sub)">SPX 현재가</span><b class="mono">$${r.current?.toLocaleString()??'-'}</b></div>
        <div class="score-row"><span style="color:var(--sub)">SPX MA200</span><b class="mono">$${r.ma200?.toLocaleString()??'-'}</b></div>
        <div class="score-row"><span style="color:var(--sub)">MA200 대비</span><b class="mono ${cls(r.diff_pct||0)}">${fmtPct(r.diff_pct)}</b></div>
        <div class="score-row"><span style="color:var(--sub)">VIX</span><b class="mono ${(data.vix||0)>25?'down':(data.vix||0)<15?'up':''}">${data.vix??'-'}</b></div>
        <div class="score-row"><span style="color:var(--sub)">Fear&Greed</span><b class="mono">${data.fear_greed??'-'}/100</b></div>
        <div class="score-row"><span style="color:var(--sub)">US 10Y 금리</span><b class="mono">${data.us_10y_yield??'-'}%</b></div>
        ${['BTC','Gold','Oil'].map(k=>{
            const item = k==='BTC'?(data.crypto||{}).BTC:(data.commodities||{})[k];
            if (!item) return '';
            return `<div class="score-row"><span style="color:var(--sub)">${k}</span><b class="mono">${fmtCurr(item.price,'$')} <span class="mono ${cls(item.change||0)}" style="font-size:.8rem">${fmtPct(item.change)}</span></b></div>`;
        }).join('')}
        ${Object.entries(data.indices||{}).map(([name,info])=>
            `<div class="score-row"><span style="color:var(--sub)">${name}</span><b class="mono">${info.price?.toLocaleString()??'-'} <span class="${cls(info.change||0)}" style="font-size:.8rem">${fmtPct(info.change)}</span></b></div>`
        ).join('')}`;
}

function renderEconTable(econ) {
    const tbody = document.getElementById('econ-tbody');
    const inds  = econ?.indicators;
    if (!inds) { tbody.innerHTML = '<tr><td colspan="7" class="empty">데이터 없음</td></tr>'; return; }
    tbody.innerHTML = Object.entries(inds).map(([k, v]) => {
        const delta = v.delta;
        const statusCls = v.status==='positive'?'b-up':v.status==='negative'?'b-down':'b-gray';
        const statusTxt = v.status==='positive'?'긍정':v.status==='negative'?'부정':'중립';
        return `<tr>
            <td><b>${v.name}</b><span class="sub-text">${v.series_id}</span></td>
            <td class="mono">${v.latest??'-'}</td>
            <td class="mono">${v.previous??'-'}</td>
            <td class="mono ${delta!=null?cls(delta):''}">${delta!=null?fmtPct(delta/Math.abs(v.previous||1)*100,true):'-'}</td>
            <td><span class="badge ${delta!=null?(delta>0?'b-up':'b-down'):'b-gray'}">${delta!=null?(delta>0?'▲':'▼'):'-'}</span></td>
            <td style="color:var(--sub)">${v.weight}</td>
            <td><span class="badge ${statusCls}">${statusTxt}</span></td>
        </tr>`;
    }).join('');
}

// ─── Market Watch ──────────────────────────────────────────────────────────
let marketFilter = 'all';
let top20Cache   = null;

function setMarketFilter(f) {
    marketFilter = f;
    ['all','us','kr'].forEach(id => {
        const btn = document.getElementById('mf-' + id);
        if (btn) btn.className = id===f ? 'btn btn-primary btn-sm' : 'btn btn-outline btn-sm';
    });
    renderTop20Filtered();
    renderSignalsFiltered();
}

function renderTop20Filtered() {
    const tbody = document.getElementById('top20-tbody');
    if (!top20Cache) return;
    let entries = Object.entries(top20Cache).filter(([k]) => !k.startsWith('message'));
    if (marketFilter === 'us') entries = entries.filter(([t]) => !/^\d{6}$/.test(t));
    if (marketFilter === 'kr') entries = entries.filter(([t]) =>  /^\d{6}$/.test(t));
    const q = searchState['top20-tbody'];
    if (q) entries = entries.filter(([t,i]) => t.toLowerCase().includes(q)||(i.name||'').toLowerCase().includes(q));
    entries = applySortToData('top20-tbody', entries);
    if (!entries.length) {
        tbody.innerHTML = `<tr><td colspan="9" class="empty">${marketFilter==='all'?'수집 중...':'해당 시장 종목 없음'}</td></tr>`;
        return;
    }
    tbody.innerHTML = entries.map(([ticker, info]) => {
        const rsi     = info.rsi;
        const rsiCls  = rsi<30?'b-up':rsi>70?'b-down':'b-gray';
        const sig     = rsi<30?'<span class="badge b-up">BUY</span>':rsi>70?'<span class="badge b-down">SELL</span>':'-';
        const krStock = /^\d{6}$/.test(ticker);
        const priceStr = krStock ? '₩'+Math.round(info.price||0).toLocaleString() : '$'+fmt(info.price);
        const updatedStr = info.last_updated
            ? new Date(info.last_updated).toLocaleTimeString('ko-KR',{hour:'2-digit',minute:'2-digit',second:'2-digit'})
            : '-';
        const score = info.score ?? 0;
        const scoreCls = score<=30?'b-up':score>=70?'b-down':'b-gray';
        return `<tr>
            <td class="ticker-cell">${ticker}</td>
            <td style="font-size:.8rem;color:var(--sub)">${info.name||''}</td>
            <td class="mono">${priceStr}</td>
            <td class="mono ${cls(info.change_pct||0)}">${fmtPct(info.change_pct)}</td>
            <td><span class="badge ${rsiCls}">${fmt(rsi,1)}</span></td>
            <td><span class="badge ${scoreCls} mono">${score}</span></td>
            <td>${sig}</td>
            <td class="mono sub-text">${updatedStr}</td>
            <td style="white-space:nowrap">
                <button class="btn btn-outline btn-sm" onclick="openTradeFor('${ticker}')" style="padding:2px 6px;font-size:.72rem;margin-right:4px">매수</button>
                <button class="btn btn-outline btn-sm" onclick="openDcfSimulator('${ticker}')" style="padding:2px 6px;font-size:.72rem">DCF</button>
            </td>
        </tr>`;
    }).join('');
}

async function fetchTop20() {
    try { top20Cache = await apiFetch('/market/monitored'); renderTop20Filtered(); }
    catch (e) { console.error(e); }
}

let signalsCache = null;

function renderSignalsFiltered() {
    const tbody = document.getElementById('signals-tbody');
    if (!signalsCache) return;
    const data = signalsCache;
    const getName  = (t) => top20Cache?.[t]?.name || '';
    const sigQ     = searchState['signals-tbody'];
    const filterTicker = (t) => {
        const kr = /^\d{6}$/.test(String(t));
        if (marketFilter==='us' && kr)  return false;
        if (marketFilter==='kr' && !kr) return false;
        if (sigQ && !String(t).toLowerCase().includes(sigQ) && !(getName(t)||'').toLowerCase().includes(sigQ)) return false;
        return true;
    };
    const fmtSigPrice = (t, p) => {
        if (p==null||p===0) return '-';
        return /^\d{6}$/.test(String(t)) ? '₩'+Math.round(p).toLocaleString() : '$'+fmt(p);
    };
    const rows = [];
    (data.oversold||[]).filter(i=>filterTicker(i.ticker)&&(i.rsi||0)>0).forEach(i=>{
        const score = top20Cache?.[i.ticker]?.score??'-';
        rows.push(`<tr>
            <td><span class="badge b-up" style="white-space:nowrap">과매도</span></td>
            <td class="ticker-cell">${i.ticker}</td>
            <td style="font-size:.78rem;color:var(--sub)">${getName(i.ticker)}</td>
            <td><span class="badge b-gray mono">${score}</span></td>
            <td>RSI <b class="mono up">${fmt(i.rsi,1)}</b></td>
            <td class="mono">${fmtSigPrice(i.ticker,i.price)}</td>
        </tr>`);
    });
    (data.overbought||[]).filter(i=>filterTicker(i.ticker)&&(i.rsi||0)>0).forEach(i=>{
        const score = top20Cache?.[i.ticker]?.score??'-';
        rows.push(`<tr>
            <td><span class="badge b-down" style="white-space:nowrap">과매수</span></td>
            <td class="ticker-cell">${i.ticker}</td>
            <td style="font-size:.78rem;color:var(--sub)">${getName(i.ticker)}</td>
            <td><span class="badge b-gray mono">${score}</span></td>
            <td>RSI <b class="mono down">${fmt(i.rsi,1)}</b></td>
            <td class="mono">${fmtSigPrice(i.ticker,i.price)}</td>
        </tr>`);
    });
    (data.undervalued||[]).filter(i=>filterTicker(i.ticker)&&(i.price||0)>0).forEach(i=>{
        const score = top20Cache?.[i.ticker]?.score??'-';
        rows.push(`<tr>
            <td><span class="badge b-warn" style="white-space:nowrap">저평가</span></td>
            <td class="ticker-cell">${i.ticker}</td>
            <td style="font-size:.78rem;color:var(--sub)">${getName(i.ticker)}</td>
            <td><span class="badge b-gray mono">${score}</span></td>
            <td>여유 <b class="mono up">+${fmt(i.upside_pct,1)}%</b></td>
            <td class="mono">${fmtSigPrice(i.ticker,i.price)}</td>
        </tr>`);
    });
    (data.ema200_support||[]).filter(i=>filterTicker(i.ticker)&&(i.price||0)>0).forEach(i=>{
        const score = top20Cache?.[i.ticker]?.score??'-';
        rows.push(`<tr>
            <td><span class="badge b-gray" style="white-space:nowrap">EMA200</span></td>
            <td class="ticker-cell">${i.ticker}</td>
            <td style="font-size:.78rem;color:var(--sub)">${getName(i.ticker)}</td>
            <td><span class="badge b-gray mono">${score}</span></td>
            <td style="font-size:.77rem;color:var(--sub)">EMA ${fmtSigPrice(i.ticker,i.ema200)}</td>
            <td class="mono">${fmtSigPrice(i.ticker,i.price)}</td>
        </tr>`);
    });
    tbody.innerHTML = rows.length ? rows.join('') : '<tr><td colspan="6" class="empty">신호 없음</td></tr>';
}

async function fetchSignals() {
    try { signalsCache = await apiFetch('/market/signals'); renderSignalsFiltered(); }
    catch (e) { console.error(e); }
}

async function searchNews() {
    const ticker = document.getElementById('news-ticker').value.trim();
    if (!ticker) return;
    const el = document.getElementById('news-results');
    el.innerHTML = '검색 중...';
    try {
        const data = await apiFetch('/market/news/' + encodeURIComponent(ticker));
        if (!data?.length) { el.innerHTML = '결과 없음'; return; }
        el.innerHTML = data.map(n => `
            <div style="padding:10px 0;border-bottom:1px solid var(--border)">
                <a href="${n.link||n.url||'#'}" target="_blank" style="color:var(--accent);text-decoration:none;font-weight:500;font-size:.88rem">${n.title}</a>
                <div style="font-size:.74rem;color:var(--sub);margin-top:3px">${n.publisher||n.source||'-'} · ${(n.date||n.published_at||'').slice(0,10)}</div>
            </div>`).join('');
    } catch (e) { el.innerHTML = '오류: ' + e.message; }
}

// ─── Trading Control ───────────────────────────────────────────────────────
async function fetchWaitingList() {
    try {
        const data = await apiFetch('/trading/waiting-list');
        const enabled = data?.enabled ?? data?.strategy_enabled ?? null;
        if (enabled !== null) updateStrategyChip(enabled);
        const renderList = (items) => items?.length
            ? items.map(i => `<div style="padding:5px 0;border-bottom:1px solid var(--border);font-size:.84rem">
                <b class="ticker-cell" style="font-size:.84rem">${i.ticker}</b> — ${i.reason||i.signal||''} <span style="color:var(--sub)">${i.score!=null?'점수:'+i.score:''}</span>
              </div>`).join('')
            : '<div class="empty">없음</div>';
        document.getElementById('wait-buy').innerHTML  = renderList(data?.buy_list??data?.buy);
        document.getElementById('wait-sell').innerHTML = renderList(data?.sell_list??data?.sell);
    } catch (e) { console.error(e); }
}

async function executeSell() {
    const ticker = document.getElementById('sell-ticker').value.trim();
    const qty    = parseInt(document.getElementById('sell-qty').value) || 0;
    if (!ticker) { showToast('티커를 입력하세요', false); return; }
    if (!confirm(`${ticker} ${qty||'전략 자동'}주 매도하시겠습니까?`)) return;
    try {
        await apiFetch('/trading/sell', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ ticker, quantity: qty }) });
        showToast('매도 완료');
    } catch (e) { showToast(e.message, false); }
}

async function calcTickerScore() {
    const ticker = document.getElementById('score-ticker').value.trim();
    const el     = document.getElementById('score-result');
    if (!ticker) { el.innerHTML = '<span style="color:var(--sub)">티커를 입력하세요</span>'; return; }
    el.innerHTML = '<span style="color:var(--sub)">조회 중...</span>';
    try {
        const d = await apiFetch('/analysis/score/' + encodeURIComponent(ticker));
        const recColor = d.recommendation==='BUY'?'var(--bull)':d.recommendation==='SELL'?'var(--bear)':'var(--sub)';
        const fmtPrice = d.current_price!=null?(d.current_price>999?d.current_price.toLocaleString():d.current_price.toFixed(2)):'-';
        const fmtDcf   = d.dcf_value!=null?(d.dcf_value>999?Math.round(d.dcf_value).toLocaleString():d.dcf_value.toFixed(2)):'-';
        const reasons  = (d.reasons||[]).map(r=>'<li style="margin:2px 0">'+r+'</li>').join('');
        el.innerHTML = `
            <div style="font-weight:600;margin-bottom:8px">${d.name||d.ticker} <span style="color:${recColor};font-size:1.1em">${d.recommendation}</span></div>
            <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;text-align:center;margin-bottom:8px">
                <div><div style="color:var(--sub);font-size:.83em">점수</div><div class="mono" style="font-size:1.2em;font-weight:600">${d.score}</div></div>
                <div><div style="color:var(--sub);font-size:.83em">현재가</div><div class="mono">${fmtPrice}</div></div>
                <div><div style="color:var(--sub);font-size:.83em">RSI</div><div class="mono">${d.rsi!=null?d.rsi.toFixed(1):'-'}</div></div>
                <div><div style="color:var(--sub);font-size:.83em">DCF</div><div class="mono">${fmtDcf}</div></div>
            </div>
            ${reasons?'<ul style="margin:0;padding-left:18px;font-size:.88em;color:var(--text)">'+reasons+'</ul>':''}`;
    } catch (e) { el.innerHTML = `<span style="color:var(--bear)">❌ ${e.message}</span>`; }
}

async function placeOrder() {
    const ticker = document.getElementById('order-ticker').value.trim();
    const qty    = parseInt(document.getElementById('order-qty').value);
    const price  = parseFloat(document.getElementById('order-price').value);
    const type   = document.getElementById('order-type').value;
    if (!ticker||!qty) { showToast('티커와 수량을 입력하세요', false); return; }
    if (!confirm(`${ticker} ${qty}주 ${type.toUpperCase()} (${price||'시장가'}) 주문하시겠습니까?`)) return;
    try {
        await apiFetch('/trading/order', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ ticker, quantity:qty, price, order_type:type }) });
        showToast('주문 완료');
    } catch (e) { showToast(e.message, false); }
}

async function confirmSellAll() {
    if (!confirm('⚠️ 모든 보유 종목을 전량 매도하고 전략에 따라 재매수합니다.\n정말 실행하시겠습니까?')) return;
    if (!confirm('최종 확인: 이 작업은 되돌릴 수 없습니다.')) return;
    try {
        showToast('실행 중...');
        const data = await apiFetch('/trading/sell-all-and-rebuy', { method: 'POST' });
        showToast(data.message || '완료');
    } catch (e) { showToast(e.message, false); }
}

// ─── Settings ─────────────────────────────────────────────────────────────
async function fetchSettings() {
    try {
        const data = await apiFetch('/trading/settings');
        document.getElementById('settings-tbody').innerHTML = data.map(s => `<tr>
            <td style="font-size:.8rem;font-weight:600;font-family:var(--mono)">${s.key}</td>
            <td class="mono" style="font-size:.83rem">${s.value??s.val??'-'}</td>
            <td><button class="btn btn-outline btn-sm" onclick="openSettingEdit('${s.key}','${s.value??s.val??''}')">수정</button></td>
        </tr>`).join('');
    } catch (e) { console.error(e); }
}

function openSettingEdit(key, val) {
    document.getElementById('setting-key').value       = key;
    document.getElementById('setting-key-label').textContent = key;
    document.getElementById('setting-val').value       = val;
    openModal('settingModal');
}

async function saveSetting() {
    const key = document.getElementById('setting-key').value;
    const val = document.getElementById('setting-val').value;
    try {
        await apiFetch('/trading/settings', { method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ key, value: val }) });
        showToast('저장됨');
        closeModal('settingModal');
        fetchSettings();
    } catch (e) { showToast(e.message, false); }
}

// ─── Alerts ───────────────────────────────────────────────────────────────
async function fetchAlerts() {
    try {
        const data = await apiFetch('/alerts/pending');
        const el  = document.getElementById('alerts-list');
        const alerts = data?.alerts ?? data?.pending ?? [];
        el.innerHTML = alerts.length
            ? alerts.map(a => `<div style="padding:8px;background:var(--raised);border-radius:6px;margin-bottom:6px;font-size:.83rem">
                <b class="ticker-cell" style="font-size:.83rem">${a.ticker}</b> — ${a.condition} $${a.price}
              </div>`).join('')
            : '<div style="color:var(--sub)">활성 알림 없음</div>';
    } catch (e) { console.error(e); }
}

async function addAlert() {
    const ticker = document.getElementById('alert-ticker').value.trim();
    const cond   = document.getElementById('alert-cond').value;
    const price  = parseFloat(document.getElementById('alert-price').value);
    if (!ticker||!price) { showToast('티커와 가격을 입력하세요', false); return; }
    try {
        await apiFetch('/alerts', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ ticker, condition:cond, price }) });
        showToast('알림 설정됨');
        fetchAlerts();
    } catch (e) { showToast(e.message, false); }
}

// ─── Logs ──────────────────────────────────────────────────────────────────
let _logAutoRefreshTimer = null;

async function fetchLogs() {
    const search = encodeURIComponent(document.getElementById('log-search')?.value||'');
    const level  = encodeURIComponent(document.getElementById('log-level')?.value||'');
    const lines  = document.getElementById('log-lines')?.value||'200';
    const el     = document.getElementById('log-output');
    if (!el) return;
    try {
        const data = await apiFetch(`/logs?lines=${lines}&level=${level}&search=${search}`);
        const levelColor = { DEBUG:'#5a6e8a', INFO:'#6baed6', WARNING:'var(--warn)', ERROR:'var(--bear)' };
        el.innerHTML = data.length===0
            ? '<span style="color:var(--sub)">로그 없음</span>'
            : data.map(line => {
                const lv = ['ERROR','WARNING','INFO','DEBUG'].find(l => line.includes(` - ${l} - `));
                const color = levelColor[lv]||'#8898aa';
                const escaped = line.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
                return `<span style="color:${color}">${escaped}</span>`;
            }).join('\n');
        scrollLogToBottom();
    } catch (e) { el.innerHTML = `<span style="color:var(--bear)">로그 조회 실패: ${e.message}</span>`; }
}

function scrollLogToBottom() { const el = document.getElementById('log-output'); if (el) el.scrollTop = el.scrollHeight; }

function toggleLogAutoRefresh() {
    const checked = document.getElementById('log-auto-refresh')?.checked;
    if (_logAutoRefreshTimer) { clearInterval(_logAutoRefreshTimer); _logAutoRefreshTimer = null; }
    if (checked) _logAutoRefreshTimer = setInterval(fetchLogs, 5000);
}

// ─── DCF ───────────────────────────────────────────────────────────────────
function updateDcfLabel() {
    document.getElementById('dcf-growth-val').textContent   = (parseFloat(document.getElementById('dcf-growth').value)*100).toFixed(0);
    document.getElementById('dcf-discount-val').textContent = (parseFloat(document.getElementById('dcf-discount').value)*100).toFixed(1);
    document.getElementById('dcf-terminal-val').textContent = (parseFloat(document.getElementById('dcf-terminal').value)*100).toFixed(1);
}

async function calcDcf() {
    const ticker   = document.getElementById('dcf-ticker').value.trim();
    const growth   = parseFloat(document.getElementById('dcf-growth').value);
    const discount = parseFloat(document.getElementById('dcf-discount').value);
    const terminal = parseFloat(document.getElementById('dcf-terminal').value);
    if (!ticker) { showToast('티커를 입력하세요', false); return; }
    try {
        const data = await apiFetch(`/analysis/dcf-custom?ticker=${encodeURIComponent(ticker)}&growth_rate=${growth}&discount_rate=${discount}&terminal_growth=${terminal}`);
        const el = document.getElementById('dcf-result');
        el.style.display = 'block';
        document.getElementById('dcf-result-val').textContent  = '$' + (data.fair_value??data.dcf_value??0).toFixed(2);
        document.getElementById('dcf-result-note').textContent = `성장률 ${(growth*100).toFixed(0)}% / WACC ${(discount*100).toFixed(1)}% / 터미널 ${(terminal*100).toFixed(1)}%`;
    } catch (e) { showToast(e.message, false); }
}

function openDcfSimulator(ticker) {
    document.getElementById('dcf-ticker').value = ticker;
    const isKrTicker = /^\d{6}$/.test(ticker);
    document.getElementById('dcf-growth').value = isKrTicker ? '0.05' : '0.10';
    updateDcfLabel();
    document.getElementById('dcf-ticker').scrollIntoView({ behavior:'smooth', block:'center' });
    calcDcf();
}

// ─── DCF Override ──────────────────────────────────────────────────────────
function openDcfSetModal(ticker, symStr) {
    document.getElementById('dcf-override-ticker').value              = ticker;
    document.getElementById('dcf-override-ticker-label').textContent  = ticker;
    document.getElementById('dcf-override-sym').textContent           = symStr||'';
    document.getElementById('dcf-override-val').value    = '';
    document.getElementById('dcf-override-growth').value = '';
    openModal('dcfOverrideModal');
}

async function saveDcfOverride() {
    const ticker  = document.getElementById('dcf-override-ticker').value;
    const fairVal = parseFloat(document.getElementById('dcf-override-val').value)||null;
    const growth  = parseFloat(document.getElementById('dcf-override-growth').value);
    if (!ticker) return;
    if (!fairVal&&!growth) { showToast('적정가 또는 성장률을 입력하세요', false); return; }
    try {
        const payload = { ticker };
        if (fairVal) payload.fair_value  = fairVal;
        if (growth)  payload.growth_rate = growth / 100;
        await apiFetch('/analysis/dcf-override', { method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) });
        showToast('DCF 설정 저장됨');
        closeModal('dcfOverrideModal');
        fetchPortfolioFull();
    } catch (e) { showToast(e.message, false); }
}

// ─── Trade Modal ───────────────────────────────────────────────────────────
function openTradeModal()       { openModal('tradeModal'); setTradeMode('buy'); }
function openTradeFor(ticker)   { openTradeModal(); document.getElementById('tx-ticker').value = ticker; }
function openSellModal(ticker)  { openModal('tradeModal'); setTradeMode('sell'); document.getElementById('tx-ticker').value = ticker; }
function closeTradeModal()      { closeModal('tradeModal'); }

function setTradeMode(mode) {
    tradeMode = mode;
    const b = document.getElementById('btn-buy-mode');
    const s = document.getElementById('btn-sell-mode');
    if (mode === 'buy') {
        b.className = 'btn btn-success'; b.style.color = '';
        s.className = 'btn btn-outline'; s.style.color = 'var(--sub)';
    } else {
        s.className = 'btn btn-danger';  s.style.color = '';
        b.className = 'btn btn-outline'; b.style.color = 'var(--sub)';
    }
}

async function submitTrade() {
    const ticker = document.getElementById('tx-ticker').value.trim();
    const qty    = document.getElementById('tx-qty').value;
    const price  = document.getElementById('tx-price').value;
    if (!ticker||!qty||!price) { showToast('모든 항목을 입력하세요', false); return; }
    try {
        await apiFetch(`/portfolio/${USER}/trade?ticker=${encodeURIComponent(ticker)}&action=${tradeMode}&quantity=${qty}&price=${price}`, { method:'POST' });
        showToast('기록 완료');
        closeTradeModal();
        fetchPortfolioFull();
    } catch (e) { showToast(e.message, false); }
}

// ─── Excel Upload ──────────────────────────────────────────────────────────
async function uploadExcel() {
    const input = document.getElementById('excelInput');
    if (!input.files?.[0]) return;
    const fd = new FormData();
    fd.append('file', input.files[0]);
    fd.append('user_id', USER);
    try {
        showToast('업로드 중...');
        const res = await fetch(API + '/portfolio/upload', { method:'POST', body:fd });
        if (!res.ok) throw new Error((await res.json()).detail||'실패');
        const data = await res.json();
        showToast(data.message||'업로드 완료');
        fetchPortfolioFull();
    } catch (e) { showToast(e.message, false); }
    finally { input.value = ''; }
}

// ─── Watchlist ─────────────────────────────────────────────────────────────
let _watchlistCache = [];

async function fetchWatchlist() {
    const tbody = document.getElementById('watchlist-tbody');
    if (tbody) tbody.innerHTML = '<tr><td colspan="4" class="empty">불러오는 중...</td></tr>';
    try {
        const data = await apiFetch(`/watchlist/${USER}`);
        _watchlistCache = data.tickers || [];
        renderWatchlistTable(_watchlistCache);
    } catch (e) {
        if (tbody) tbody.innerHTML = `<tr><td colspan="4" class="empty" style="color:var(--bear)">오류: ${e.message}</td></tr>`;
    }
}

function renderWatchlistTable(tickers) {
    const tbody = document.getElementById('watchlist-tbody');
    if (!tbody) return;
    if (!tickers?.length) { tbody.innerHTML = '<tr><td colspan="4" class="empty">Watchlist가 비어있습니다.<br>종목을 추가하면 자동매매가 시작됩니다.</td></tr>'; return; }
    tbody.innerHTML = tickers.map(item => {
        const ticker  = typeof item === 'string' ? item : item.ticker;
        const addedAt = typeof item === 'object' && item.added_at ? item.added_at.slice(0,10) : '-';
        const isKrT   = /^\d{6}$/.test(ticker);
        return `<tr>
            <td class="ticker-cell">${ticker}</td>
            <td><span class="badge ${isKrT ? 'b-warn' : 'b-up'}">${isKrT ? '🇰🇷 KR' : '🇺🇸 US'}</span></td>
            <td class="mono sub-text">${addedAt}</td>
            <td>
                <button class="btn btn-danger btn-sm" onclick="removeWatchlistTicker('${ticker}')">제거</button>
            </td>
        </tr>`;
    }).join('');
}

async function addWatchlistTicker() {
    const input  = document.getElementById('wl-ticker-input');
    let ticker   = (input?.value || '').trim().toUpperCase();
    if (!ticker) { showToast('티커를 입력하세요', false); return; }
    // KR normalization: 숫자만이면 6자리 zero-fill
    if (/^\d+$/.test(ticker) && ticker.length < 6) ticker = ticker.padStart(6, '0');
    try {
        const res = await apiFetch(`/watchlist/${USER}/${ticker}`, { method: 'POST' });
        if (res.status === 'already_exists') { showToast(`${ticker}은 이미 Watchlist에 있습니다`, false); return; }
        showToast(`${ticker} 추가됨`);
        if (input) input.value = '';
        fetchWatchlist();
    } catch (e) { showToast(e.message, false); }
}

async function removeWatchlistTicker(ticker) {
    if (!confirm(`${ticker}를 Watchlist에서 제거하시겠습니까?`)) return;
    try {
        await apiFetch(`/watchlist/${USER}/${ticker}`, { method: 'DELETE' });
        showToast(`${ticker} 제거됨`);
        fetchWatchlist();
    } catch (e) { showToast(e.message, false); }
}

// ─── Strategy Mode ─────────────────────────────────────────────────────────
async function fetchStrategyMode() {
    try {
        const data = await apiFetch(`/watchlist/${USER}/mode`);
        _updateModeBtns('kr', data.kr_strategy_mode);
        _updateModeBtns('us', data.us_strategy_mode);
    } catch (e) { /* 탭 첫 진입 오류 무시 */ }
}

async function setStrategyMode(market, mode) {
    try {
        const data = await apiFetch(`/watchlist/${USER}/mode`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ market, mode }),
        });
        _updateModeBtns('kr', data.kr_strategy_mode);
        _updateModeBtns('us', data.us_strategy_mode);
        const label = mode === 'watchlist' ? '내 Watchlist 종목만 자동매매' : 'Top100 전체 자동매매';
        showToast(`${market.toUpperCase()} 전략: ${label}`);
    } catch (e) { showToast(e.message, false); }
}

function _updateModeBtns(market, mode) {
    const t = document.getElementById(`${market}-mode-top100`);
    const w = document.getElementById(`${market}-mode-watchlist`);
    if (!t || !w) return;
    t.className = mode === 'top100'    ? 'btn btn-primary btn-sm' : 'btn btn-outline btn-sm';
    w.className = mode === 'watchlist' ? 'btn btn-primary btn-sm' : 'btn btn-outline btn-sm';
    const hints = {
        top100:    'Top100 전체가 자동매매 대상입니다',
        watchlist: '내 Watchlist 종목만 자동매매됩니다 (Top100은 점수 계산 생략)',
    };
    const hint = document.getElementById(`${market}-mode-hint`);
    if (hint) hint.textContent = hints[mode] || '';
}

// ─── Modal Helpers ─────────────────────────────────────────────────────────
function openModal(id)  { document.getElementById(id).classList.add('open'); }
function closeModal(id) { document.getElementById(id).classList.remove('open'); }
window.addEventListener('click', e => {
    document.querySelectorAll('.modal-backdrop.open').forEach(m => { if (e.target === m) m.classList.remove('open'); });
});

// ─── Dashboard market filter ────────────────────────────────────────────
function setDashFilter(market) {
    dashMarketFilter = market;
    ['all','kr','us'].forEach(id => {
        const btn = document.getElementById('df-' + id);
        if (btn) btn.className = id===market ? 'btn btn-primary btn-sm' : 'btn btn-outline btn-sm';
    });
    if (_balanceDataCache) renderBalanceContent(_balanceDataCache);
    else fetchBalance();
    if (_portfolioDataCache) renderPortfolioTable(_portfolioDataCache);
}

// ─── SSE 실시간 가격 스트림 ───────────────────────────────────────────────────
let _priceStream = null;
let _portfolioRenderTimer = null;

function connectPriceStream() {
    if (_priceStream) _priceStream.close();
    _priceStream = new EventSource(API + '/market/stream');

    _priceStream.onmessage = (e) => {
        let changes;
        try { changes = JSON.parse(e.data); } catch { return; }

        // ─ top20Cache 업데이트
        if (top20Cache) {
            for (const [ticker, info] of Object.entries(changes)) {
                if (top20Cache[ticker]) {
                    top20Cache[ticker].price      = info.price;
                    top20Cache[ticker].change_pct = info.change_pct;
                    top20Cache[ticker].change     = info.change;
                    top20Cache[ticker].rsi        = info.rsi;
                }
            }
            const active = document.querySelector('.nav-item.active')?.dataset?.tab;
            if (active === 'market') renderTop20Filtered();
        }

        // ─ 포트폴리오 캐시 업데이트 (debounce 300ms)
        if (_portfolioDataCache) {
            let updated = false;
            for (const holding of _portfolioDataCache) {
                const upd = changes[holding.ticker];
                if (!upd) continue;
                holding.price         = upd.price;
                holding.current_price = upd.price;
                holding.change_pct    = upd.change_pct;
                const qty = holding.quantity || 0;
                holding.current_value = upd.price * qty;
                holding.profit_loss   = holding.current_value - (holding.buy_price || 0) * qty;
                holding.return_pct    = holding.buy_price > 0
                    ? ((upd.price - holding.buy_price) / holding.buy_price * 100)
                    : 0;
                updated = true;
            }
            if (updated) {
                clearTimeout(_portfolioRenderTimer);
                _portfolioRenderTimer = setTimeout(() => {
                    const active = document.querySelector('.nav-item.active')?.dataset?.tab;
                    if (active === 'dashboard') renderPortfolioTable(_portfolioDataCache);
                }, 300);
            }
        }

        setUpdated();
    };

    _priceStream.onerror = () => {
        // EventSource가 자동 재연결 처리 (3초 후)
    };
}

// ─── Init ──────────────────────────────────────────────────────────────────
async function initApp() {
    if (typeof lucide !== 'undefined') lucide.createIcons();
    await Promise.all([
        fetchBalance(),
        fetchMacroBar(),
        fetchStrategyStatus(),
        fetchSectorWeightsMini(),
        fetchPortfolioFull(),
    ]);
    connectPriceStream();
    setInterval(() => {
        const active = document.querySelector('.nav-item.active')?.dataset?.tab;
        // SSE가 실시간 가격을 처리하므로 풀 refresh만 주기적으로 수행
        if (active === 'dashboard') { fetchBalance(); }
        if (active === 'market')    { fetchSignals(); }
    }, 120000);
}

// ─── Bootstrap ────────────────────────────────────────────────────────────
(async function bootstrap() {
    try {
        await apiFetch('/market/macro');
        hideLogin();
        initApp();
    } catch (e) { showLogin(); }
})();

async function resetAllCooldowns() {
    if (!confirm('모든 종목의 매수/매도 쿨다운을 초기화하시겠습니까?')) return;
    try {
        await apiFetch('/trading/cooldown/reset', { 
            method: 'POST', 
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({}) 
        });
        showToast('전체 쿨다운 초기화됨');
        fetchPortfolioFull();
    } catch (e) { showToast(e.message, false); }
}

async function resetTickerCooldown(ticker, action) {
    try {
        await apiFetch('/trading/cooldown/reset', { 
            method: 'POST', 
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ticker: ticker, action: action }) 
        });
        showToast(`${ticker} ${action === 'buy' ? '매수' : '매도'} 쿨다운 해제`);
        fetchPortfolioFull();
    } catch (e) { showToast(e.message, false); }
}
