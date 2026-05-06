import sys
import subprocess
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "playwright"])
    subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
    from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.goto('file:///Users/sangjunpark/workspace/000_develop/001_quant/static/index.html')
    # hide overlay and trigger tab switch
    page.evaluate('document.getElementById("login-overlay").style.display = "none"')
    page.evaluate("switchTab(document.querySelector('[data-tab=\"market\"]'), 'market')")
    page.wait_for_timeout(500)
    
    # get the rects
    rects = page.evaluate("""() => {
        const els = document.querySelectorAll('.page-section');
        const res = {};
        els.forEach(el => {
            res[el.id] = el.getBoundingClientRect();
        });
        const market = document.getElementById('market');
        res['market_children'] = Array.from(market.children).map(c => ({tag: c.tagName, rect: c.getBoundingClientRect()}));
        return res;
    }""")
    print("Dashboard:", rects.get("dashboard"))
    print("Market:", rects.get("market"))
    for c in rects.get("market_children", []):
        print(c)
    browser.close()
