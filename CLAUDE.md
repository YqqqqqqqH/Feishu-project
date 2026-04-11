# CLAUDE.md — 飞书 UI 自动化测试 Skill 项目

## 项目目标

为 openclaw 框架设计并实现一个 UI 自动化测试 Skill，完整流程：
1. 飞书接收测试任务指令
2. 浏览器自动化访问淘宝（https://www.taobao.com/）
3. 完成淘宝账号登录
4. 搜索关键词"索尼耳机"
5. 筛选好评率 ≥ 99% 的商品
6. 将符合条件的商品加入购物车
7. 将测试结果回传飞书

## 交付物

- 技术设计文档（方案设计文档）
  - 架构设计（架构图、模块划分、技术栈）
  - Skill 定义（SKILL.md 结构、相关脚本）
  - 关键技术点分析
  - 核心代码示例（加分项）

## 技术约束

- Skill 必须符合 openclaw 规范
- 飞书作为任务入口和结果出口

## 工作目录结构（规划）

```
feishu_project/
├── CLAUDE.md          # 本文件
├── handout.md         # 原始需求
├── DESIGN.md          # 技术设计文档（主交付物）
├── SKILL.md           # Skill 定义文件
└── src/               # 核心代码示例
    ├── skill.py       # Skill 入口
    ├── browser.py     # 浏览器自动化模块
    └── feishu.py      # 飞书消息收发模块
```

## 开发指引

- 主交付物是 `DESIGN.md`，需覆盖 handout.md 中 1.1~1.4 所有章节
- `SKILL.md` 按 openclaw Skill 规范编写
- 代码示例用 Python + Playwright（或 Selenium），保持最小可运行
- 不要过度设计，聚焦需求文档要求的场景
