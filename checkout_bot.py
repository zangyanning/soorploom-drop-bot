"""
Soor Ploom (soorploomclothier.com) 上新抢购 bot

连接你已经登录好的 Chrome (CDP)，到点后:
  1. 清空购物车
  2. 主路径: 打开 wishlist 页 -> 读取 Checkout 链接 (/cart/<variant_id>:1,...) -> 进入结账页
  3. 主路径失败 (wishlist 为空 / 商品不全 / 被重定向回首页): 用下面 ITEMS 逐件 /cart/add.js 重试加购，
     然后打开 /checkout
  4. 等结账页金额稳定，点 Pay now (#checkout-pay-button)，等待订单确认页

使用前请先阅读 README.md，并修改下面 "配置" 部分。

用法:
  python3 checkout_bot.py                  # 等到 DROP_TIME，抢完自动付款
  python3 checkout_bot.py --dry-run        # 同上，但停在结账页不点 Pay now
  python3 checkout_bot.py --dry-run --now  # 不等时间，立即跑 ITEMS，不点 Pay now
  python3 checkout_bot.py --test           # 立即用 TEST_ITEMS 跑一遍，不点 Pay now
"""
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

# ============================== 配置 ==============================

BASE = "https://soorploomclothier.com"
WISHLIST_URL = f"{BASE}/pages/wishlist"

# 启动 Chrome 时 --remote-debugging-port 用的端口
CDP = "http://127.0.0.1:9333"

# 开售时间 (看首页公告，比如 "Launching October 29th 10:00EDT" = 加州时间 07:00)
DROP_TIME = datetime(2026, 10, 29, 7, 0, 0, tzinfo=ZoneInfo("America/Los_Angeles"))

WISHLIST_TRIES = 2     # wishlist 主路径尝试次数
ADD_WINDOW_S = 600     # fallback 加购最多重试多久 (秒)
ADD_ALL_WAIT_S = 120   # 超过这个时间还有没加上的，就先结账已加上的

# 要抢的商品。id 是 variant ID，即商品链接里 ?variant= 后面的数字
# (每个花色+尺码对应一个 variant)。name 用来在结账页核对商品，填商品标题。
ITEMS = [
    # {"name": "Hawk Pullover", "variant": "Pigeon / 6", "id": 1234567890123,
    #  "url": f"{BASE}/products/hawk-pullover-aw26?variant=1234567890123"},
]

# 用于 --test: 填一件现在就能买的商品，验证流程能走到结账页
TEST_ITEMS = [
    # {"name": "Parlor Dress", "variant": "Trellis Print, Powder / 6", "id": 9876543210987,
    #  "url": f"{BASE}/products/parlor-dress-aw26?variant=9876543210987"},
]

# ==================================================================

ADD_JS = """async (vid) => {
    const r = await fetch('/cart/add.js', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({items: [{id: vid, quantity: 1}]}),
    });
    return r.status;
}"""


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {msg}", flush=True)


def wait_for_drop():
    while (left := (DROP_TIME - datetime.now(DROP_TIME.tzinfo)).total_seconds()) > 0:
        print(f"\r还有 {left:.0f} 秒", end="", flush=True)
        time.sleep(min(left, 1 if left < 30 else 5))
    print()
    log("开抢!")


def open_checkout(page, url):
    """打开 url，到达带 Pay now 按钮的结账页返回 True。
    还没开售时 Shopify 会把 /cart/... 重定向回首页，这种情况立即返回 False。"""
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    deadline = time.time() + 25
    while time.time() < deadline:
        if page.locator("#checkout-pay-button").count():
            return True
        if page.url.rstrip("/") == BASE:
            log("被重定向回首页 (还没开售?)")
            return False
        time.sleep(0.3)
    log(f"没到结账页，当前: {page.url[:100]}")
    return False


def via_wishlist(page, items):
    for i in range(WISHLIST_TRIES):
        page.goto(WISHLIST_URL, wait_until="domcontentloaded", timeout=60000)
        # 按钮先渲染出来 href 为空，约 1 秒后 Swym 才填上 /cart/<vid>:1,...
        try:
            link = page.wait_for_selector("#swymcs-wishlist-checkout[href^='/cart/']", timeout=20000)
        except Exception:
            log(f"wishlist 第 {i+1} 次: 没等到 Checkout 链接")
            continue
        href = link.get_attribute("href") or ""
        missing = [it["name"] for it in items if str(it["id"]) not in href]
        if missing:
            log(f"[WARN] wishlist 里缺少: {missing}，转 fallback")
            return False
        log(f"wishlist 第 {i+1} 次: 点 Checkout")
        if open_checkout(page, BASE + href if href.startswith("/") else href):
            return True
    return False


def via_items(page, items):
    pending, start = list(items), time.time()
    while pending and time.time() - start < ADD_WINDOW_S:
        for it in list(pending):
            status = page.evaluate(ADD_JS, it["id"])
            if status == 200:
                log(f"加购成功: {it['name']} {it['variant']}")
                pending.remove(it)
            elif status == 429:
                # Shopify 限流 / "Verifying your connection": 刷新页面过验证，再放慢
                log("429 被限流，刷新页面等 3 秒")
                page.goto(BASE, wait_until="domcontentloaded", timeout=60000)
                time.sleep(3)
                break
        if pending and len(pending) < len(items) and time.time() - start > ADD_ALL_WAIT_S:
            break
        time.sleep(1)
    if pending:
        log(f"[WARN] 没抢到: {[it['name'] for it in pending]}")
    if len(pending) == len(items):
        return False
    return open_checkout(page, f"{BASE}/checkout")


def pay(page, items, dry_run):
    # 运费是异步算的，等按钮金额连续 3 秒不变再读/点
    btn = page.locator("#checkout-pay-button")
    last, stable_since = None, time.time()
    while time.time() - stable_since < 3:
        cur = btn.inner_text().replace("\n", " ")
        if cur != last:
            last, stable_since = cur, time.time()
        time.sleep(0.3)
    text = page.inner_text("body")
    present = [it["name"] for it in items if it["name"] in text]
    log(f"结账页商品: {present}")
    log(f"按钮: {last}")
    if dry_run:
        log("dry-run: 不点 Pay now，停在结账页")
        return
    page.click("#checkout-pay-button")
    log("已点 Pay now，等待确认...")
    try:
        page.wait_for_url(lambda u: "thank" in u or "/orders/" in u, timeout=90000)
        log(f"下单成功! {page.url[:120]}")
    except Exception:
        log(f"[WARN] 90 秒内没到确认页 (可能要验证码或 3DS)，去 Chrome 里看一下。当前: {page.url[:120]}")


def main():
    test = "--test" in sys.argv
    dry_run = test or "--dry-run" in sys.argv
    items = TEST_ITEMS if test else ITEMS
    if not items:
        raise SystemExit("ITEMS / TEST_ITEMS 是空的，先在文件顶部配置商品")

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        for p in ctx.pages:
            if "shop.app" in p.url or "/checkouts/" in p.url:
                p.close()
        page = next((p for p in ctx.pages if BASE in p.url), None) or ctx.new_page()
        if BASE not in page.url:
            page.goto(BASE, wait_until="domcontentloaded")
        page.bring_to_front()

        if not test and "--now" not in sys.argv:
            wait_for_drop()

        page.evaluate("async () => { await fetch('/cart/clear.js', {method: 'POST'}) }")
        ok = False if test else via_wishlist(page, items)
        if not ok:
            log("wishlist 结账失败，fallback 到 ITEMS 逐件加购")
            ok = via_items(page, items)
        if not ok:
            log("[FAIL] 没能进入结账页")
            sys.exit(1)
        pay(page, items, dry_run)


if __name__ == "__main__":
    main()
