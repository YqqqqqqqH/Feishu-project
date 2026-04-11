import json
import re
from flask import Flask, request, jsonify

app = Flask(__name__)

# 触发关键词
TRIGGER_KEYWORDS = ["买", "想买", "推荐", "搜索", "找", "帮我找", "看看"]

# 已处理的 message_id 集合，用于去重（飞书可能重复推送）
processed_messages = set()


def parse_feishu_message(data):
    """从飞书事件回调中提取用户文本内容。

    飞书事件结构：
    {
      "header": {"event_type": "im.message.receive_v1", ...},
      "event": {
        "message": {
          "message_id": "...",
          "message_type": "text",
          "content": "{\"text\":\"想买索尼耳机\"}"
        },
        "sender": {"sender_id": {...}, ...}
      }
    }
    """
    try:
        event = data.get("event", {})
        message = event.get("message", {})
        message_id = message.get("message_id", "")
        message_type = message.get("message_type", "")
        sender = event.get("sender", {}).get("sender_id", {}).get("open_id", "unknown")

        if message_type != "text":
            return None

        content_str = message.get("content", "{}")
        content = json.loads(content_str)
        text = content.get("text", "").strip()

        return {
            "message_id": message_id,
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
    keyword = text
    for kw in TRIGGER_KEYWORDS:
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
        # TODO: 调用 M2 调度层，启动浏览器自动化流程
    else:
        print(f"[忽略消息] 未识别为购物任务: {msg['text']}")

    return jsonify({})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)