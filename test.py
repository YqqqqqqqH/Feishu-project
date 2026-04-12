import asyncio
import os
import threading
from flask import Flask, request, jsonify

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from feishu import parse_feishu_message, extract_task, send_feishu_message
from browser import run_task

app = Flask(__name__)

# 已处理的 message_id 集合，用于去重（飞书可能重复推送）
processed_messages = set()


@app.route('/callback', methods=['POST'])
def callback():
    """飞书事件回调路由 — 处理消息、解析任务、启动后台爬虫。"""
    data = request.json

    # 飞书回调地址验证
    if data.get("type") == "url_verification":
        return jsonify({"challenge": data.get("challenge")})

    # 解析消息
    msg = parse_feishu_message(data)
    if not msg:
        return jsonify({})

    # 去重
    if msg["message_id"] in processed_messages:
        return jsonify({})
    processed_messages.add(msg["message_id"])

    print(f"[收到消息] sender={msg['sender_id']}, text={msg['text']}")

    # 提取任务
    task = extract_task(msg["text"])
    if task:
        print(f"[解析任务] keyword={task['keyword']}, threshold={task['rating_threshold']}%")
        # 回复用户：已收到任务
        send_feishu_message(
            msg["chat_id"],
            f"收到！正在为你搜索「{task['keyword']}」，好评率 ≥ {task['rating_threshold']}%，请稍候..."
        )
        # 在后台线程中启动浏览器自动化，避免阻塞飞书回调
        threading.Thread(
            target=_run_browser_task,
            args=(task["keyword"], task["rating_threshold"], msg["chat_id"]),
            daemon=True,
        ).start()
    else:
        print(f"[忽略消息] 未识别为购物任务: {msg['text']}")

    return jsonify({})


def _run_browser_task(keyword, threshold, chat_id):
    """后台执行浏览器自动化任务，完成后回传飞书。"""
    result = asyncio.run(run_task(keyword, rating_threshold=threshold))

    if result["status"] == "success":
        lines = [f"搜索「{keyword}」完成，共抓取 {result['total_scraped']} 个商品，筛选结果：\n"]
        for i, item in enumerate(result["items"], 1):
            rating_str = f"{item['rating']}%" if item["rating"] else "未获取"
            cart_str = "已加购" if item.get("added_to_cart") else "加购失败"
            lines.append(f"{i}. {item['title']}\n   价格: ¥{item['price']} | 好评率: {rating_str} | {cart_str}")
        send_feishu_message(chat_id, "\n".join(lines))
    else:
        send_feishu_message(chat_id, f"任务失败：{result.get('message', '未知错误')}")


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)