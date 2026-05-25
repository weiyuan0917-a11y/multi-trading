# 当前功能说明

更新时间：2026-05-20

本文按当前代码实现记录功能边界，重点说明哪些能力已经接入、哪些能力只是预留。

## 运行架构

```text
Next.js 控制台 :3010
  -> 本地 FastAPI Agent :8010
     -> LongPort/券商 API
     -> 公共行情 fallback
     -> 本地配置、K 线缓存、worker ledger
     -> QQQ / 股票自动交易 worker

Convex / Cloud Console
  -> 账号、订阅、订单、License 签发
  -> Local Agent 通过签名 License 离线校验权限
```

项目采用 local-first 设计：交易、券商密钥、行情密钥、worker、回测和本地数据都在用户机器上运行；云端只承担账号、订阅、订单、License 签发等控制面能力。

## 页面与模块

| 模块 | 前端页面 | 后端入口 | 状态 |
| --- | --- | --- | --- |
| 设置 / 账户 | `/setup` | `/setup/*` | 已实现 |
| 本地登录 / API key | `/auth` | `/auth/*` | 已实现 |
| QQQ 0DTE 实盘 | `/auto-trading/options-0dte` | `/auto-trading/options-0dte/*`、`/strategy/qqq-0dte/*` | 已实现 |
| QQQ 1DTE 实盘 | `/auto-trading/options-1dte` | `/auto-trading/options-1dte/*`、`/strategy/qqq-1dte/*` | 已实现 |
| 股票自动交易 | `/auto-trading/stocks` | `/auto-trader/*`、`/auto-trading/stocks/*` | 已实现 |
| Agent Strategy Lab | `/agent-strategy-lab` | `/agent-strategy-lab/*` | 已实现 |
| 通用回测 | `/backtest` | `/backtests/*`、`/backtest/*` | 已实现 |
| 期权链 / 下单 | `/options`、`/trade` | `/options/*`、`/trade/*` | 已实现 |
| TradingAgents | `/tradingagents` | 主 API research/tradingagents 路由 | 已实现 |
| Billing | `/billing` | Convex billing actions | `manual_qr` 已实现，其他 provider 预留 |
| License 管理 | `/admin/licenses` | `/license/local`、Convex license delivery | 已实现 |
| 订单管理 | `/admin/orders` | Convex manual order actions | 已实现 |

`/admin/licenses`、`/admin/orders`、`/api/admin/*`、本地 billing server proxy 与 `frontend/convex` 属于管理员版，不应进入普通用户发布包。用户版发布包通过 `scripts/create-release.ps1 -Edition user` 生成，会物理移除这些源码。

## Owner / Account 隔离

当前代码按 owner 与 account 区分本地运行上下文：

- Web 设置页写入 `data/user_env/<owner>.env`。
- 同一 owner 可以注册多个券商 account。
- worker 启动时携带 owner/account。
- 内部行情、持仓、订单和下单 API 接收 owner/account，用于路由到正确券商账户。
- 股票、QQQ 0DTE、QQQ 1DTE worker 都应使用自己的 owner/account 上下文。

这避免了 `davies`、`davies1983`、`davies0811` 等不同 owner 之间共享券商密钥、行情 API、飞书或 LLM 配置的问题。

## 权限与 License

本地后端的权限来源是有效 License，而不是浏览器可伪造的 header：

- 不信任 `X-MT-Cloud-Plan`。
- 不信任 `X-MT-Cloud-Role`。
- 不信任 `X-MT-Cloud-Is-Admin`。
- 无有效付费 License 访问付费接口返回 `403`，错误码通常是 `plan_required`。

签名机制：

- 新 License 使用 RSA-PSS-SHA256。
- 发行端私钥变量：
  - `CONVEX_LOCAL_LICENSE_PRIVATE_KEY_PEM`
  - `LOCAL_LICENSE_PRIVATE_KEY_PEM`
  - 兼容别名：`CONVEX_LOCAL_LICENSE_RSA_PRIVATE_KEY_PEM`、`LOCAL_LICENSE_RSA_PRIVATE_KEY_PEM`
- 本地后端公钥变量：
  - `LOCAL_LICENSE_PUBLIC_KEY_PEM`
  - `CONVEX_LOCAL_LICENSE_PUBLIC_KEY_PEM`
  - 兼容别名：`LOCAL_LICENSE_RSA_PUBLIC_KEY_PEM`、`CONVEX_LOCAL_LICENSE_RSA_PUBLIC_KEY_PEM`
  - 或 `LOCAL_LICENSE_PUBLIC_KEY_PATH` / `CONVEX_LOCAL_LICENSE_PUBLIC_KEY_PATH`
- 旧 HMAC 验证仍作为迁移兼容层存在；新部署不要再依赖 HMAC。

相关测试在 `tests/test_local_license_security.py`，覆盖签名有效、篡改失败、伪造权限 header 不生效、无 License 403、有 License 200 等场景。

## QQQ 期权 worker

QQQ 0DTE 与 1DTE 共用一套核心策略引擎和 worker 能力，差异主要是合约到期日偏移：

- 0DTE 默认当日到期。
- 1DTE 默认 `expiry_offset_days = 1`。

已实现能力：

- 启停 worker。
- 保存/重载 live worker 配置。
- 从前端表单同步策略参数。
- 行情 quote 与 1m bars 来源展示。
- 决策日志 tail 展示。
- runtime 状态、last_action、bars_today、last_bar 展示。
- 断连/重启恢复展示。
- 执行 ledger 与状态快照。
- 手动平仓/外部持仓 reconcile 保护。
- L3 confirmation token 与风控校验。

关键本地文件：

- `data/qqq_0dte/live_worker_config.json`
- `data/qqq_0dte/live_worker_decision_tail.jsonl`
- `data/qqq_0dte/live_worker_execution_ledger.jsonl`
- `data/qqq_1dte/live_worker_config.json`
- `data/qqq_1dte/live_worker_decision_tail.jsonl`
- `data/qqq_1dte/live_worker_execution_ledger.jsonl`

仓库只保留 example 和 `.gitkeep`，真实运行文件不应提交。

## QQQ 策略

已接入策略变体：

- `reaction_zone`：反应区 + 成交量单边。
- `morning_strangle`：早盘宽跨，双买 Call + Put。
- `morning_directional`：早盘方向单，涨跌幅阈值触发单腿。
- `gamma_scalping`：开盘突破/回归剥头皮。
- `gamma_pro`：更完整的 gamma 策略组合。

早盘宽跨的当前特殊逻辑：

- 单腿止盈拆成长腿/短腿两个阈值。
- 长腿是 `call_strikes_otm` 与 `put_strikes_otm` 中 OTM 步长更大的腿。
- 短腿是 OTM 步长更小的腿。
- 两边步长相同，则两条腿都按短腿止盈。
- 只影响单腿止盈。
- 不影响组合止盈、单腿止损、组合止损、强平时间。
- 某一腿先止盈后，另一腿仍继续受组合止盈、组合止损和强平规则约束。

## 股票自动交易 worker

股票自动交易已经具备：

- owner/account 启动上下文。
- 自动扫描、信号、人工确认、归档。
- 策略打分、强势股、配对回测。
- 研究快照、模型比较、策略矩阵、ML 矩阵。
- 近期指标、SLA 状态。
- worker 重启恢复。
- worker-owned 持仓保护，避免误处理账户原有人工持仓。

相关入口：

- 页面：`/auto-trading/stocks`
- API：`/auto-trader/*`
- worker：`api/auto_trader_worker.py`

## Agent Strategy Lab

Agent Strategy Lab 是 QQQ 参数研究与审批模块。它不直接下单，也不直接启动实盘 worker。

数据层读取：

- LongPort/券商行情。
- QQQ 1m 分时。
- VIX、成交量、前日高低、期权报价。
- `live_worker_decision_tail.jsonl`。
- `live_worker_execution_ledger.jsonl`。
- 当前 `live_worker_config.json`。

支持标的：

- QQQ 0DTE。
- QQQ 1DTE。

支持策略：

- `morning_strangle`。
- `morning_directional`。

支持研究维度：

- `risk_controls`：重点验证止盈、止损、spread、步长等风控参数。
- `time_window`：重点验证开仓窗口和强平时间。
- `combined`：同时验证时间、风控和步长。

验证层：

- 生成候选参数。
- 对候选做 60/120/180 天窗口回测。
- 检查 closed trades、平均收益、最大连亏、单日最大亏损等门槛。
- 结果中保留中文摘要、风险说明、验证指标和审批状态。

审批层：

- 审批前只展示候选实际 patch。
- 写入时只 merge `strategy_config_patch`。
- 尽量保留原 live config。
- 支持审批记录与回滚。

## K 线缓存与异步任务

为避免大窗口 1m 回测超时，当前实现把重计算放入异步任务：

- Agent Lab：`/agent-strategy-lab/tasks`
- 通用回测：`/backtests`

K 线缓存 API：

- `POST /backtest/kline-cache/fetch`
- `GET /backtest/kline-cache/status`
- `DELETE /backtest/kline-cache`

当页面显示 `kline_server_cache_miss` 时，表示服务端没有对应组合的 K 线缓存，需要先下载缺失缓存。

## 支付 provider 架构

当前 billing 已经从单一二维码逻辑拆成 provider 架构：

| Provider | 状态 | 说明 |
| --- | --- | --- |
| `manual_qr` | 可用 | 静态二维码半自动确认，管理员确认到账后签发 License |
| `wechat_native` | 预留 | 后续接微信 Native 下单与回调 |
| `alipay_qr` | 预留 | 后续接支付宝当面付/二维码回调 |
| `aggregate_qr` | 预留 | 后续接聚合支付统一二维码与回调 |

前端和 Convex 共用 provider 枚举，未实现的 provider 会保持 disabled/planned 状态。

## 安全边界

实盘交易安全目前依赖多层保护：

- 本地 License 权限。
- owner/account 上下文。
- L3 confirmation token。
- kill switch / dry run。
- worker-owned ledger。
- broker 持仓 reconcile。
- 手动平仓保护。
- 断连恢复快照。
- 决策日志和执行 ledger。

仍需人工注意：

- 不要在错误 owner/account 下启动 worker。
- 不要把同一账户同时交给多个不同 worker 管理同一标的。
- 不要把旧 ledger 复制到另一个账户。
- 不要在未确认 `TRADE_DRY_RUN` 状态时做实盘测试。

## 推荐验证命令

后端基础验证：

```powershell
cd D:\github\multi-trading
python -m compileall -q api mcp_server tests launcher.py runtime_process_utils.py backend_uvicorn_spec.py
pytest tests/test_local_license_security.py
pytest tests/test_agent_strategy_lab_service.py
pytest tests/test_qqq_0dte_strategy.py
pytest tests/test_qqq_worker_option_sell_guard.py
```

前端类型检查：

```powershell
cd D:\github\multi-trading\frontend
npm run lint
```

启动脚本说明见 `scripts/README.md`。
