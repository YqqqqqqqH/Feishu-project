# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目目标

为 openclaw 框架设计并实现一个 UI 自动化测试 Skill，完整流程：
1. 飞书接收测试任务指令
2. 浏览器自动化访问淘宝（https://www.taobao.com/）
3. 完成淘宝账号登录
4. 搜索关键词（由用户消息动态指定，不局限于"索尼耳机"）
5. 筛选好评率 ≥ 阈值的商品（默认 99%，用户可指定）
6. 将符合条件的商品加入购物车
7. 将测试结果回传飞书

## 开发命令

```bash
# 启动飞书回调服务（端口 5000）
python test.py

# 单独测试浏览器自动化（不需要飞书，直接运行搜索流程）
python test_search.py

# 打通公网隧道（开发阶段，供飞书回调访问）
cloudflared tunnel --url http://localhost:5000
```

必须设置的环境变量：
```bash
export FEISHU_APP_ID=your_app_id
export FEISHU_APP_SECRET=your_app_secret
```

## 架构与数据流

```
飞书用户消息
    │
    ▼
POST /callback  (test.py)
    │  parse_feishu_message()  — 解析事件结构，提取 text + chat_id
    │  extract_task()          — 关键词触发 + 正则提取关键词/阈值
    │
    ▼
threading.Thread(_run_browser_task)  — 后台线程，不阻塞回调
    │
    ▼
asyncio.run(run_task())  (src/browser.py)
    │  launch_browser()     — stealth + 本地 Chrome + user-data-dir
    │  check_login()        — 检测登录态
    │  search_products()    — 首页搜索框交互 + 等待结果页
    │  scrape_product_list()— 抓取链接/标题/价格（最多 10 个）
    │  rank_and_filter()    — 进详情页获取好评率，降序排序
    │  add_to_cart()        — 对筛选结果执行加购
    │
    ▼
send_feishu_message()  (src/feishu.py)  — 结果回传
```

### 关键模块

- `test.py` — Flask 回调入口（M1+M2）。消息去重用内存 set，重启后清空。
- `src/feishu.py` — 飞书 Token 管理（2小时自动刷新）、消息解析、消息发送。
- `src/browser.py` — 全部浏览器自动化逻辑（M3）。
- `test_search.py` — 直连 `run_task()` 的本地测试脚本，不需要飞书环境。

### 会话持久化

- `.chrome_profile/` — Playwright `launch_persistent_context` 的 user-data-dir，复用完整 Chrome 用户数据（主登录态来源）。
- `.session/taobao_state.json` — cookie 备份，作为跨 profile 迁移的备选注入源。

## 反风控策略

`browser.py` 中当前采用的反风控手段（修改时注意不要破坏）：
1. `playwright-stealth` — 隐藏 `navigator.webdriver` 等自动化特征
2. 本地正式版 Chrome（`_find_chrome()`）— 避免被识别为 Playwright 测试浏览器
3. `user-data-dir` — 携带真实指纹和登录态
4. `_human_delay()` — 操作间随机延迟（0.3~2s）
5. 随机键盘输入延迟（80~200ms）
6. 模拟鼠标滚动翻页

## 当前进度

### M1 飞书接入层 — 已完成
- [x] Flask 回调接口 + 飞书 URL 验证
- [x] 飞书事件消息解析（从嵌套结构中提取文本 + chat_id）
- [x] 消息去重（message_id）
- [x] 任务提取：关键词触发 + 正则提取商品名和好评率阈值
- [x] Bot Token 管理（自动获取 + 缓存 + 过期刷新）
- [x] 消息回传（send_feishu_message，文本消息）
- [ ] 卡片消息回传（结构化报告，M4 完成后对接）

### M2 Agent 调度层 — 已完成
- [x] 后台线程调度浏览器任务（避免阻塞飞书回调）
- [x] 任务完成后自动回传结果到飞书

### M3 浏览器自动化层 — 已完成
- [x] 反风控增强（stealth + 本地 Chrome + user-data-dir + 随机延迟 + cookie 注入）
- [x] 淘宝登录检测 + 等待手动登录
- [x] 首页搜索框交互 + 等待商品列表动态渲染
- [x] 商品卡片信息抓取（名称、价格、链接）
- [x] 进入详情页获取好评率（多来源：API 响应 / 内联脚本 / body 文本）
- [x] 按好评率降序排序，优先返回 ≥ 阈值的商品，不足则补齐 top N
- [x] 加入购物车操作
- [ ] 截图取证（后期）

### M4 数据处理层 — 待开发
### M5 Skill 定义层 — 待开发

## 开发指引

- 主交付物是 `DESIGN.md`，需覆盖 handout.md 中 1.1~1.4 所有章节
- `SKILL.md` 按 openclaw Skill 规范编写
- `extract_task()` 采用关键词触发 + 正则混合策略，修改触发词时同步更新 `TRIGGER_KEYWORDS`
- 好评率提取逻辑优先级：API 响应 JSON > 内联脚本文本 > body 文本（`_merge_review_summary`）
- `rank_and_filter()` 不硬卡阈值，按好评率排序返回 top N，阈值决定排序优先级而非过滤
