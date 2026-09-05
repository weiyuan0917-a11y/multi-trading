# Community 收紧清单

本清单用于把 GitHub 仓库收紧为可独立运行的 Community 源码，而把现有
`MultiTradingSetup-<version>.exe` 作为私有 Pro 客户端交付。它是迁移清单，
不是删除脚本；每一项迁移后都应运行
`./scripts/check-community-boundary.ps1 -Strict`。

## 发布目标

Community 只能研究、回测、生成订单意图与执行模拟成交。它不包含真实券商
凭证、交易适配器、真实下单/撤单入口、Pro 授权、支付或客户安装包构建链路。

Pro 保留闭源 Setup、签名授权和实盘执行。Community 的任何分支即使修改自身
代码，也不能取得 Pro 的券商凭证、私有授权或官方执行服务能力。

## 保留在 Community

- 股票与期权的公共行情、历史 K 线、期权链、Greeks、筛选、策略研究、
  回测、模拟定价、指标、报告与审计。
- 自动交易的信号生成、风控计算、`ExecutionIntent` 数据协议。
- `PaperExecutionProvider`、本地模拟资金/仓位/订单/手续费模型。
- 独立的 Community MCP：只开放 L1/L2 的行情、股票与期权研究、回测、
  策略/风控计算、订单意图与模拟交易工具；不得要求或读取券商凭证。
- 不含真实密钥的示例配置、Apache-2.0 许可和第三方声明。
- 前端的研究、回测、模拟交易和使用文档。

## Community MCP 边界

Community 应新增 `mcp_server/community_mcp_server.py`，或以等价的独立入口
提供下列能力：

- L1：公开行情、历史 K 线、股票/期权研究数据、期权链、Greeks、新闻与已保存研究结果。
- L2：股票/期权回测、策略筛选、参数实验、风险计算、生成 `ExecutionIntent`、
  Paper 订单与模拟成交查询。
- 禁止：券商账户连接、凭证读取、真实持仓/订单查询、真实下单/撤单、L3 确认令牌、
  实盘 Worker 启停和付费授权管理。

Community MCP 的工具清单必须在测试中断言不包含任何真实执行工具；Pro MCP
才可实现真实券商工具，并在有效许可证与执行授权之后调用。

## 改造成模拟能力

| 当前区域 | Community 目标 | 完成条件 |
| --- | --- | --- |
| `api/auto_trader.py`、QQQ 与 Swing Worker | 信号和模拟仓位保留 | 不导入券商上下文，不调用真实订单 API |
| `api/services/trade_safety.py` | 迁为 Paper/Rehearsal 的单一执行入口 | 只接受本地模拟执行器；删除“拦截后仍保留真实适配器”的设计 |
| `frontend/app/trade`、`frontend/app/options` | 替换为 `paper-trade` / 模拟持仓界面 | 不显示账户凭证、真实委托或撤单控件 |
| `api/main.py`、`api/runtime_bridge.py` | 拆出 Community 路由 | 不注册 `/trade/*`、`/options/*` 的真实交易写接口 |

## 迁入私有 Pro 仓库

| 区域 | 原因 |
| --- | --- |
| `api/brokers/` | 真实券商 SDK、上下文及订单写操作 |
| `api/services/account_registry.py`、`broker_context.py`、`broker_client_service.py`、`credential_vault.py`、`trade_permissions.py` | 账户、凭证、交易权限和连接生命周期 |
| `api/routers/options_trade.py`、`api/routers/setup.py`、`api/routers/license.py` | 真实交易、券商 Setup 和产品授权接口 |
| `mcp_server/broker_mcp_server.py`、`longport_mcp_server.py`、`options_service.py` | 真实券商 MCP、真实账户查询与期权订单服务；Community 以独立研究型 MCP 替代 |
| `frontend/app/setup`、`trade`、`options`、`billing`、`admin` 及 `frontend/convex/` 的商业模块 | 凭证输入、交易控制、支付和授权管理 |
| `launcher_customer_go/`、`launcher_customer_native/`、`installer_customer_go/`、`*.spec`、客户构建脚本 | Pro 客户端和 Setup 交付链路 |

## 从 Community 删除

- `scripts/build_customer_installer.ps1`、`build_customer_installer.bat` 及所有发布/安装器产物。
- 客户端 EXE、ISS、`dist/`、`build/`、支付二维码、许可证私钥与客户许可证。
- 所有真实券商环境变量说明，例如 `LONGPORT_APP_SECRET`、`FUTU_OPEND_*`。
- 所有 L3 实盘确认、实盘 Worker 启停、订单和账户凭证文档。

## 实施顺序

1. 在私有 Pro 仓库保留当前可构建的客户 Setup 和全部实盘资产。
2. 为 Community 新增 Paper 账户、Paper 订单、Paper 成交和 `ExecutionIntent` 协议。
3. 新增研究型 Community MCP，并为股票/期权研究、回测和模拟工具建立工具清单测试。
4. 将自动交易 Worker 改为只调用 Paper 执行器；用模拟仓位做回归测试。
5. 移除或替换所有本清单列出的 Pro 路径与 UI 页面。
6. 改写 `.env.example`、README、MCP 文档和启动说明，只保留 Community 能运行的内容。
7. 执行边界检查和 Community 测试；检查通过后才发布 GitHub Release。

## 发布门禁

```powershell
./scripts/check-community-boundary.ps1
./scripts/check-community-boundary.ps1 -Strict
```

普通模式只输出报告；`-Strict` 在仍发现 Pro 路径或实盘标识时返回非零退出码，
适合放入 GitHub Actions 或发布前 CI。该脚本不扫描 Git 历史，也不删除文件；
历史中曾出现过的密钥仍需独立轮换和清理。
