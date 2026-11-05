# AI 群聊室（ai_meetint_room）

仿微信群聊界面的本地工具：把 **豆包 / DeepSeek / 千问 / 智谱清言 / 元宝** 五个 AI 助手拉进同一个"群"，你 @ 谁，谁就回答；不 @ 或 @所有人则全员回复。支持私聊、按助手筛选群聊记录、引用转发、Markdown 渲染、记录导出。

**无需任何付费 API**——通过 Playwright 驱动你本机已登录的真实浏览器（Edge/Chrome）与各网页版助手对话，并以内置的拟人化机制（随机延迟、逐字输入、每日限额、安静时段、熔断自保）最大限度降低账号风控风险。

## 功能特性

- **群聊 / 私聊 / 助手视角**：@ 路由到指定助手；私聊独立会话；"群聊记录"视图筛选出某助手收到的问题与它的全部回复
- **引用转发**：引用 A 的回答发给 @B，让 B 看到前者的答案（截断 500 字，模拟人复制粘贴）
- **取消 / 重发**：排队中、输入中均可取消（发送前拦截并清空草稿，零平台流量）；失败一键重发
- **Markdown 渲染**（marked + DOMPurify 消毒，本地 vendor，离线可用）；`breaks: true` 保留平台原始换行，元宝这类"单换行分段"的回答不再糊成一段，列表符号自动合并
- **长文本折叠（仅手机端）**：超过 260 字的消息默认限高 300px + 底部渐隐，「展开全文 / 收起」手动切换；桌面端始终完整显示
- **助手启停**：`config.yaml` 里给成员加 `enabled: false` 即可停用——不启动该平台浏览器、不进成员/状态列表、@ 它会明确报错；改回 `true` 重启即恢复
- **导出**：当前视图（群聊 / 私聊 / 助手视角）一键导出 Markdown
- **防风控中心**：侧栏实时显示各平台排队数、今日用量、熔断状态；本地全量保存聊天历史，查看历史零网络请求
- **设置页**：各平台间隔 / 日限额、安静时段可视化调整，保存即时生效
- **移动端适配**：响应式布局 + 抽屉侧栏，手机可通过 Tailscale 访问
- **服务端日志**：`data/logs/app.log` 滚动记录发送 / 取消 / 熔断 / 失败

## 环境要求

- Windows 10 / 11
- Python 3.10+
- 本机安装 **Microsoft Edge** 或 **Google Chrome**（驱动真实浏览器，不使用 headless）
- 各 AI 平台的可用账号（建议豆包 / 元宝使用独立小号）

## 安装步骤

```powershell
# 1. 克隆仓库
git clone https://github.com/payphonle/ai_meetint_room.git
cd ai_meetint_room

# 2. （可选但推荐）创建虚拟环境
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3. 安装依赖
pip install -r requirements.txt

# 4. 启动
.\start-local.bat        # 优先用 .venv 里的 Python（推荐）
# 或：.\start.bat         # 直接用系统 Python
# 或：python -m app.main
# 或：python -m uvicorn app.main:app --host 127.0.0.1 --port 8787
```

浏览器打开 http://127.0.0.1:8787 即可使用（`server.host` 为 `0.0.0.0` 时局域网内可访问）。

> 说明：本项目使用系统真实浏览器（`channel="chrome"` / `"msedge"` 依次回退），
> **无需**执行 `playwright install`。若两者都没装，再执行 `playwright install chromium`。

## 首次使用

1. `config.yaml` 中 `scheduler.echo_mode` 默认为 `true`（回声假助手，不联网、零风控风险，用于熟悉界面）；
   改为 `false` 后，`real_platforms` 中的平台走浏览器自动化
2. 群里发 `@DeepSeek 你好` → 自动弹出真实 Edge 窗口打开对应平台 → **首次需在该窗口手动登录**
   （5 分钟内完成，登录态持久化到 `data/profiles/<平台>/`，之后免登录）
3. 建议按 风控强度从低到高 逐家实测：DeepSeek / 智谱清言 → 千问 → 豆包 / 元宝（严格档：间隔 90~300 秒、日限 20 条）
   （千问在 `config.yaml` 里默认 `enabled: false` 停用，要用就把该行改成 `true` 再重启）

## 手机访问（推荐 Tailscale）

- 电脑与手机都登录 Tailscale 后，电脑执行 `tailscale serve --bg 8787`，手机访问
  `https://<机器名>.<tailnet>.ts.net`——加密、不暴露公网、不限同一 Wi-Fi
- 不修改 Windows 防火墙；`server.host` 保持 `0.0.0.0` 仅供可信网络使用

## 防风控设计红线（开发与使用共同遵守）

1. 只驱动真实 Chrome/Edge 有头窗口，禁止 headless、禁止伪造指纹
2. 只读 DOM 获取回复，禁止抓包 / 拦截网络帧
3. 禁止逆向 HTTP 接口、自动登录、自动过验证码；验证码 → 人工处理
4. 每平台串行发送、随机间隔（45~300 秒）、每日限额（20~30 条）、01:00-07:30 安静时段
5. 标签页打开后常驻整个运行期，退出时统一关闭；不做定时保活、不做空闲轮询
6. 查看历史消息一律读本地 SQLite，零网络请求
7. 每个助手在网页端只维持一个长驻会话；"新话题"仅手动触发
8. 检测到登录页 / 滑块 / 频繁提示 → 立即熔断该渠道并告警，零自动重试
9. 不挂代理，使用日常机器与家庭宽带 IP
10. 强风控平台（豆包 / 元宝）建议使用独立小号

## 目录结构

```
ai_meetint_room/
  app/
    main.py            FastAPI 服务（REST + WS + 静态托管 + 设置/导出/取消/重发/诊断接口）
    router.py          @ 提及解析、投递文本清洗
    scheduler.py       每助手串行队列：拟人延迟 / 日限额 / 安静时段 / 熔断 / 取消
    storage.py         SQLite 存储层（消息、投递目标、引用关系）
    browser.py         Playwright 浏览器管理（持久化 profile、拟人点击/输入）
    adapters/
      base.py          适配器接口
      common.py        共用框架：就绪检测、拟人发送、回复轮询、取消透传
      deepseek.py / chatglm.py / qianwen.py / doubao.py / yuanbao.py
      echo.py          回声假助手（echo_mode=true 时使用）
  web/                 前端（原生 HTML/CSS/JS，无构建步骤；vendor 内置 marked/DOMPurify；logos 各平台图标）
  scripts/             运维小工具（平台状态、探针、端到端诊断、logo 更新、卡死消息清理）
  config.yaml          全部可调参数（平台节奏 / 限额 / 安静时段 / 绑定地址 / 助手启停）
  data/                运行时数据（gitignore）：chat.db、profiles/ 登录态、logs/、debug/
  start.bat            Windows 一键启动（系统 Python）
  start-local.bat      Windows 一键启动（优先 .venv，推荐）
```

## 常见问题

- **某平台熔断（侧栏标红）**：多为登录过期、页面改版、风控提示。人工在浏览器窗口处理后，
  侧栏点击该助手即可恢复；若反复出现，用诊断接口排查：
  `GET /api/debug/dom/<平台>?probe=1`（输出页面元素 / 截图 / 轮询数据 / 回复块探针）
- **平台明明回答了，聊天室却显示「等待回复超时」**：豆包等平台的消息列表是虚拟列表，新回复挂载时旧节点会被卸载，块数量不增长。适配器现在用「发送前文本快照 + 轮询期间每 3 秒滚到底部」判定新回复，不再只看块数量；若仍复现，用 `GET /api/debug/dom/<平台>?probe=1` 查看 `wait_debug` 里的 `counts` 与 `last_len`
- **选择器过时**：平台改版后更新 `app/adapters/<平台>.py` 顶部的选择器常量即可，适配器会熔断自保而非盲目重试
- **日志与排查**：`data/logs/app.log`；`scripts/check_platforms.py` 查看各平台状态；
  `scripts/probe_blocks.py 豆包` 检查回复块提取内容
- **设置保存后 config.yaml 注释丢失**：safe_dump 重写的固有行为，参数以设置页为准

## 免责声明

本项目仅供个人学习与效率工具用途。各平台服务条款对自动化访问的容忍度不同，尽管已做大量拟人化防护，
仍无法保证账号 absolutely 不受影响——请自行评估、使用小号、保持低频。请勿用于任何商业或批量场景。
