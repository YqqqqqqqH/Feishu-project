"""M3 浏览器自动化模块 — 淘宝搜索、商品抓取、加购。"""

import asyncio
import json
import os
import random
import re
import time
from pathlib import Path
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

# stealth 实例（全局复用）
_stealth = Stealth()

# ── 路径配置 ──────────────────────────────────────────────
# user-data-dir：复用本地 Chrome 的完整用户数据（cookie、登录态、指纹）
# 比 storage_state 更彻底，淘宝风控更难识别
USER_DATA_DIR = Path(__file__).parent.parent / ".chrome_profile"

# 旧版 session 文件（仅作为 cookie 注入的备选来源）
SESSION_DIR = Path(__file__).parent.parent / ".session"
SESSION_FILE = SESSION_DIR / "taobao_state.json"

# 本地正式版 Chrome / Chromium 路径（避免 Playwright 自带的测试版浏览器）
_CHROME_CANDIDATES = [
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
]


def _find_chrome():
    """查找本地安装的正式版 Chrome。"""
    for path in _CHROME_CANDIDATES:
        if os.path.isfile(path):
            return path
    return None


async def _human_delay(lo=0.3, hi=1.5):
    """随机延迟，模拟人类操作节奏。"""
    await asyncio.sleep(random.uniform(lo, hi))


async def launch_browser():
    """启动浏览器（反风控增强版）。

    策略：
    1. stealth 插件 — 隐藏 Playwright 自动化特征
    2. 本地正式版 Chrome — 避免被识别为测试浏览器
    3. user-data-dir — 复用完整用户数据（cookie、登录态）
    4. cookie 注入 — 若 user-data-dir 无登录态，从旧 session 文件注入
    """
    pw = await async_playwright().start()

    chrome_path = _find_chrome()
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)

    # 使用 launch_persistent_context：绑定 user-data-dir，复用登录态
    # 指定本地正式版 Chrome，避免 Playwright 自带的 Chromium 测试版
    launch_args = {
        "headless": False,
        "user_data_dir": str(USER_DATA_DIR),
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
        ],
    }
    if chrome_path:
        launch_args["executable_path"] = chrome_path
        print(f"[浏览器] 使用本地 Chrome: {chrome_path}")
    else:
        print("[浏览器] 未找到本地 Chrome，使用 Playwright 内置浏览器")

    context = await pw.chromium.launch_persistent_context(**launch_args)

    # 注入 stealth 脚本（隐藏 navigator.webdriver 等自动化特征）
    page = context.pages[0] if context.pages else await context.new_page()
    await _stealth.apply_stealth_async(page)
    print("[浏览器] stealth 插件已注入")

    # cookie 注入：如果旧 session 文件存在，补充注入 cookie
    if SESSION_FILE.exists():
        try:
            state = json.loads(SESSION_FILE.read_text())
            cookies = state.get("cookies", [])
            if cookies:
                await context.add_cookies(cookies)
                print(f"[浏览器] 已注入 {len(cookies)} 条 cookie")
        except Exception as e:
            print(f"[浏览器] cookie 注入失败: {e}")

    # launch_persistent_context 没有独立的 browser 对象
    # 返回 None 作为 browser 占位，关闭时用 context.close()
    return pw, None, context, page


async def check_login(page):
    """检查是否已登录淘宝。"""
    await page.goto("https://www.taobao.com/", wait_until="domcontentloaded")
    await _human_delay(2, 3)

    login_link = await page.query_selector('a[href*="login.taobao.com"]')
    if login_link:
        text = await login_link.inner_text()
        if "登录" in text:
            return False
    return True


async def wait_for_manual_login(page, timeout=120):
    """等待用户手动完成登录（扫码/账密），最多等待 timeout 秒。"""
    print(f"[登录] 请在 {timeout} 秒内完成淘宝登录...")
    await page.goto("https://login.taobao.com/", wait_until="domcontentloaded")

    # 扫码后可能跳转到多种页面：www.taobao.com、i.taobao.com、login中间页等
    # 改用轮询检测：只要 URL 离开了 login.taobao.com 就算登录成功
    start = time.time()
    while time.time() - start < timeout:
        url = page.url
        if "login.taobao.com" not in url:
            print(f"[登录] 登录成功，跳转到: {url}")
            return True
        await page.wait_for_timeout(1000)

    print("[登录] 登录超时")
    return False


async def save_session(context):
    """保存当前 cookie 到文件（供后续 cookie 注入使用）。

    注意：launch_persistent_context 会自动持久化 user-data-dir，
    这里额外保存 cookie 作为备份和跨 profile 迁移用。
    """
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    cookies = await context.cookies()
    state = {"cookies": cookies}
    SESSION_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    print(f"[Session] 已保存 {len(cookies)} 条 cookie")


async def search_products(page, keyword):
    """在淘宝搜索关键词，等待商品列表加载。"""
    print(f"[搜索] 关键词: {keyword}")

    print(f"[搜索] 导航到淘宝首页...")
    await page.goto("https://www.taobao.com/", wait_until="domcontentloaded")
    await _human_delay(2, 4)

    # 定位搜索框
    print(f"[搜索] 定位搜索框...")
    search_input = None
    for sel in ['input#q', 'input[name="q"]', 'input[placeholder*="搜索"]']:
        search_input = await page.query_selector(sel)
        if search_input:
            print(f"[搜索] 找到搜索框: {sel}")
            break

    if not search_input:
        search_input = await page.wait_for_selector('input[type="text"], input[type="search"]', timeout=10000)

    # 模拟人类输入：点击 → 短暂停顿 → 逐字输入
    await search_input.click()
    await _human_delay(0.3, 0.8)
    await search_input.evaluate("el => el.value = ''")
    await page.keyboard.type(keyword, delay=random.randint(80, 200))
    await _human_delay(0.5, 1.0)

    # 按回车触发搜索
    print(f"[搜索] 发送回车键...")
    try:
        await asyncio.gather(
            search_input.press('Enter'),
            page.wait_for_load_state('networkidle', timeout=25000),
        )
        print(f"[搜索] 导航完成")
    except Exception as e:
        print(f"[搜索] 导航异常: {e}")

    await _human_delay(2, 4)

    current_url = page.url
    print(f"[诊断] 当前 URL: {current_url}")

    # 等待网络空闲
    try:
        await page.wait_for_load_state('networkidle', timeout=20000)
    except:
        pass

    await _human_delay(1, 2)

    if 'error' in current_url:
        print(f"[错误] 淘宝返回错误页面，可能被反爬虫拦截")
    else:
        print(f"[搜索] ✓ 搜索完成")


async def scrape_product_list(page):
    """从搜索结果页抓取商品基本信息。"""
    items = []

    # 模拟人类滚动浏览：随机滚动距离 + 随机间隔
    for i in range(5):
        scroll_dist = random.randint(400, 800)
        await page.evaluate(f"window.scrollBy(0, {scroll_dist})")
        await _human_delay(0.8, 2.0)
        print(f"[抓取] 滚动 {i+1}/5")

    # 查找商品链接
    print(f"[抓取] 查找商品链接...")
    links = await page.query_selector_all('a[href*="item.taobao.com"], a[href*="detail.tmall.com"]')
    print(f"[抓取] 找到 {len(links)} 个商品链接")

    if not links:
        divs = await page.query_selector_all('div[class*="card"], div[class*="item"], div[class*="product"]')
        print(f"[诊断] 找到 {len(divs)} 个可能的卡片容器")
        return items

    for link in links[:10]:
        try:
            href = await link.get_attribute("href")
            if not href:
                continue

            title = await link.inner_text()
            title = title.strip()
            if not title or len(title) < 2:
                continue

            if href.startswith('//'):
                href = 'https:' + href
            elif href.startswith('/'):
                href = 'https://taobao.com' + href

            # 尝试从父容器提取价格
            price = '0'
            try:
                parent = await link.evaluate_handle(
                    "el => el.closest('[class*=\"card\"], [class*=\"item\"], [class*=\"product\"]')"
                )
                if parent:
                    price_el = await parent.evaluate(
                        "el => { const p = el.querySelector('[class*=\"price\"], [class*=\"Price\"]'); return p ? p.textContent : null; }"
                    )
                    if price_el:
                        price = price_el.replace('¥', '').strip()
            except:
                pass

            items.append({
                "title": title,
                "price": price,
                "sales": "",
                "shop": "",
                "url": href,
                "rating": None,
            })
            print(f"[抓取] ✓ {title[:30]}... ¥{price}")
        except Exception as e:
            print(f"[抓取] 单个商品解析失败: {e}")
            continue

    print(f"[抓取] 成功抓取 {len(items)} 个商品")
    return items


async def fetch_rating(page, product_url):
    """进入商品详情页获取好评率。"""
    try:
        detail_page = await page.context.new_page()
        await _stealth.apply_stealth_async(detail_page)
        await detail_page.goto(product_url, wait_until="domcontentloaded", timeout=15000)
        await _human_delay(2, 3)

        # 尝试多种选择器定位好评率
        rating_selectors = [
            '[class*="ratePercent"]',
            '[class*="goodRate"]',
            '[class*="rate-percent"]',
            '.tb-rate-counter .rate-percent',
            '.tm-rate .percent',
        ]
        for sel in rating_selectors:
            el = await detail_page.query_selector(sel)
            if el:
                text = await el.inner_text()
                match = re.search(r'(\d+\.?\d*)', text)
                if match:
                    await detail_page.close()
                    return float(match.group(1))

        # 备选：从页面全文正则匹配好评率
        body_text = await detail_page.inner_text('body')
        match = re.search(r'好评[率度]\s*[：:]\s*(\d+\.?\d*)%?', body_text)
        if match:
            await detail_page.close()
            return float(match.group(1))

        await detail_page.close()
        return None
    except Exception as e:
        print(f"[好评率] 获取失败: {e}")
        return None


async def rank_and_filter(page, items, threshold=99, max_results=5):
    """获取好评率并按好评率降序排序。

    策略：不硬卡阈值，按好评率排序返回 top N。
    优先返回 >= threshold 的商品，不足则用好评率最高的补齐。
    """
    print(f"[筛选] 开始获取好评率，共 {len(items)} 个商品...")

    # 限制详情页访问数量，避免触发反爬（最多查 10 个）
    candidates = items[:10]

    for item in candidates:
        if item["url"]:
            rating = await fetch_rating(page, item["url"])
            item["rating"] = rating
            label = f"{rating}%" if rating else "未获取"
            print(f"  {item['title'][:20]}... -> 好评率: {label}")

    # 分两组：有好评率的和没有的
    rated = [i for i in candidates if i["rating"] is not None]
    unrated = [i for i in candidates if i["rating"] is None]

    # 按好评率降序排序
    rated.sort(key=lambda x: x["rating"], reverse=True)

    # 优先取 >= threshold 的，不足则补齐
    above = [i for i in rated if i["rating"] >= threshold]
    below = [i for i in rated if i["rating"] < threshold]

    result = above + below + unrated
    result = result[:max_results]

    above_count = len(above)
    print(f"[筛选] 好评率 >= {threshold}%: {above_count} 个，共返回 {len(result)} 个")
    return result


async def add_to_cart(page, product_url):
    """进入商品详情页，点击加入购物车。"""
    try:
        await page.goto(product_url, wait_until="domcontentloaded", timeout=15000)
        await _human_delay(2, 3)

        cart_selectors = [
            '#J_LinkBasket',
            'button:has-text("加入购物车")',
            'a:has-text("加入购物车")',
            '[class*="addCart"]',
            '[class*="AddCart"]',
            '[data-spm*="addcart"]',
            'button:has-text("加购")',
        ]
        for sel in cart_selectors:
            btn = await page.query_selector(sel)
            if btn:
                await _human_delay(0.3, 0.8)
                await btn.click()
                await _human_delay(1.5, 2.5)
                print(f"[加购] 成功")
                return True

        print("[加购] 未找到加购按钮")
        return False
    except Exception as e:
        print(f"[加购] 失败: {e}")
        return False


async def run_task(keyword, rating_threshold=99, max_results=5):
    """M3 主流程：登录 → 搜索 → 抓取 → 筛选 → 加购。

    返回结构化结果供 M4 生成报告。
    """
    pw, browser, context, page = await launch_browser()

    try:
        # 1. 登录
        logged_in = await check_login(page)
        if not logged_in:
            success = await wait_for_manual_login(page)
            if not success:
                return {"status": "failed", "message": "登录超时"}
            await save_session(context)

        # 2. 搜索
        await search_products(page, keyword)

        # 3. 抓取商品列表
        items = await scrape_product_list(page)
        if not items:
            return {"status": "failed", "message": "未找到商品"}

        # 4. 好评率筛选排序
        ranked = await rank_and_filter(page, items, rating_threshold, max_results)

        # 5. 加购（对筛选结果中的商品执行）
        cart_results = []
        for item in ranked:
            if item["url"]:
                added = await add_to_cart(page, item["url"])
                item["added_to_cart"] = added
                cart_results.append(item)

        # 保存 session 供下次复用
        await save_session(context)

        return {
            "status": "success",
            "keyword": keyword,
            "threshold": rating_threshold,
            "total_scraped": len(items),
            "items": cart_results,
        }
    except Exception as e:
        print(f"[任务异常] {e}")
        return {"status": "failed", "message": str(e)}
    finally:
        # launch_persistent_context 没有独立 browser 对象，直接关闭 context
        await context.close()
        await pw.stop()
