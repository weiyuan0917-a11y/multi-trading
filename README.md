<p align="center">
  <img src="assets/windows/multitrading-logo-source.png" alt="MultiTrading Logo" width="180">
</p>

# MultiTrading Community

[下载 Pro 版本 Setup.exe / Download the Pro Setup.exe](https://github.com/weiyuan0917-a11y/multi-trading/releases/tag/V1.0.206)

MultiTrading Community 是 MultiTrading 的公开研究版。它面向股票与期权研究、策略实验、历史回测和模拟交易学习，所有执行能力都限制在本地 Paper 或 Rehearsal 模式。仓库不包含券商连接、券商凭证、真实账户接口、真实下单/撤单、商业授权服务或客户安装器构建链路。

Pro 版本是单独交付的闭源客户端。上面的 Release 提供 Pro 客户端 `Setup.exe`，支持 Windows 10 及以上版本直接一键下载安装。Pro 安装包不是由本 Community 源码构建，也不随本仓库发布。

## 功能模块

### 股票与行情研究

- 使用统一的股票标的格式进行行情快照查询。
- 提供可重复的离线合成行情，便于开发、测试和示例运行；返回结果明确标记为延迟研究数据。
- 研究代码与 UI 均不读取账户余额、真实持仓或券商密钥。

### 期权研究

- 生成期权链研究示例，包括 Call/Put、行权价、到期天数和隐含波动率。
- 提供 Delta、Gamma、Theta、Vega 等 Greeks 字段，用于定价理解、敏感度分析和策略比较。
- 研究结果是估算值，不是可交易报价，也不构成投资建议。

### 策略与历史回测

- 接收历史价格序列，运行可重复的基础策略回测。
- 输出期初资金、期末权益、收益率和交易次数，方便比较参数或策略版本。
- 回测假设明确写入结果：不计滑点、税费和真实市场冲击，适合研究验证而非收益承诺。

### 自动交易研究与模拟执行

- 自动交易模块保留信号生成、策略参数实验和风险计算能力。
- 通过 `ExecutionIntent` 统一描述标的、资产类别、方向、数量、参考价格、策略和执行模式。
- `PaperBroker` 在内存中维护模拟现金、仓位、订单和成交记录，支持买入、卖出、资金校验和持仓均价计算。
- 仅接受 `paper` 与 `rehearsal` 两种模式；不会连接任何外部交易通道，也没有真实订单适配器接口。

### Community MCP

`mcp_server/community_mcp_server.py` 提供研究型 MCP 工具：

- `market_quote`：股票研究行情快照。
- `option_research`：期权链和 Greeks 研究数据。
- `research_backtest`：历史价格序列回测。
- `create_execution_intent`：生成非执行性的订单意图。
- `paper_submit_order`：提交本地模拟订单并生成模拟成交。
- `paper_account`：查询本地模拟资金、仓位和订单记录。

Community MCP 不注册账户连接、凭证读取、真实持仓查询、真实下单/撤单、实盘 Worker 或付费授权工具。

## API 接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/health` | 查看服务状态、版本和允许的执行模式 |
| `GET` | `/market/quote/{symbol}` | 查询股票研究行情快照 |
| `GET` | `/research/options/{symbol}` | 查询期权链与 Greeks 研究示例 |
| `POST` | `/research/backtest` | 运行价格序列回测 |
| `POST` | `/research/execution-intents` | 生成 `ExecutionIntent`，不执行订单 |
| `POST` | `/paper/orders` | 提交 Paper/Rehearsal 模拟订单 |
| `GET` | `/paper/account` | 查询模拟账户、仓位和成交 |

## Community 与 Pro 对比

| 能力 | Community（本仓库） | Pro（独立闭源客户端） |
| --- | --- | --- |
| 股票研究 | 公开研究接口、离线合成行情 | 按授权提供完整客户端能力和数据连接 |
| 期权研究 | 期权链、Greeks、定价与策略实验 | 在商业客户端中提供扩展的数据和工作流 |
| 回测 | 本地历史价格回测和结果摘要 | 商业版扩展能力以交付版本为准 |
| 自动交易 | 信号、风控、`ExecutionIntent` | 按授权提供完整交易工作流 |
| 执行模式 | 仅 Paper / Rehearsal，本地模拟成交 | 由授权、风控和部署策略决定，可包含真实券商工作流 |
| 账户与订单 | 仅内存模拟账户 | 闭源客户端中的账户、订单和连接能力 |
| MCP | 研究、回测、订单意图和 Paper 模拟 | 私有实现可按授权提供交易相关工具 |
| 安装方式 | 从源码运行，不含安装包构建链路 | Windows 10+ `Setup.exe` 一键安装 |
| 授权与商业模块 | 不包含支付、许可证和客户管理 | 由 Pro 交付、授权和服务体系负责 |

Community 源码可以用于学习、研究和二次开发；修改 Community 代码不会获得 Pro 的私有凭证、授权服务或官方执行基础设施。

## 本地运行

```powershell
python -m pip install -r requirements.txt
./scripts/start-api.ps1 -Dev

cd frontend
npm install
npm run dev
```

API 默认地址为 `http://127.0.0.1:8010`，本地 UI 默认地址为 `http://127.0.0.1:3010`。

启动研究型 MCP：

```json
{
  "mcpServers": {
    "multitrading-community": {
      "command": "python",
      "args": ["mcp_server/community_mcp_server.py"],
      "env": {"PYTHONPATH": "D:\\path\\to\\multi-trading"}
    }
  }
}
```

## 发布前检查

```powershell
./scripts/check-community-boundary.ps1 -Strict
python -m pytest -q tests
```

边界规则和迁移说明见 [Community 收紧清单](docs/community-hardening-checklist.md) 与 [Community / Pro 边界](docs/community-pro-boundary.md)。检查脚本不扫描 Git 历史，也不会替代历史密钥轮换和安全审计。

## 许可证与联系

Copyright 2026 Multi-Trading contributors. Community 源码以 [Apache License 2.0](LICENSE) 发布。MultiTrading 名称、Logo 和视觉识别不作为商标授权，详见 [NOTICE](NOTICE)、[TRADEMARKS.md](TRADEMARKS.md) 和 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

联系作者 / Contact: QQ `178522360`

---

## English

MultiTrading Community is the public research edition of MultiTrading. It is built for stock and options research, strategy experiments, historical backtests, and simulated trading. Every execution path is limited to local Paper or Rehearsal mode. This repository contains no broker connection, broker credential, live-account endpoint, real order/cancel implementation, commercial licensing service, or customer installer build chain.

The Pro edition is delivered as a separate closed-source client. The Release link above provides the Pro `Setup.exe`, which supports Windows 10 and later and can be installed with a one-click setup flow. The installer is not built from this Community repository and is not included here.

### Modules

**Stock and market research**

- Query normalized stock symbols through a small research API.
- Use deterministic offline synthetic quotes for development, tests, and examples; responses are marked as delayed research data.
- Research code and the UI never read account balances, live positions, or broker secrets.

**Options research**

- Generate option-chain examples with calls and puts, strikes, expiry days, and implied volatility.
- Inspect Delta, Gamma, Theta, and Vega for pricing intuition, sensitivity analysis, and strategy comparison.
- Results are estimates for research, not tradable quotes or investment advice.

**Strategies and backtests**

- Run a repeatable baseline backtest over a historical price list.
- Report starting cash, ending equity, return percentage, and trade count for strategy and parameter comparisons.
- Assumptions are explicit: no slippage, taxes, or real-market impact are modeled.

**Automated-trading research and simulation**

- Keep signal generation, strategy experiments, and risk calculations available for research.
- Use `ExecutionIntent` to describe a symbol, asset class, side, quantity, reference price, strategy, and execution mode.
- `PaperBroker` keeps simulated cash, positions, orders, and fills in memory, including buy/sell validation and average-cost updates.
- Only `paper` and `rehearsal` modes are accepted. There is no external trading channel or live-order adapter.

**Community MCP**

`mcp_server/community_mcp_server.py` exposes research-only tools: `market_quote`, `option_research`, `research_backtest`, `create_execution_intent`, `paper_submit_order`, and `paper_account`.

It does not register account connections, credential reads, live position queries, real order/cancel tools, live workers, or paid licensing tools.

### Community vs. Pro

| Capability | Community (this repository) | Pro (separate closed-source client) |
| --- | --- | --- |
| Stock research | Public APIs and offline synthetic quotes | Full client capabilities and data connections under a license |
| Options research | Chains, Greeks, pricing, and strategy experiments | Extended data and workflows in the commercial client |
| Backtesting | Local historical-price backtests and summaries | Commercial extensions depend on the delivered build |
| Automated trading | Signals, risk calculations, and `ExecutionIntent` | Complete trading workflow under the applicable authorization |
| Execution | Paper / Rehearsal only, local simulated fills | Controlled by authorization, risk controls, and deployment policy; may include live broker workflows |
| Accounts and orders | In-memory simulation only | Closed-source account, order, and connection features |
| MCP | Research, backtest, intents, and Paper simulation | Private tools may add trading capabilities under license |
| Installation | Run from source; no installer build chain | One-click `Setup.exe` for Windows 10 and later |
| Commercial services | No billing, licensing, or customer management | Managed by the Pro delivery and licensing system |

Community code is intended for learning, research, and independent development. Changing this repository does not grant access to private Pro credentials, licensing services, or official execution infrastructure.

### Run locally

```powershell
python -m pip install -r requirements.txt
./scripts/start-api.ps1 -Dev

cd frontend
npm install
npm run dev
```

The API runs at `http://127.0.0.1:8010` and the local UI at `http://127.0.0.1:3010` by default. Use the MCP configuration shown above to start the research-only server.

### Release checks

```powershell
./scripts/check-community-boundary.ps1 -Strict
python -m pytest -q tests
```

See [Community hardening checklist](docs/community-hardening-checklist.md) and [Community / Pro boundary](docs/community-pro-boundary.md) for the publication rules. The checker does not scan Git history or replace credential rotation and security review.

### License and contact

Copyright 2026 Multi-Trading contributors. Community source is released under the [Apache License 2.0](LICENSE). The MultiTrading name, logo, and visual identity are not licensed as trademarks. See [NOTICE](NOTICE), [TRADEMARKS.md](TRADEMARKS.md), and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Contact: QQ `178522360`
