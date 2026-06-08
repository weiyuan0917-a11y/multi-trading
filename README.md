<p align="center">
  <img src="frontend/public/brand/multitrading-logo.svg" alt="Multi-Trading" width="360" />
</p>

<h1 align="center">Multi-Trading 量化交易系统</h1>

<p align="center">
  <a href="./LICENSE"><img alt="License: Apache-2.0" src="https://img.shields.io/badge/License-Apache--2.0-blue.svg" /></a>
  <a href="./SECURITY.md"><img alt="Security policy" src="https://img.shields.io/badge/Security-policy-brightgreen.svg" /></a>
  <a href="./DISCLAIMER.md"><img alt="Not financial advice" src="https://img.shields.io/badge/Trading-risk%20warning-orange.svg" /></a>
</p>

<p align="center">
  <strong><a href="https://github.com/weiyuan0917-a11y/multi-trading/releases/tag/V1.0.42">下载 Windows 安装包：MultiTradingSetup-1.0.42.exe</a></strong>
  <br />
  <a href="./CHANGELOG.md">更新日志 / Changelog</a>
  <br />
  当前源码与客户版构建脚本版本：V1.0.42（安装包发布后更新 Release 下载链接）
</p>

<p align="center">
  沟通交流：QQ 178522360 ｜ 邮箱 <a href="mailto:178522360@qq.com">178522360@qq.com</a>
</p>

> **风险提示：本项目仅供技术学习、研究交流和自动化流程演示，不构成任何投资建议、理财建议或交易承诺。软件产生的行情分析、策略评分、交易信号、回测结果和智能体报告均仅供参考，不能作为买卖依据。任何实盘操作均由使用者自行判断、自行承担风险，投资需谨慎，入市有风险。**

基于 `Broker OpenAPI (LongPort-compatible provider)` + `MCP` + `FastAPI` + `Next.js` 的交易与策略平台。  
它支持两种使用方式：

- **可视化 UI 模式**：在网页中完成配置、分析、回测、信号扫描、下单、通知管理
- **智能体 MCP 模式**：让 OpenClaw/Claude 等智能体通过 MCP 工具调用交易能力

---

## 1. 你可以用它做什么

### 1.1 市场与策略

- 多市场观察（A 股 / 港股 / 美股）
- 市场分析（情绪、宏观、板块轮动、数据状态标识）
- 多策略回测（支持不同 K 线周期）
- AutoTrader 扫描强势股并做策略评分
- 策略模板化（趋势 / 均值回归 / 防守）

### 1.2 交易与风控

- 手动交易：下单、撤单、持仓与订单状态查看
- 期权交易：到期日/期权链查询、单腿/多腿下单、费用试算、订单与持仓查看
- 半自动交易：生成待确认信号，人工确认执行
- 全自动交易：扫描后自动下单（可开关）
- 风控约束：仓位、交易频次、冷却、同标的限制等
- 新增限制：**美股仅允许盘前/盘中/盘后下单，夜盘禁止下单**
- MCP 分级授权：期权下单归属 L3，支持 `confirmation_token` 校验

### 1.3 自动化与通知

- 飞书机器人（WebSocket 长连接，无需公网 IP）
- 定时市场报告、交易事件推送
- 观察模式提示（连续无信号时给出原因和建议）
- 配置导入导出、快照备份与回滚
- Setup 页面支持“一键停止前后端服务”

---

## 2. 技术架构

- `api/main.py`：FastAPI API 层（前端与自动化统一入口）
- `api/auto_trader.py`：自动交易引擎与调度
- `api/engine/*`：策略组件化（EntryRule/ExitRule/Sizer/Guard）
- `mcp_server/*`：MCP Server 与扩展工具（交易、回测、告警、日志、通知）
- `frontend/`：Next.js + Tailwind + ECharts 可视化控制台

---

## 2.1 MCP 服务入口

项目当前推荐只启用中性名称 `broker-trading`，避免客户端同时启动多个 MCP 入口造成重复连接或进程状态误判：

- 兼容入口：`mcp_server/longport_mcp_server.py`（LongPort-compatible）
- 推荐入口：`mcp_server/broker_mcp_server.py`
- 配置文件：`mcp_server/mcp_config.json` 与 `.cursor/mcp.json` 默认只保留 `broker-trading`

稳定性默认项：

- `OPENCLAW_MCP_TOOL_COMPAT=standard`：返回更严格的 MCP 标准工具字段
- `OPENCLAW_MCP_START_BACKGROUND_ON_TOOL_CALL=false`：工具调用不隐式启动后台监控/定时任务
- `OPENCLAW_MCP_SINGLE_INSTANCE=false`：避免客户端重复探测时被单实例锁误判为崩溃
- `OPENCLAW_MCP_TOOL_TIMEOUT_SECONDS=120`：单次工具调用超时后返回 MCP error，不阻塞主循环

说明：

- 凭证环境变量仍兼容使用 `LONGPORT_APP_KEY` / `LONGPORT_APP_SECRET` / `LONGPORT_ACCESS_TOKEN`
- 对外文案保留 “LongPort-compatible provider” 提示，便于识别当前兼容路径
- 可运行 `python diagnose_mcp.py` 检查 `initialize -> tools/list -> tools/call` 基础链路

---

## 3. 环境要求

- Python 3.12+
- Node.js 18+（建议 LTS）
- 可访问券商 OpenAPI（当前 LongPort-compatible provider）
- Windows/macOS/Linux 均可

### 3.1 安全与合规（密钥与 Git）

1. **勿将含密钥的文件提交到 Git**  
   仓库根目录 `.gitignore` 已忽略：
   - `.env`、`.env.local`
   - `mcp_server/notification_config.json`（飞书/钉钉 Webhook、App Secret 等）
   - `api/auto_trader_config.json` 与 `api/auto_trader_config.backups.json`（交易与标的池等运行配置）

2. **首次克隆或新环境**  
   - 复制示例后本地填写：
     - `cp mcp_server/notification_config.example.json mcp_server/notification_config.json`（Windows 可用 `copy`）
     - `cp api/auto_trader_config.example.json api/auto_trader_config.json`（若需独立文件；也可首次启动由程序写入默认配置）
   - 或仅用根目录 **`.env`**（参考 `.env.example`）；飞书/券商凭证等 **环境变量优先于 JSON** 的规则见 `config/notification_settings.py` 等。

3. **若仓库曾提交过真实密钥**  
   请在各平台 **轮换/作废旧密钥**；若已 `git init` 且上述文件曾被跟踪，停止跟踪示例：
   ```bash
   git rm --cached mcp_server/notification_config.json api/auto_trader_config.json 2>/dev/null || true
   ```
   历史 commit 中仍可能存在密文，需用 `git filter-repo` 等工具清理或视为已泄露处理。

4. **生产环境暴露面**  
   - 开发示例中的 `uvicorn --host 0.0.0.0` 表示监听所有网卡，**勿在未做防护时直接暴露公网**。  
   - 推荐：API 监听 **`127.0.0.1`**，由 **Nginx/Caddy** 等反向代理提供 **HTTPS**，并配置 **IP 白名单 / 内网 VPN / 企业零信任**；前端与 API 同源或经代理统一入口。  
   - `CORSMiddleware` 在生产中应把 `allow_origins` 收紧为实际前端域名，避免长期 `*`。

---

## 4. 快速启动（推荐：UI 模式）

### 4.1 安装依赖

```powershell
cd D:\Multi-Trading\multi-trading
pip install -r requirements.txt

cd D:\Multi-Trading\multi-trading\frontend
npm install
```

### 4.2 配置 `.env`

在项目根目录创建或编辑 `.env`，至少包含：

```env
LONGPORT_APP_KEY=xxx
LONGPORT_APP_SECRET=xxx
LONGPORT_ACCESS_TOKEN=xxx
```

可选增强（市场分析与通知）：

```env
FINNHUB_API_KEY=xxx
TIINGO_API_KEY=xxx
FRED_API_KEY=xxx
COINGECKO_API_KEY=xxx
FEISHU_APP_ID=xxx
FEISHU_APP_SECRET=xxx
FEISHU_SCHEDULED_CHAT_ID=xxx

# Feishu Bot 通过 API 主进程代理访问 broker 行情源（LongPort-compatible provider，推荐开启，降低连接占用）
FEISHU_BOT_USE_API_PROXY=true
FEISHU_BOT_API_BASE_URL=http://127.0.0.1:8010
FEISHU_BOT_API_TIMEOUT_SECONDS=8
```

可选增强（OpenBB 外部研究源）：

```env
OPENBB_ENABLED=true
OPENBB_BASE_URL=http://127.0.0.1:6900
OPENBB_TIMEOUT_SECONDS=8

# TradingAgents 研究增强（默认关闭，失败自动降级）
TRADINGAGENTS_ENABLED=false
TRADINGAGENTS_TIMEOUT_SECONDS=25
TRADINGAGENTS_MAX_SYMBOLS=3
TRADINGAGENTS_LLM_PROVIDER=openai
TRADINGAGENTS_DEEP_MODEL=gpt-5.4
TRADINGAGENTS_QUICK_MODEL=gpt-5.4-mini
TRADINGAGENTS_OUTPUT_LANGUAGE=Chinese
TRADINGAGENTS_MAX_DEBATE_ROUNDS=1
TRADINGAGENTS_MAX_RISK_DISCUSS_ROUNDS=1
TRADINGAGENTS_CHECKPOINT_ENABLED=false
TRADINGAGENTS_SCORE_WEIGHT=0.25

# AutoTrader Worker 通过 API 主进程代理访问 broker 行情源（LongPort-compatible provider，推荐开启，降低连接占用）
AUTO_TRADER_WORKER_USE_API_PROXY=true
AUTO_TRADER_API_BASE_URL=http://127.0.0.1:8010
AUTO_TRADER_API_PROXY_TIMEOUT_SECONDS=8
# Worker 代理失败时是否允许回退直连 broker（LongPort-compatible provider，默认关闭，避免新增连接）
LONGPORT_DIRECT_FALLBACK=0

# API 启动时是否自动拉起独立 Worker/Supervisor（默认 false；实盘建议保持 false，只手动启动）
AUTO_TRADER_AUTOSTART_ON_API_BOOT=false

# 按「日历天数」拉 K 线时是否优先读 data/klines 缓存（与回测页「下载K线到服务器」一致）；默认 1 开启，0 关闭仅分页拉取
LONGPORT_USE_SERVER_KLINE_CACHE=1

# API 主进程可配置网关单入口（可选）
# 配置后 trade/*、/internal/longport/*、signals 会优先走该网关，失败再走本地应急路径
LONGPORT_GATEWAY_BASE_URL=
LONGPORT_GATEWAY_TIMEOUT_SECONDS=8
```

### 4.0 开发环境与小生产（运维要点）

| 项目 | 本地开发 | 小生产（完善后再长期跑） |
|------|----------|---------------------------|
| API `--reload` | **要**（改代码即热重载） | **不要**（避免多 worker、子进程句柄丢失） |
| API `--host` | `0.0.0.0` 便于局域网调试 | **`127.0.0.1`**，对外只走反代或本机 |
| 启动方式 | 双终端或脚本 `-Dev` / `--dev` | **`scripts/start-api.*`** 默认即小生产模式 |
| 日志 | 可只看控制台 | 建议用脚本 **Tee** 到 `logs/api-*.log`、`logs/frontend-*.log` |

**进程状态**：`GET /setup/services/status` 中飞书 Bot 使用 `feishu_bot_tracking`（`subprocess` / `pid_file` / `none`）+ `feishu_bot_pid`；自动交易 Supervisor / Worker 对齐为 **`auto_trader_supervisor_tracking`**、**`auto_trader_supervisor_pid`**、**`auto_trader_worker_tracking`**、**`auto_trader_worker_pid`**（API 热重载后无子进程句柄时仍以 pid 文件判断）。

**一键脚本说明**：见仓库 **`scripts/README.md`**。

### 4.3 启动后端 API

**推荐（脚本 + 日志）**

- Windows：`.\scripts\start-api.ps1`（小生产）或 `.\scripts\start-api.ps1 -Dev`（开发）
- macOS / Linux：`chmod +x scripts/start-api.sh` 后 `./scripts/start-api.sh` 或 `./scripts/start-api.sh --dev`

**手动命令（等价，应用入口以 `backend_uvicorn_spec.py` 为准）**

```powershell
cd D:\Multi-Trading\multi-trading
$env:PYTHONPATH="D:\Multi-Trading\multi-trading"
python scripts/run_api.py --dev
# 或小生产：python scripts/run_api.py
```

**生产 / 公网前建议**：仅本机或反代访问时使用 `--host 127.0.0.1 --port 8010`（不加 `--reload`），由反向代理对外提供 TLS 与访问控制；参见 **§3.1** 与上表 **§4.0**。

### 4.4 启动前端 UI

**推荐（脚本 + 日志）**

- Windows：`.\scripts\start-frontend.ps1`
- macOS / Linux：`./scripts/start-frontend.sh`

**手动命令**

```powershell
cd D:\Multi-Trading\multi-trading\frontend
npm run dev
```

访问：`http://localhost:3010`

### 4.4.1 本地验证流程（固定端口：8010 / 3010）

为避免与机器上已有服务冲突，建议用以下固定端口做联调验证：后端 `8010`，前端 `3010`。

1) 启动后端（终端 A）：

```powershell
cd D:\Multi-Trading\multi-trading
$env:PYTHONPATH="D:\Multi-Trading\multi-trading"
python scripts/run_api.py --host 127.0.0.1 --port 8010
```

2) 启动前端（终端 B）：

```powershell
cd D:\Multi-Trading\multi-trading\frontend
cmd /c "set PORT=3010&&npm run dev"
```

3) 验收检查：

- 前端：打开 `http://127.0.0.1:3010`，确认 `dashboard` 可加载
- 后端健康：`http://127.0.0.1:8010/health` 返回 `{"ok":true,...}`
- 配置接口：`http://127.0.0.1:8010/setup/config` 返回 200
- 诊断接口：`http://127.0.0.1:8010/setup/longport/diagnostics` 返回 200

如果端口被占用，可先用 `netstat -ano | Select-String ":8010|:3010"` 查看占用进程后再重试。

### 4.5 一键启动（Windows 可执行文件）

如果你使用 `dist/MultiTradingLauncher.exe` 启动，会执行以下自检：

- 后端 OpenAPI 关键路由检查（包括期权与 stop-all）
- 前端关键页面路由检查（`/`、`/setup`、`/options`、`/trade`、`/backtest`）
- 前端源码哈希标记检查（`frontend/app`、`frontend/components`、`frontend/lib`）
- 单实例锁（防止重复双击并发启动多套服务）
- 后端守护（watchdog）单实例锁（防止重复守护并发重启）

若任一检查不通过，会自动重启并在必要时执行前端重建。

说明：

- 启动器默认只打开前端页面 `http://127.0.0.1:3010`
- 不会自动打开 OpenBB 页面 `http://127.0.0.1:6900`

---

## 5. 页面说明（UI）

- `dashboard`：多市场概览、关键指标、风险与状态
- `market`：综合市场分析 + 板块轮动
- `signals`：信号检测与实时价格信息
- `backtest`：策略回测、收益曲线与回撤曲线；可将 K 线**下载到服务器**目录 `data/klines/`（>1000 根由后端分页拉取合并），勾选「回测使用服务器已下载的 K 线」后 `POST /backtest/compare` 直接读缓存，减少 broker API 重复请求与超时；可用「从自动交易同步 ML」从 `GET /auto-trader/status` 的 `config` 拉取 `ml_filter_*`、`ml_walk_forward_windows` 等与实盘一致的 ML 参数
- `trade`：下单、持仓、订单与撤单
- `options`：期权链查询、策略建仓、费用试算、下单确认、期权订单/持仓、期权回测
- `notifications`：通知与机器人状态；**通知偏好**（定时市场报告、半自动/全自动/观察模式飞书开关、与信号中心同源的 API 底部反转监控标的等）读写后保存至 `mcp_server/notification_config.json` → `notification_preferences`
- `auto-trader`：自动交易控制台（模板、参数、扫描、配置回滚等）；可在自动交易页「**参与评分策略**」列表中点击 **「编辑」** 弹窗修改 **`strategy_params_map`**，与「并入矩阵优选前三」追加变体**并行综合评分**；`POST /auto-trader/config` 与配置导入会持久化该字段。策略评分与信号检测按配置「天数」拉 K 线时，后端会**分页对齐自然日窗口**，并**优先使用** `data/klines/` 下与标的+周期+天数匹配的缓存（可先在同标的的回测页「下载K线到服务器」写入同一路径）
- `setup`：系统初始化配置（API Key / OpenBB 配置 / 服务启动 / 停止前后端）

---

## 5.1 期权策略原理（QQQ 0DTE / 股票期权日内）

本系统的 QQQ 期权策略统一由 `mcp_server/strategy_qqq_0dte` 模块驱动，核心入口是 `strategy_variant`。  
实盘中：

- `0dte` 与股票期权日内（内部实例 `1dte`）共用同一套策略逻辑（信号、开平仓规则一致）
- 两者主要差异在到期解析（`expiry_offset_days`）：`0dte=0`、股票期权日内内部实例 `1dte=1`（可在 live worker 配置中调整）

### A) reaction_zone（反应区单腿）

**核心思想**：基于昨高/昨低/昨收/今开与心理价位构建“关键位反应区”，结合成交量与形态确认做单腿突破/反转。

- 先做交易时段与闸门过滤：仅 RTH，开盘前几分钟禁开，超过新开仓截止时刻不再开新仓
- 在关键位附近识别活跃反应区，结合放量条件（`volume_lookback_bars` + `volume_spike_multiplier`）
- 通过突破确认或回踩反转确认后，买入 Call 或 Put（按 `strike_step` + `call/put_strikes_otm` 选约）
- 单腿退出按优先级：止盈（`take_profit_pct`）/止损（`stop_loss_pct`）/最大持仓分钟（`max_hold_minutes`）

适合：趋势刚启动或关键位博弈明确、且希望用较少参数覆盖大多数日内场景。

### B) morning_strangle（早盘宽跨双腿）

**核心思想**：早盘指定时间窗内，若价格相对昨收波动不大，则同时买入 Call + Put，押注短时波动扩张。

- 入场窗口：`strangle_entry_start_hhmm_et` 到 `strangle_entry_end_hhmm_et`
- 仅当 `|chg(prev_close)| <= strangle_range_pct` 才开仓，避免高单边趋势时硬做宽跨
- 同时开两腿，分别记录成本，支持整组与单腿两套风控
- 退出机制：
  - 单腿止损/止盈：`strangle_leg_stop_loss_pct` / `strangle_leg_take_profit_pct`
  - 组合止损/止盈：`strangle_stop_loss_return` / `strangle_take_profit_return`
  - 组合止损冷静期：`strangle_stop_loss_cooldown_minutes`
  - 到达强平时刻：`strangle_force_close_hhmm_et`

适合：开盘后短时间内方向不明、但预期会有波动放大的交易日。

### C) morning_directional（早盘方向单腿）

**核心思想**：在与宽跨相同的早盘时间窗，直接根据相对昨收的涨跌幅阈值做单腿方向。

- 入场窗口同宽跨（`strangle_entry_*`）
- 若跌幅达到 `directional_down_pct`，买 Call；若涨幅达到 `directional_up_pct`，买 Put
- 每日次数由 `max_trades_per_day` 限制
- 退出机制：
  - 单腿止盈：`directional_take_profit_return`
  - 单腿止损：`directional_stop_loss_pct`（设为 0 可关闭）
  - 到达强平时刻：`strangle_force_close_hhmm_et`

适合：早盘单边方向清晰，希望用简单阈值快速跟随。

### D) gamma_scalping（开盘 Gamma 剥头皮）

**核心思想**：在开盘窗口内做“突破 + 回归”两类短线单腿信号，并加入 VIX 与龙头股联动确认。

- 时间窗：`gamma_entry_start_hhmm_et` 到 `gamma_entry_end_hhmm_et`
- 两类信号：
  - 突破昨高/昨低（可要求 `gamma_require_breakout_prev_day=true`）
  - VWAP 偏离后首次回归（`gamma_enable_vwap_reversion` + `gamma_vwap_deviation_pct`）
- VIX 门槛：`gamma_require_vix_rising` + `gamma_vix_rising_min_pct`
- 龙头确认：`gamma_require_leader_confirmation`，结合 QQQ 与龙头涨跌幅差（`gamma_leader_*`、`gamma_rt_*`）
- 退出机制：硬止损（`gamma_hard_stop_loss_pct`）+ 快止盈（`gamma_take_profit_min_return`）+ 最长持仓（`gamma_max_hold_minutes`）+ 强平时刻（`gamma_force_close_hhmm_et`）

适合：开盘后高波动、节奏快、对执行速度和风控一致性要求高的场景。

### E) gamma_pro（Gamma Pro 扩展）

**核心思想**：在 gamma_scalping 基础上扩展“假突破反手 + 午后续航”，更偏全天分段交易。

- 开仓窗更靠后：`gamma_pro_entry_start_hhmm_et` ~ `gamma_pro_entry_end_hhmm_et`
- 午间暂停：`gamma_pro_midday_skip_start_hhmm_et` ~ `gamma_pro_midday_skip_end_hhmm_et`
- 新增信号：
  - 假突破反手（`gamma_pro_enable_false_breakout_reversal`）
  - 午后回踩 VWAP 续航（`gamma_pro_afternoon_start_hhmm_et` + `gamma_pro_vwap_pullback_pct`）
- 龙头确认可独立开关：`gamma_pro_require_leader_confirmation`
- 退出机制：`gamma_pro_hard_stop_loss_pct`、`gamma_pro_take_profit_return`、`gamma_pro_max_hold_minutes`、`gamma_pro_force_close_hhmm_et`

适合：希望把交易窗口延展到午后，同时保持 Gamma 类策略快进快出的风控框架。

### F) 参数与落盘规则（实盘重要）

- `live_worker_config.json` 中策略参数位于 `strategy_config`
- 系统会按当前 `strategy_variant` 只保留本策略相关字段，并补齐缺失默认值
- 建议优先在前端面板修改并保存，再启动 Worker，避免策略变体与参数集不一致

---

## 6. AutoTrader 说明

### 6.1 工作流程

1. **强势股筛选**（`screen_strong_stocks`，按 `top_n` 截断；非 `pair_mode` 下只对这批标的继续往下走）  
2. **策略评分**（`score_strategies`：在配置项 `strategies` 列表上对单标的回测/打分，取 **`scored[0]`** 作为该标的的 **`best` 策略）**  
3. **入场 / 出场规则判断**（`StrategyPipeline.evaluate_entry` / `evaluate_exit`；见 **6.3**）  
4. **Guard 风控拦截**（冷却、日内次数、已有仓位等）  
5. **可选 ML 过滤**（买入路径上 `ml_filter_enabled` 等）  
6. 生成信号（待确认 / 自动执行 / 演练）  
7. 通知与日志  

**说明**：`pair_mode` 下标的来自配对列表，但仍是「先评分选 `best` 策略 → 再判入场」；**自动卖出**路径遍历**当前持仓**，对每只持仓同样先 `score_strategies` 取 `best`，再 `evaluate_exit`。

#### 6.1.1 独立 Worker（扫描进程）

- 定时扫描、信号与 `.auto_trader_worker.runtime.json`（前端「worker 更新时间」等）由 **独立进程** `api/auto_trader_worker.py` 在 **Supervisor** 守护下运行，与 **API 主进程分离**。
- 在 **自动交易页保存配置**、模板应用、配置导入/回滚、Agent 改配置、研究参数应用时，后端只保存配置，**不应自动启动或停止** Supervisor。
- 股票自动交易 Worker 应只能通过 Auto Trader 页面手动启动/重启，或由显式服务启动接口 **`POST /setup/services/start`**（`enable_auto_trader: true`）启动。
- **重启 API** 默认不应自动拉起 Worker；实盘环境建议保持 **`AUTO_TRADER_AUTOSTART_ON_API_BOOT=false`**。

### 6.2 模式

- **半自动**：生成信号后手动确认下单
- **全自动**：满足条件直接下单
- **演练模式**：只产出信号，不真实下单（推荐新模板先用）

### 6.3 入场与出场规则（引擎语义）

实现位置：`api/engine/rules_entry.py`、`api/engine/rules_exit.py`、`api/engine/pipeline.py`；装配与扫描：`api/auto_trader.py`（`_build_entry_rule` / `_build_exit_rules`、`_run_scan_once_inner`）。K 线周期由 **`kline`** 决定；入场/卖出信号判定使用的历史根数由 **`signal_bars_days`**（写入 `ScanContext.bars_days`）决定。

#### 6.3.1 与策略评分的关系

- **标的池**：普通模式下仅对 **强势股筛选结果**（至多 `top_n` 只）做后续步骤。  
- **用哪条策略**：对每只标的，`score_strategies` 在 **`strategies`** 中排序后，**`best = scored[0]`** 的 `best["strategy"]` 会写入 `ScanContext.strategy_name`（及 `strategy_params`）。  
- **`strategy_cross`（策略交叉入场）**：用 **上述胜出策略** 的 `get_strategy(...)` 在 K 线上判断是否出现 **`buy`**——即 **先评分选策略，再在该策略上判交叉/信号**；评分本身不负责发出买卖指令。  
- **`breakout` / `mean_reversion`**：入场条件由价量/RSI/均线等规则决定，**不**读取策略的 `buy/sell` 输出（`strategy_name` 仍会传入上下文供元数据等使用）。

#### 6.3.2 入场规则（`entry_rule`）

同一时间只启用 **一种** 入场规则，由 **`entry_rule`** 选择：

| 取值 | 含义 |
|------|------|
| `strategy_cross` | 用 **`best["strategy"]`** 对应的策略函数在 K 线上判断是否 **买入** |
| `breakout` | 收盘价突破前 N 根最高价 + 可选成交量倍数 |
| `mean_reversion` | RSI（14）超卖 + 收盘价相对 MA20 的下方偏离 |

**`strategy_cross`**

- 至少 **25** 根 K 线。  
- **严格**（`signal_relaxed_mode: false`）：上一根策略输出 `prev != "buy"` 且当前最后一根 `now == "buy"`（上升沿）。  
- **宽松**（`signal_relaxed_mode: true`）：只要最后一根 `now == "buy"`。

**`breakout`**

- `breakout_lookback_bars`（默认 20，代码下限 **5**）、`breakout_volume_ratio`（默认 **1.2**）。  
- 最后一根 **收盘价** > 前 `lookback_bars` 根（不含最后一根）的 **最高价**。  
- 最后一根成交量 ÷ 上述窗口均量 ≥ `breakout_volume_ratio`；若 **`breakout_volume_ratio` = 0**，则不检查成交量。

**`mean_reversion`**

- `mean_reversion_rsi_threshold`（默认 **35**）、`mean_reversion_deviation_pct`（默认 **2.0**）；**MA 周期固定 20**。  
- **RSI14** ≤ `mean_reversion_rsi_threshold`。  
- `dev_pct = (MA20 - 最新收盘) / MA20 × 100`（价在均线下方），且 **dev_pct ≥ mean_reversion_deviation_pct**。  
- 两条件**同时满足**才入场。

#### 6.3.3 出场规则（`exit_rules` + 内置优先级）

- **`exit_rules`**：启用哪些规则，可选：`hard_stop`、`take_profit`、`time_stop`、`strategy_sell`。  
- **`rule_priority`**：构建规则列表时用于排序与补全；**真正评估顺序**由各类上 **`priority` 数值升序**决定（**先命中先退出**），与配置列表书写顺序无关：

| 规则 ID | 内置 priority（越小越先） |
|---------|---------------------------|
| `hard_stop` | 10 |
| `take_profit` | 20 |
| `time_stop` | 30 |
| `strategy_sell` | 40 |

**`hard_stop`**：`pnl_pct ≤ -|hard_stop_pct|`。  
**`take_profit`**：`pnl_pct ≥ |take_profit_pct|`。  
**`pnl_pct`** = `(current_price - avg_cost) / avg_cost × 100`（`api/engine/types.py` · `PositionSnapshot`）。

**`time_stop`**：当前时间 − **`opened_at`** ≥ `time_stop_hours`；无有效 **`opened_at`** 则不触发。

**`strategy_sell`**：对持仓标的同样先取 **`best` 策略**，再用策略函数判 **卖出**；严格/宽松与 `strategy_cross` 对称（`sell` 上升沿 vs 仅当前根 `sell`）。

#### 6.3.4 与模板及风控的关系

策略模板（`POST /auto-trader/template/apply`）会改写 `entry_rule`、`exit_rules`、`rule_priority` 及止损/止盈/时间等数值；以 **`api/auto_trader_config.json`** 与接口返回为准。更细的参数释义见 **`参数与术语说明.md`**。

---

### 6.4 策略模板

- `trend`：偏趋势跟随（已接入 breakout 入场）
- `mean_reversion`：偏短线回归（已接入均值回归入场）
- `defensive`：偏防守，优先控制回撤

### 6.5 Research 层（P0/P1/P2）与 OpenBB 增强

- 一键运行 Research，生成快照并支持导出 JSON / 模型对比 CSV
- 研究快照包含：
  - `external_research.market_regime`（外部市场状态）
  - `external_research.symbol_factors`（外部因子样本）
  - `external_research.tradingagents_insights`（多代理研究洞察，可选）
  - `allocation_plan`、`strategy_rankings`、`pair_backtest`
- 新增 TradingAgents 研究加权（可选，不进入下单关键路径）：
  - 从 `TRADINGAGENTS_SCORE_WEIGHT`（或配置项 `research_tradingagents_weight`）读取权重
  - 按 `action/confidence` 对研究评分做乘子调整（`buy` 提升、`sell` 抑制、`hold` 不变）
  - 快照新增 `agent_gating` 便于复盘与解释
- 新增 regime gating（研究层，不进入下单执行关键路径）：
  - 按 `risk_on / neutral / risk_off` 调整策略评分乘子
  - 按状态限制单标的上限与目标总暴露
  - 按置信度计算有效总暴露：
    - `effective_exposure = target_gross_exposure * (0.6 + 0.4 * confidence)`
- 前端分配计划表可查看：
  - `weight_raw`（原始权重）
  - `weight`（建议权重）
  - `Δ权重`（gating 调整差值）

---

## 7. 智能体接入（MCP 模式）

### 7.1 MCP 服务配置（示例）

在 Claude Desktop 的配置中加入（路径按你的机器实际修改）：

```json
{
  "mcpServers": {
    "broker-trading": {
      "command": "C:\\Path\\To\\python.exe",
      "args": [
        "D:\\Multi-Trading\\multi-trading\\mcp_server\\broker_mcp_server.py"
      ],
      "env": {
        "PYTHONPATH": "D:\\Multi-Trading\\multi-trading",
        "MCP_SERVER_NAME": "broker-trading"
      }
    }
  }
}
```

### 7.2 Agent 安全调参接口

为了让智能体可调参数但不破坏硬风控，项目提供：

- `GET /auto-trader/config/policy`：查询可调字段与取值范围
- `POST /auto-trader/config/agent`：按白名单更新参数

硬风控字段（如总仓位/最小现金比/单标的上限/日交易上限）会被锁定，越权会返回 `policy_violation`。

---

## 8. 核心 API（常用）

### 8.1 健康与配置

- `GET /health`
- `GET /setup/config`
- `POST /setup/config`
- `GET /setup/services/status`（飞书机器人状态同时参考 **本进程子进程句柄** 与 **`mcp_server/.feishu_bot.pid`**，避免 API 热重载后界面误显示「已停止」）
- `POST /setup/services/start`
- `POST /setup/services/stop`
- `POST /setup/services/stop-all`
- `GET /setup/longport/diagnostics`

### 8.1b 通知偏好

- `GET /notifications/status`（含 `preferences_summary` 摘要）
- `GET /notifications/preferences`（完整偏好 + 默认值参考）
- `PUT /notifications/preferences`（JSON 体为偏好对象，或与 `GET` 相同结构；服务端与当前已存配置**深度合并**后校验落盘）

环境变量（可选）：

- `NOTIFICATION_API_REVERSAL_WATCH`：默认 `true`；`false` 时 API 进程不启动「底部反转」后台轮询线程（仍可在通知中心编辑并保存偏好）。

### 8.2 市场与分析

- `GET /dashboard/summary`
- `GET /market/analysis`
- `GET /market/sectors`
- `GET /signals`

### 8.3 回测

- `GET /backtest/compare`
- `GET /auto-trader/pair-backtest`

### 8.4 交易

- `GET /trade/account`
- `GET /trade/positions`
- `GET /trade/orders`
- `POST /trade/order`
- `POST /trade/order/{order_id}/cancel`

### 8.5 期权

- `GET /options/expiries`
- `GET /options/chain`
- `POST /options/fee-estimate`
- `POST /options/order`
- `GET /options/orders`
- `GET /options/positions`
- `POST /options/backtest`

### 8.6 AutoTrader

- `GET /auto-trader/status`
- `POST /auto-trader/config`
- `POST /auto-trader/scan/run`
- `GET /auto-trader/signals`
- `POST /auto-trader/signals/{signal_id}/confirm`
- `GET /auto-trader/templates`
- `GET /auto-trader/template/preview`
- `POST /auto-trader/template/apply`
- `GET /auto-trader/config/export`
- `POST /auto-trader/config/import`
- `GET /auto-trader/config/backups`
- `GET /auto-trader/config/rollback/preview`
- `POST /auto-trader/config/rollback`
- `GET /auto-trader/config/policy`
- `POST /auto-trader/config/agent`
- `POST /auto-trader/research/run`
- `GET /auto-trader/research/snapshot`
- `GET /auto-trader/research/status`
- `GET /auto-trader/research/model-compare`
- `POST /auto-trader/research/strategy-matrix/run` · `GET /auto-trader/research/strategy-matrix/result?market=`（可选 `market`，缺省用当前自动交易配置里的市场；**按市场分文件缓存** `.auto_trader_research.strategy_matrix.{us|hk|cn}.json`，切换市场互不覆盖；旧版单文件 `.auto_trader_research.strategy_matrix.json` 仅在对应市场无新文件时作回退读取。异步去重会对比 **市场、TopN、K 线、回测天数、matrix_overrides 等**；若写入失败 API 日志会有 `research _write_json failed`）
- `POST /auto-trader/research/ml-matrix/run` · `GET /auto-trader/research/ml-matrix/result?market=`（同上，缓存为 `.auto_trader_research.ml_matrix.{us|hk|cn}.json`，旧单文件 `.auto_trader_research.ml_matrix.json` 作同市场回退。日 K 下后端默认至少 **300 日历日**：交易所按「交易日」返回 bar，约需 **≥140 根** 才能凑够 **≥80** 行净特征；结果里可看 `bar_fetch_preflight`）
- `POST /auto-trader/research/ml-matrix/apply-to-config`（将 **当前配置市场** 下已缓存的 ML 矩阵最优参数写入 `auto_trader_config.json`，可选 `variant`：`auto`/`balanced`/`high_precision`/`high_coverage`/`best_score`，`enable_ml_filter` 默认 true；保存后会按 `enabled` 同步 Worker 进程；Worker 运行中会从配置文件读取更新）
- `GET /auto-trader/metrics/recent`
- `GET /auto-trader/metrics/sla`

#### 8.6.1 策略矩阵加速模板（可直接复制）

策略矩阵已支持三项加速能力：并行回测、增量缓存、早停剪枝。  
可在 `POST /auto-trader/research/strategy-matrix/run` 里通过 `matrix_overrides` 调参。

**保守（最快）**

```json
{
  "async_run": true,
  "top_n": 6,
  "max_strategies": 4,
  "max_drawdown_limit_pct": 25,
  "min_symbols_used": 3,
  "matrix_overrides": {
    "use_config_strategies_only": true,
    "parallel_workers": 4,
    "backtest_days": 90,
    "max_total_variants": 80,
    "max_variants_per_strategy": 6,
    "max_eval_cache_entries": 30000
  }
}
```

**平衡（推荐默认）**

```json
{
  "async_run": true,
  "top_n": 8,
  "max_strategies": 6,
  "max_drawdown_limit_pct": 30,
  "min_symbols_used": 4,
  "matrix_overrides": {
    "use_config_strategies_only": true,
    "parallel_workers": 6,
    "backtest_days": 120,
    "max_total_variants": 160,
    "max_variants_per_strategy": 10,
    "max_eval_cache_entries": 50000
  }
}
```

**激进（覆盖更全面，耗时更长）**

```json
{
  "async_run": true,
  "top_n": 10,
  "max_strategies": 8,
  "max_drawdown_limit_pct": 35,
  "min_symbols_used": 4,
  "matrix_overrides": {
    "use_config_strategies_only": false,
    "parallel_workers": 8,
    "backtest_days": 180,
    "max_total_variants": 320,
    "max_variants_per_strategy": 16,
    "max_eval_cache_entries": 80000
  }
}
```

返回结果中的 `perf` 字段可用来判断是否真正提速：

- `parallel_workers`：并行线程数（通常 4~8 最稳）
- `cache_hits` / `cache_misses`：命中率越高，重复运行越快
- `early_stop_variants`：越高表示剪枝越有效
- `cache_entries`：当前策略评估缓存条目数

策略评估缓存按市场落盘到：

- `.auto_trader_research.strategy_eval_cache.{us|hk|cn}.json`

### 8.7 OpenBB 外部研究接口

- `GET /research/external/openbb/health`
- `GET /research/external/openbb/market-regime`
- `GET /research/external/openbb/symbol-factor`

---

## 9. 飞书机器人（WebSocket 模式）

项目支持飞书指令机器人，常用命令包括：行情、分析、买入、卖出、持仓、订单、取消、市场分析、板块轮动等。

默认推荐 **API 代理模式**（由后端 API 进程统一访问 broker 行情源，当前 LongPort-compatible provider），可显著降低多进程并发时的连接占用：

```env
FEISHU_BOT_USE_API_PROXY=true
FEISHU_BOT_API_BASE_URL=http://127.0.0.1:8010
FEISHU_BOT_API_TIMEOUT_SECONDS=8
```

如需回退为飞书机器人直连 broker（当前 LongPort-compatible provider，不推荐），可设置：

```env
FEISHU_BOT_USE_API_PROXY=false
```

启动示例：

```powershell
cd D:\Multi-Trading\multi-trading
$env:PYTHONPATH="D:\Multi-Trading\multi-trading"
python mcp_server/feishu_command_bot.py
```

---

## 10. 关键风控说明

- 下单前会进行账户与风险校验
- 支持同标的冷却、同日次数限制、最大并发持仓等限制
- AutoTrader 支持 Guard 链路记录（便于解释“为何买/为何卖/为何跳过”）
- **美股下单时段限制**：仅允许盘前/盘中/盘后，夜盘禁止下单

---

## 11. 常见问题

### Q1: 前端打开慢或经常超时

- 先确认后端 `8000` 与前端 `3000` 是否都正常启动
- 检查 API Key 是否配置完整
- 查看 `dashboard/market` 是否命中外部数据超时（会自动兜底）

### Q2: Setup 页面保存后显示未配置

- 检查 `.env` 是否写入成功
- 确认服务是否重启并重新加载环境变量

### Q2.1: Setup 页没有“停止前后端服务”按钮

- 大概率是前端还在使用旧构建
- 先停止 3010 端口前端，再执行：
  - `cd D:\Multi-Trading\multi-trading\frontend`
  - `npm run build`
  - `npm run start`
- 或直接使用最新版 `dist/MultiTradingLauncher.exe` 自动自检并重建

### Q3: AutoTrader 没有信号

- 先用演练模式 + 手动扫描看 `decision_log`
- 检查是否被 Guard 拦截（冷却、日内限制、已有持仓等）
- 可尝试切换模板（趋势/均值回归）

### Q3.1: 点击 Research 偶发后台断连/重启

- 现已增加 watchdog 单实例锁，避免多个守护进程并发重启
- 研究任务执行期间会写入 busy 标记，守护进程会暂缓健康重启判定
- 若仍偶发，先确认只运行一个 `MultiTradingLauncher.exe` 实例

### Q3.2: 强势股列表在变，但「决策链路」很久不更新

- **正常现象（数据源不同）**：`GET /auto-trader/strong-stocks` 在 Worker 本轮名单为空等情况下，会用 **API 进程内的即时筛选 + 短周期缓存**（默认约十余秒 TTL，见 `AUTO_TRADER_STRONG_STOCKS_CACHE_TTL_SECONDS`）刷新列表；而 **决策链路** 只来自 Worker **`run_scan_once` 结束** 时写入的 **`last_scan_summary.decision_log`**，只有完整扫描跑完才会变。
- **若「Worker 完整扫描」时间也很久不变**：再查 Worker/Supervisor 是否在跑、`enabled`、间隔 `interval_seconds` 与 `GET /auto-trader/status` 的 `last_scan_at`（已与 Worker 的 `last_scan_summary.scan_time` 对齐，不再误用 API 进程内未参与扫描的占位字段）。
- 自动交易页已区分 **列表刷新** 与 **Worker 完整扫描** 时间；决策链标题下展示 **数据时间（Worker 完整扫描）**。

### Q4: 为什么下单被拒绝

- 查看返回的 `detail` 字段（会包含具体风控原因）
- 美股在夜盘时段提交订单会被明确拒绝
- 期权下单需满足 L3 授权与 `confirmation_token` 校验（若已配置）

### Q5: 期权接口返回 `{"detail":"Not Found"}`

- 通常是连接到了旧后端实例
- 请重启后端或使用最新版 `MultiTradingLauncher.exe`
- 确认 `http://127.0.0.1:8010/openapi.json` 中存在 `/options/*` 路由

### Q6: 前端日志提示 `Blocked cross-origin request ... allowedDevOrigins`

- 这是 Next.js 开发模式跨源来源校验告警
- 请在 `frontend/next.config.js` 的 `allowedDevOrigins` 中包含你的访问来源（如 `localhost`、`127.0.0.1`、局域网 IP）
- 建议访问域名保持一致，不要混用 `localhost` 与 `127.0.0.1`

### Q7: Setup 显示自动交易「已停」但 pid 文件里进程还在 / 或相反

- 先看 `GET /setup/services/status` 的 **`auto_trader_supervisor_tracking`** / **`auto_trader_worker_tracking`**：`subprocess` 表示 API 进程仍持有启动时的句柄；`pid_file` 表示仅靠磁盘 pid 判断（常见于 **`--reload` 或 API 重启** 后句柄丢失）。
- 停止服务应使用 Setup 或 **`POST /setup/services/stop`**；若仍异常，可手动结束对应 PID 后删除残留 `*.pid`（参见 **§3.1** 运行时文件说明）。

### Q8: 飞书机器人窗口一闪就退出

- 在仓库根目录执行，并保证 **`PYTHONPATH`** 指向仓库根（脚本已设置）；查看 **`mcp_server/notification_config.json`** 是否配置完整。
- 需要留痕时：`python -u mcp_server/feishu_command_bot.py 2>&1 | Tee-Object -FilePath logs\feishu-bot.log -Append`（PowerShell）。

### Q9: Worker 扫描时间不动 / 接口返回 `409` `worker_not_running`

- 确认 **`auto_trader_config.json`** 里 **`enabled=true`**，且已在 Auto Trader 页面手动启动/重启 Worker；保存配置本身不应启动 Supervisor。
- 小生产请勿对 API 使用 **`--reload`**，否则子进程管理易与预期不一致；见 **§4.0**。

---

## 12. 项目结构（简版）

```text
multi-trading/
├── api/                     # FastAPI + AutoTrader + 策略引擎
│   ├── main.py              # 应用装配层（FastAPI 初始化 / 中间件 / 异常处理 / include_router）
│   ├── runtime_bridge.py    # 路由到运行时资源/服务的桥接层（避免路由直接耦合 main）
│   ├── schemas_setup.py     # Setup 域请求模型
│   ├── schemas_auto_trader.py # Auto-Trader 域请求模型（配置 + research/runtime）
│   ├── schemas_fees_risk.py # Fees/Risk 域请求模型
│   ├── schemas_options_trade.py # Options/Trade 域请求模型
│   ├── schemas_backtest.py  # Backtest 域请求模型
│   ├── routers/             # 领域路由（setup / notifications / fees-risk / options-trade / dashboard-market / backtest / auto-trader）
│   └── services/            # 共享服务层（配置/诊断/进程控制/费用风控/期权交易等）
├── frontend/                # Next.js UI
├── mcp_server/              # MCP 服务与扩展工具
├── scripts/                 # 启动脚本（含 run_api.py，与 launcher 共用 uvicorn 规格）
├── logs/                    # 运行日志目录（*.log 默认不入库，见 .gitignore）
├── backend_uvicorn_spec.py  # 后端 uvicorn 参数单一来源（launcher / run_api）
├── runtime_process_utils.py # pid 文件与子进程状态（launcher / api.main 共用）
├── config/                  # 配置模块
├── requirements.txt
└── README.md
```

---

## 13. 安全建议

- 不要把 `.env`、密钥文件提交到代码仓库
- 实盘前先做回测与演练模式验证
- 新策略先小仓位、逐步放量
- 对智能体只开放白名单参数，硬风控始终由人工掌控
- 本软件输出的交易信号、评分、报告和通知仅供学习研究参考，不构成投资建议；任何实盘交易请自行决策并承担风险

---

## 14. 开源许可、致谢与风险声明

本项目以 [Apache License 2.0](./LICENSE) 开源发布。该许可证保留版权声明、专利授权和免责声明，适合社区协作与商业友好的二次开发。

请同时阅读：

- [NOTICE](./NOTICE)：项目版权、品牌与第三方致谢
- [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md)：主要开源依赖与上游项目说明
- [DISCLAIMER.md](./DISCLAIMER.md)：交易风险、投资建议与自动化执行免责声明
- [CONTRIBUTING.md](./CONTRIBUTING.md)：贡献规范
- [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md)：社区行为准则
- [SECURITY.md](./SECURITY.md)：安全策略与漏洞报告方式

特别感谢以下开源项目和社区：

- [TradingAgents](https://github.com/TauricResearch/TradingAgents)：多智能体金融研究框架，本项目可选集成其研究链路。
- [OpenBB](https://github.com/OpenBB-finance/OpenBB)：开放金融数据平台，本项目可选接入 OpenBB API/服务能力。
- [FastAPI](https://github.com/fastapi/fastapi)、[Next.js](https://github.com/vercel/next.js)、[Model Context Protocol](https://github.com/modelcontextprotocol)、[Longbridge OpenAPI SDK](https://github.com/longbridgeapp/openapi) 等基础设施项目。

Multi-Trading 与上述项目、品牌或公司没有官方从属、背书或授权关系，除非另有明确说明。使用第三方服务、SDK 或数据源时，请自行遵守其许可证、服务条款、数据授权和所在地区监管要求。

---

## 文档入口

- [参数与术语说明](./参数与术语说明.md)
- [参数速查表（保守/平衡/激进）](./参数速查表.md)

---

## 15. 参考链接

- 沟通交流：QQ 178522360 ｜ 邮箱 [178522360@qq.com](mailto:178522360@qq.com)
- [V1.0.16 Windows 安装包下载](https://github.com/weiyuan0917-a11y/multi-trading/releases/tag/v1.0.16)
- [全部发布版本](https://github.com/weiyuan0917-a11y/multi-trading/releases)
- [LongPort OpenAPI (LongPort-compatible provider)](https://open.longportapp.com/)
- [Model Context Protocol](https://github.com/modelcontextprotocol)

---

欢迎在此基础上继续扩展策略与风控，先稳后快。
# Multi-Trading Quant Trading System

> Note: parts of this historical README contain mojibake from earlier encoding
> issues. Use [docs/engineering-health.md](docs/engineering-health.md) for the
> current UTF-8 engineering, verification, and live-trading safety checklist.
>
> Cloud console deployment notes live in
> [docs/cloud-console-deployment.md](docs/cloud-console-deployment.md).
