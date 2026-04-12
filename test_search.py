"""本地快速测试搜索流程 — 不启动飞书回调，直接测试浏览器自动化。"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))
from browser import run_task


async def main():
    """快速测试搜索和抓取流程。"""
    print("\n========== M3 搜索流程测试 ==========")
    print("测试关键词: 索尼耳机")
    print("好评率阈值: 95%")
    print("预期结果: 成功抓取至少 5 个商品\n")

    result = await run_task("索尼耳机", rating_threshold=95, max_results=5)

    print("\n========== 测试结果 ==========")
    print(f"状态: {result['status']}")
    if result['status'] == 'success':
        print(f"搜索关键词: {result['keyword']}")
        print(f"好评率阈值: {result['threshold']}%")
        print(f"抓取总数: {result['total_scraped']} 个")
        print(f"返回数量: {len(result['items'])} 个")
        print("\n商品列表:")
        for i, item in enumerate(result['items'], 1):
            rating_str = f"{item['rating']}%" if item['rating'] else "未获取"
            cart_str = "✓ 已加购" if item.get('added_to_cart') else "✗ 加购失败"
            print(f"{i}. {item['title'][:30]}")
            print(f"   价格: ¥{item['price']} | 好评率: {rating_str} | {cart_str}")
    else:
        print(f"错误: {result.get('message', '未知错误')}")

    print("\n========== 测试完成 ==========")


if __name__ == '__main__':
    asyncio.run(main())
