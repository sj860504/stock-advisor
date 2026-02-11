class ReportService:
    """
    Slack 硫붿떆吏 諛?由ы룷???띿뒪???앹꽦 ?꾨떞 ?쒕퉬??
    (?곗씠?곕? 諛쏆븘???덉걶 臾몄옄?대줈 蹂??
    """

    @staticmethod
    def format_comprehensive_report(data: dict) -> str:
        """醫낇빀 遺꾩꽍 ?곗씠?곕? Slack 硫붿떆吏???띿뒪?몃줈 蹂??""
        if "error" in data: return f"??遺꾩꽍 ?ㅽ뙣: {data['error']}"
        
        p = data.get('price_info', {})
        f = data.get('fundamental', {})
        t = data.get('technical', {})
        port = data.get('portfolio', {})
        m = data.get('macro_context', {})
        
        msg = f"?뵇 **[{data.get('name')} ({data.get('ticker')})] 醫낇빀 遺꾩꽍 由ы룷??*\n\n"
        
        # 1. 媛寃?諛??ы듃?대━??
        change_icon = "??" if p.get('change_pct', 0) > 0 else "?뱣"
        msg += f"?뮥 **?꾩옱媛**: ${p.get('current')} ({p.get('change_pct', 0):+.2f}%) {change_icon}\n"
        if port.get('owned'):
            msg += f"?몳 **?섏쓽 ?됰떒**: ${port.get('avg_cost')} (?꾩옱 ?섏씡瑜? {port.get('return_pct', 0):+.2f}%)\n"
        msg += "\n"
        
        # 2. 媛移??됯?
        msg += "?뭿 **?댁옱 媛移?遺꾩꽍**\n"
        dcf_fair = f.get('dcf_fair', 'N/A')
        upside = f.get('upside_dcf', 0)
        msg += f"??DCF ?곸젙媛: **${dcf_fair}** (?곸듅?щ젰 {upside:+.1f}%)\n"
        
        target = f.get('analyst_target')
        if target:
            t_upside = f.get('upside_analyst', 0)
            msg += f"??湲곌? 紐⑺몴媛: **${target}** (?곸듅?щ젰 {t_upside:+.1f}%)\n\n"
        
        # 3. 湲곗닠??吏??
        rsi = t.get('rsi', 50)
        rsi_status = "?뵶 怨쇰ℓ?? if rsi > 70 else ("?윟 怨쇰ℓ?? if rsi < 30 else "??以묐┰")
        msg += f"?뱢 **湲곗닠??吏??*\n"
        msg += f"??RSI: {rsi} ({rsi_status})\n"
        
        emas = t.get('emas', {})
        current = p.get('current', 0)
        ema200 = emas.get('ema200')
        if ema200:
            dist = round((current - ema200)/ema200*100, 1)
            msg += f"??EMA200 ?鍮? {dist:+.1f}% ({'?뺣같?? if current > ema200 else '??같??})\n\n"
        
        # 4. 嫄곗떆 ?섍꼍
        if m:
            msg += f"?뙇 **嫄곗떆 ?섍꼍**: {m.get('regime')} Market (VIX: {m.get('vix')})\n\n"
        
        # 5. ?댁뒪
        if 'news_summary' in data:
            msg += data['news_summary']
        
        # 6. 寃곕줎
        conclusion = "?먮떒 ?좊낫"
        if isinstance(upside, (int, float)) and upside > 20 and rsi < 40: 
            conclusion = "?뵦 **媛뺣젰 留ㅼ닔 李ъ뒪 (??됯?+怨쇰ℓ??**"
        elif isinstance(upside, (int, float)) and upside > 10: 
            conclusion = "??**留ㅼ닔 怨좊젮 (??됯?)**"
        elif rsi > 75: 
            conclusion = "?좑툘 **留ㅻ룄/?듭젅 怨좊젮 (?④린 怨쇱뿴)**"
        else: 
            conclusion = "?? **蹂댁쑀 諛?愿留?*"
        
        msg += f"\n?뮕 **AI 寃곕줎**: {conclusion}"
        
        return msg

    @staticmethod
    def format_hourly_gainers(gainers: list, macro: dict) -> str:
        """?쒓컙蹂??곸듅 醫낅ぉ 由ы룷???щ㎎??""
        msg = f"?뙇 **?쒖옣 ?곹솴 ?붿빟**\n"
        if macro:
            regime = macro.get('market_regime', {})
            msg += f"??**?곹깭**: {regime.get('status')} ({regime.get('diff_pct', 0):+.1f}% above MA200)\n"
            msg += f"??**湲덈━**: {macro.get('us_10y_yield')}%\n"
            msg += f"??**VIX**: {macro.get('vix')}\n"
            
            btc = macro.get('crypto', {}).get('BTC')
            if btc:
                msg += f"??**BTC**: ${btc['price']:,.0f} ({btc['change']:+.2f}%)\n"
            
            commodities = macro.get('commodities', {})
            gold = commodities.get('Gold')
            oil = commodities.get('Oil')
            if gold and oil:
                msg += f"??**Gold**: ${gold['price']:,.1f} ({gold['change']:+.2f}%) | **Oil**: ${oil['price']:,.2f} ({oil['change']:+.2f}%)\n"
        
        msg += "\n?뙔 **?꾨텋 ?ㅽ????곸듅 由ы룷??(?꾩껜)**\n"
        for g in gainers: 
            state_icon = "?뙌" if g['market'] == "Pre-market" else "??"
            msg += f"{state_icon} **{g['name']} ({g['ticker']})**: +{g['change']:.2f}% (${g['price']:.2f})\n"
            
        return msg
