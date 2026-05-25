# Multi-Trading

Multi-Trading 是一个本地优先的量化交易与研究控制台。当前实现由本地 FastAPI Agent、Next.js 前端、券商/行情接入、QQQ 期权策略、股票自动交易、Agent Strategy Lab、License/支付控制台组成。

> 风险提示：本项目包含实盘交易能力。所有策略、回测、智能体建议都不是投资建议；实盘下单前必须确认账户、owner、account、风控参数、License 与 kill switch 状态。

## 当前入口

默认端口固定为：

- 本地后端：`http://127.0.0.1:8010`
- 前端页面：`http://127.0.0.1:3010`
- API 文档：`http://127.0.0.1:8010/docs`

常用页面：

| 页面 | 地址 | 用途 |
| --- | --- | --- |
| 设置 | `/setup` | 券商账户、行情 API、飞书、LLM、Convex dev 管理 |
| QQQ 0DTE 实盘 | `/auto-trading/options-0dte` | 0DTE worker 启停、配置、状态、恢复展示 |
| QQQ 1DTE 实盘 | `/auto-trading/options-1dte` | 1DTE worker 启停、配置、状态、恢复展示 |
| 股票自动交易 | `/auto-trading/stocks` | 股票 worker、策略与风控 |
| QQQ 策略验证 | `/strategy/qqq-0dte` | 参数表单、矩阵回测、调试验证 |
| Agent Strategy Lab | `/agent-strategy-lab` | 智能体候选参数、异步验证、审批写入草稿 |
| 回测 | `/backtest` | 通用回测任务与结果 |
| TradingAgents | `/tradingagents` | 多智能体金融研究入口 |
| 账单 | `/billing` | 本地/云端订阅与支付入口 |
| License 管理 | `/admin/licenses` | 管理员签发和投递本地 License |
| 订单管理 | `/admin/orders` | 半自动二维码订单确认 |

`/admin/licenses` 和 `/admin/orders` 只属于管理员版。普通用户发布包应使用 `scripts/create-release.ps1 -Edition user` 生成，发布脚本会物理移除管理员页面、管理员 API、billing server proxy 和 Convex 签发端源码。

## 已实现功能

### 本地 Agent 与多账户

- FastAPI 后端默认监听 `127.0.0.1:8010`。
- Next.js 前端默认监听 `3010`，不要再使用旧的 `3000` 作为本项目入口。
- 支持本地账号、owner、account 维度隔离。
- 券商配置、行情 API、飞书、LLM 等密钥按登录 owner 保存到 `data/user_env/<owner>.env`。
- 券商账户支持同一 owner 下多个 account；worker 启动时显式携带 owner/account，并传入内部行情与交易 API。
- 本地后端不再信任浏览器伪造的 `X-MT-Cloud-Plan`、`X-MT-Cloud-Role`、`X-MT-Cloud-Is-Admin` 权限头；付费能力以本地有效 License 为准。

### QQQ 0DTE / 1DTE 自动期权交易

- 支持 QQQ 0DTE 与 1DTE 页面、配置读写、worker 启停、状态刷新。
- worker 从券商 API 或本地服务端 K 线缓存获取分时行情，并在状态区展示 quote/bars 来源。
- 实盘决策日志写入 `data/qqq_0dte/live_worker_decision_tail.jsonl` 或 `data/qqq_1dte/live_worker_decision_tail.jsonl`。
- 执行流水和恢复快照用于断连/重启保护，worker 重启后可恢复自己已开仓的订单，继续等待止盈、止损或强平信号。
- 已加入外部持仓保护：worker 只处理自己 ledger 中确认的 worker-owned 仓位，避免后端重启或手动测试时误卖账户里原有持仓。
- 已加入期权卖出保护：如果某条腿已被手动平仓或券商持仓不存在，worker 不应继续发出会导致裸卖 call/put 的卖出单。
- 早盘宽跨 `morning_strangle` 的单腿止盈拆成：
  - `strangle_long_leg_take_profit_pct`
  - `strangle_short_leg_take_profit_pct`
- 长腿/短腿按 `call_strikes_otm` 和 `put_strikes_otm` 比较；只影响“单腿止盈”，不影响组合止盈、单腿止损、组合止损和强平时间。

支持的 QQQ 策略变体包括：

- `reaction_zone`
- `morning_strangle`
- `morning_directional`
- `gamma_scalping`
- `gamma_pro`

### 股票自动交易

- 股票 worker 支持 owner/account 显式传递。
- 支持信号、确认、研究快照、策略矩阵、ML 矩阵、近期指标与 SLA 状态。
- 支持 worker 重启恢复。
- 已加入原有持仓保护：自动交易只应接管自己创建并记录的仓位，不应把账户中已有的人工持仓当成可自动卖出的仓位。

### Agent Strategy Lab

Agent Strategy Lab 是当前推荐的 QQQ 参数研究入口。它的边界是“研究与审批”，不是实盘执行。

已实现：

- 页面：`/agent-strategy-lab`
- API：`/agent-strategy-lab/*`
- 支持 QQQ 0DTE / 1DTE。
- 支持策略下拉：
  - `morning_strangle`
  - `morning_directional`
- 支持研究维度：
  - `risk_controls`
  - `time_window`
  - `combined`
- 支持异步 Lab run，避免 60/120/180 天 1m 回测导致页面请求超时。
- 支持“下载缺失 K 线缓存”，调用 `/backtest/kline-cache/fetch`。
- 读取实盘配置、决策日志、执行 ledger、行情/K 线缓存，先做数据质量检查。
- 智能体/候选生成层只输出候选参数，不直接改实盘配置。
- 确定性验证层对候选进行 60/120/180 天窗口验证。
- 审批写入时只 merge `strategy_config_patch`，避免把无关策略默认字段写进 `live_worker_config.json`。
- 审批前 diff 只展示候选实际改动字段，避免把无关字段误认为已验证。
- 支持审批记录、中文摘要、北京时间短格式展示和回滚。

当前规则链路：

```text
智能体研究层
  -> 生成候选参数 / 交易日判断 / 风控解释
确定性回测与验证层
  -> 过滤不合格候选
人工确认 / 自动审批闸门
  -> 写入 live_worker_config 草稿
QQQ 实盘 worker
  -> 只按确定性规则执行
券商 API 下单
```

### License、支付与权限

- 本地 License 缓存在 `data/auth/local_licenses.json`。
- 后端付费接口无有效 License 时返回 `403`，错误码通常是 `plan_required`。
- 新 License 优先使用 RSA-PSS-SHA256 签名：
  - 发行端私钥：`CONVEX_LOCAL_LICENSE_PRIVATE_KEY_PEM` 或 `LOCAL_LICENSE_PRIVATE_KEY_PEM`
  - 本地后端公钥：`LOCAL_LICENSE_PUBLIC_KEY_PEM` 或 `CONVEX_LOCAL_LICENSE_PUBLIC_KEY_PEM`
  - 也支持 `LOCAL_LICENSE_PUBLIC_KEY_PATH` 指向本地公钥文件。
- 旧 HMAC 变量仍保留为兼容旧 License 的迁移入口；新部署应使用 RSA。
- Billing 已拆成 payment provider 架构：
  - `manual_qr`：当前可用，静态二维码半自动确认。
  - `wechat_native`：预留微信 Native。
  - `alipay_qr`：预留支付宝二维码。
  - `aggregate_qr`：预留聚合支付。

用户版只保留 License 导入与公钥验签；管理员版才保留 License 签发、订单确认和私钥配置。版本边界见 [用户版与管理员版](docs/editions.md)。

### 回测、K 线缓存与研究

- 通用异步回测 API：`/backtests`。
- QQQ 策略回测 API：`/strategy/qqq-0dte/backtest`。
- QQQ 矩阵 API：`/strategy/qqq-0dte/matrix`。
- K 线缓存：
  - `POST /backtest/kline-cache/fetch`
  - `GET /backtest/kline-cache/status`
  - `DELETE /backtest/kline-cache`
- 1m QQQ 60/120/180 天验证依赖服务端缓存；缺失时页面会提示下载缺失缓存。

### TradingAgents、MCP 与通知

- TradingAgents 页面和 API 已接入，适合金融多角色研究。
- MCP 推荐入口是 `mcp_server/broker_mcp_server.py`。
- 支持飞书通知和指令机器人配置。
- 支持公共行情 fallback，包括 Polygon、TwelveData、Tencent、AkShare、Yahoo、Stooq、本地缓存等。

## 快速启动

Windows PowerShell：

```powershell
cd D:\github\multi-trading

py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -r requirements-dev.txt

cd frontend
npm install
cd ..

.\scripts\start-api.ps1
.\scripts\start-frontend.ps1
```

也可以直接启动：

```powershell
python scripts/run_api.py --port 8010
cd frontend
npm run dev
```

`frontend/package.json` 的 `dev` 脚本已经固定 `-p 3010`。

## 本地配置

首次运行前：

```powershell
Copy-Item .env.example .env
Copy-Item frontend\.env.local.example frontend\.env.local
```

常用配置位置：

- 根目录 `.env`：兼容和全局占位。
- `frontend/.env.local`：Next.js 本地开发变量。
- `data/user_env/<owner>.env`：Web 设置页实际写入的 owner 级密钥。
- `data/auth/local_licenses.json`：本地 License 缓存。
- `data/qqq_0dte/live_worker_config.json`：QQQ 0DTE 实盘配置。
- `data/qqq_1dte/live_worker_config.json`：QQQ 1DTE 实盘配置。

不要提交真实密钥、真实 License、实盘 ledger、决策日志、PID、运行日志或 `.secrets`。

## 实盘安全

实盘前至少确认：

- 当前登录 owner/account 是否正确。
- License 是否有效，`plan_required` 不是 worker 故障，而是权限不足或 License 缺失。
- `TRADE_KILL_SWITCH`、`TRADE_DRY_RUN`、L3 confirmation token 是否符合预期。
- QQQ worker 状态区里的 quote/bars 来源是否正常。
- 决策日志是否持续更新。
- 断连恢复区是否显示“本次启动已恢复”或明确显示未恢复原因。
- 账户里原有人工持仓不要交给 worker 管理，除非已有明确 ledger 归属。

## 目录说明

| 路径 | 说明 |
| --- | --- |
| `api/` | FastAPI 本地后端、路由、worker、服务层 |
| `frontend/` | Next.js 控制台、Convex 云端函数、页面 |
| `mcp_server/` | MCP、QQQ 策略引擎、回测、通知 |
| `scripts/` | 启动、诊断、本地验证脚本 |
| `tests/` | 后端、策略、License、Agent Lab 测试 |
| `docs/` | 部署、安全、功能说明 |
| `data/` | 本地运行数据；仓库只保留示例和占位 |

## 更多文档

- [当前功能说明](docs/current-features.md)
- [用户版与管理员版](docs/editions.md)
- [启动脚本说明](scripts/README.md)
- [云端控制台与 License 部署](docs/cloud-console-deployment.md)
- [工程健康检查](docs/engineering-health.md)
- [安全说明](SECURITY.md)
