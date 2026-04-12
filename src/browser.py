"""M3 浏览器自动化模块 — 淘宝搜索、商品抓取、加购。"""

import asyncio
import json
import os
import re
import time
from pathlib import Path
from playwright.async_api import async_playwright

# session 持久化目录
SESSION_DIR = Path(__file__).parent.parent / ".session"
SESSION_FILE = SESSION_DIR / "taobao_state.json"


async def launch_browser():
    """启动浏览器，优先加载已保存的 session。"""
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=False)

    if SESSION_FILE.exists():
        context = await browser.new_context(storage_state=str(SESSION_FILE))
        print("[浏览器] 已加载保存的 session")
    else:
        context = await browser.new_context()
        print("[浏览器] 使用全新 session")

    page = await context.new_page()
    return pw, browser, context, page


async def check_login(page):
    """检查是否已登录淘宝。"""
    await page.goto("https://www.taobao.com/", wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)

    # 检查页面上是否有登录入口（未登录时会显示"请登录"）
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
    """保存当前 session 到文件。"""
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    await context.storage_state(path=str(SESSION_FILE))
    print("[Session] 已保存")


async def search_products(page, keyword):
    """在淘宝搜索关键词，等待商品列表加载。"""
    print(f"[搜索] 关键词: {keyword}")
    await page.goto("https://www.taobao.com/", wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)

    # 定位搜索框并输入（#q 是淘宝搜索框的稳定 id）
    # 备选：用 placeholder 属性定位
    search_input = await page.query_selector('#q')
    if not search_input:
        search_input = await page.query_selector('input[placeholder*="搜索"]')
    if not search_input:
        search_input = await page.wait_for_selector('input[type="text"]', timeout=10000)

    await search_input.click()
    await search_input.fill(keyword)
    await page.wait_for_timeout(500)

    # 按回车触发搜索
    await search_input.press('Enter')

    # 等待搜索结果页加载 — 用属性前缀匹配，不依赖哈希后缀
    # 淘宝搜索结果容器的 class 通常包含 "contentInner" 或 "Content"
    await page.wait_for_selector(
        '[class*="contentInner"], [class*="search-content"], [class*="shoplist"]',
        timeout=20000,
    )
    await page.wait_for_timeout(2000)
    print("[搜索] 商品列表已加载")


async def scrape_product_list(page):
    """从搜索结果页抓取商品基本信息。"""
    items = []

    # 滚动页面触发懒加载
    for _ in range(5):
        await page.evaluate("window.scrollBy(0, 600)")
        await page.wait_for_timeout(800)

    # 用属性前缀匹配商品卡片，不依赖哈希后缀
    # 淘宝卡片 class 通常包含 "doubleCardWrapper" 或 "CardWrapper"
    cards = await page.query_selector_all('[class*="doubleCardWrapper"]')
    if not cards:
        cards = await page.query_selector_all('[class*="CardWrapper"]')
    if not cards:
        cards = await page.query_selector_all('[class*="card--"] a[href*="item"]')
    if not cards:
        # 最后兜底：搜索结果区域内所有包含商品链接的块
        cards = await page.query_selector_all('.search-content .item, [class*="shoplist"] .item')

    print(f"[抓取] 找到 {len(cards)} 个商品卡片")

    for card in cards:
        try:
            item = await _extract_card_info(card)
            if item:
                items.append(item)
        except Exception as e:
            print(f"[抓取] 单个商品解析失败: {e}")
            continue

    return items


async def _extract_card_info(card):
    """从单个商品卡片中提取信息。"""
    # 商品名称 — 用属性前缀匹配，兼容哈希变化
    title_el = await card.query_selector('[class*="title--"], [class*="Title--title"], .title')
    if not title_el:
        title_el = await card.query_selector('a[href*="detail.tmall"], a[href*="item.taobao"]')
    title = await title_el.inner_text() if title_el else ""
    title = title.strip()
    if not title:
        return None

    # 价格
    price_el = await card.query_selector('[class*="priceInt"], [class*="Price--price"], .price')
    price_text = await price_el.inner_text() if price_el else "0"
    price = price_text.strip().replace("¥", "").replace(",", "")

    # 销量
    sales_el = await card.query_selector('[class*="realSales"], [class*="Sales--"], .deal-cnt')
    sales_text = await sales_el.inner_text() if sales_el else ""

    # 店铺名
    shop_el = await card.query_selector('[class*="ShopInfo"], [class*="shopName"], .shop, .shopname')
    shop = await shop_el.inner_text() if shop_el else ""

    # 商品链接
    link_el = await card.query_selector('a[href*="detail"], a[href*="item"]')
    link = await link_el.get_attribute("href") if link_el else ""
    if link and link.startswith("//"):
        link = "https:" + link

    return {
        "title": title,
        "price": price,
        "sales": sales_text.strip(),
        "shop": shop.strip(),
        "url": link,
        "rating": None,
    }


async def fetch_rating(page, product_url):
    """进入商品详情页获取好评率。"""
    try:
        detail_page = await page.context.new_page()
        await detail_page.goto(product_url, wait_until="domcontentloaded", timeout=15000)
        await detail_page.wait_for_timeout(2000)

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
        await page.wait_for_timeout(2000)

        # 尝试多种加购按钮选择器
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
                await btn.click()
                await page.wait_for_timeout(2000)
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
        await browser.close()
        await pw.stop()
