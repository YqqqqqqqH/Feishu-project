import json
import os
import re
import time
import asyncio
import threading
import requests
from flask import Flask, request, jsonify

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))
from browser import run_task

app = Flask(__name__)

# 飞书应用凭证 — 创建 Bot 后从飞书开放平台获取
APP_ID = os.environ.get("FEISHU_APP_ID", "")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")

# 触发关键词
TRIGGER_KEYWORDS = ["买", "想买", "推荐", "搜索", "找", "帮我找", "看看"]

# 已处理的 message_id 集合，用于去重（飞书可能重复推送）
processed_messages = set()

# Token 缓存
_token_cache = {"token": "", "expire_time": 0}


def get_tenant_access_token():
    """获取飞书 tenant_access_token，带缓存（有效期 2 小时，提前 5 分钟刷新）。"""
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expire_time"]:
        return _token_cache["token"]

    resp = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": APP_ID, "app_secret": APP_SECRET},
    )
    data = resp.json()
    if data.get("code") != 0:
        print(f"[Token 获取失败] {data}")
        return ""

    token = data["tenant_access_token"]
    _token_cache["token"] = token
    _token_cache["expire_time"] = now + data.get("expire", 7200) - 300
    print("[Token 刷新成功]")
    return token


def send_feishu_message(chat_id, text):
    """向指定会话发送文本消息。"""
    token = get_tenant_access_token()
    if not token:
        print("[发送失败] 无法获取 Token")
        return False

    resp = requests.post(
        "https://open.feishu.cn/open-apis/im/v1/messages",
        params={"receive_id_type": "chat_id"},
        headers={"Authorization": f"Bearer {token}"},
        json={
            "receive_id": chat_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}),
        },
    )
    result = resp.json()
    if result.get("code") != 0:
        print(f"[发送失败] {result}")
        return False
    print(f"[消息已发送] chat_id={chat_id}")
    return True


def parse_feishu_message(data):
    """从飞书事件回调中提取用户文本内容。"""
    try:
        event = data.get("event", {})
        message = event.get("message", {})
        message_id = message.get("message_id", "")
        message_type = message.get("message_type", "")
        chat_id = message.get("chat_id", "")
        sender = event.get("sender", {}).get("sender_id", {}).get("open_id", "unknown")

        if message_type != "text":
            return None

        content_str = message.get("content", "{}")
        content = json.loads(content_str)
        text = content.get("text", "").strip()

        return {
            "message_id": message_id,
            "chat_id": chat_id,
            "sender_id": sender,
            "text": text,
        }
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        print(f"消息解析失败: {e}")
        return None


def extract_task(text):
    """从用户文本中提取搜索意图和关键词。

    策略：关键词触发 + 正则提取商品名。
    示例：
      "想买索尼耳机" -> {"keyword": "索尼耳机", "threshold": 99}
      "帮我找好评高的机械键盘" -> {"keyword": "机械键盘", "threshold": 99}
      "推荐一款95分以上的鼠标" -> {"keyword": "鼠标", "threshold": 95}
    """
    # 检查是否包含触发关键词
    triggered = any(kw in text for kw in TRIGGER_KEYWORDS)
    if not triggered:
        return None

    # 尝试提取自定义好评率阈值，如 "95分以上"、"好评率98%"
    threshold = 99  # 默认
    threshold_match = re.search(r"(\d{2,3})\s*[%分]", text)
    if threshold_match:
        val = int(threshold_match.group(1))
        if 80 <= val <= 100:
            threshold = val

    # 移除触发词和阈值描述，剩余部分作为商品关键词
    # 按长度降序排列，优先匹配长词（"想买"先于"买"），避免残留
    keyword = text
    for kw in sorted(TRIGGER_KEYWORDS, key=len, reverse=True):
        keyword = keyword.replace(kw, "")
    # 移除常见修饰语
    keyword = re.sub(r"(一款|一个|好评[率高]*的?|以上|\d{2,3}[%分]|帮我)", "", keyword)
    keyword = keyword.strip()

    if not keyword:
        return None

    return {
        "keyword": keyword,
        "rating_threshold": threshold,
    }


@app.route('/callback', methods=['POST'])
def callback():
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