# 启动脚本（小生产 / 开发）

仓库根目录下执行（Windows 用 PowerShell）：

| 场景 | API | 前端 |
|------|-----|------|
| **小生产（默认）** | `.\scripts\start-api.ps1` 或 `./scripts/start-api.sh` | `.\scripts\start-frontend.ps1` 或 `./scripts/start-frontend.sh` |
| **本地开发** | `.\scripts\start-api.ps1 -Dev` 或 `./scripts/start-api.sh --dev` | 同上 |
| **管理员前端** | 同上 | `.\scripts\start-frontend.ps1 -Edition admin` 或 `./scripts/start-frontend.sh --edition admin` |

- **单一来源**：`api.main:app`、各环境绑定地址由仓库根目录 **`backend_uvicorn_spec.py`** 定义；`launcher.py` 与 **`scripts/run_api.py`** 均通过 `build_uvicorn_argv()` 拼装参数，避免多处硬编码。
- 也可直接：`python scripts/run_api.py`（小生产）、`python scripts/run_api.py --dev`、`python scripts/run_api.py --host 0.0.0.0 --port 8010`。
- API 默认绑定 **127.0.0.1:8010**（小生产）；开发模式为 **0.0.0.0:8010** 且带 **`--reload`**。
- 前端默认是 **user** edition；管理员控制台需显式设置 **admin** edition，否则 `/admin/*` 与 `/api/admin/*` 返回 404。
- 日志目录：`logs/`，按启动时间生成 `api-*.log` / `frontend-*.log`（`logs/*.log` 已加入 `.gitignore`）。
- 自定义端口（仅 shell 示例）：`PORT=8080 ./scripts/start-api.sh`。
- PowerShell 自定义绑定：`.\scripts\start-api.ps1 -BindHost 0.0.0.0`（仍无 reload，适合局域网访问小生产）。

飞书机器人等独立进程若手动启动，可自行重定向，例如：

`python -u mcp_server/feishu_command_bot.py 2>&1 | Tee-Object -FilePath logs\feishu-bot.log -Append`

## 发布包

生成用户版和管理员版发布包：

```powershell
.\scripts\create-release.ps1 -Edition user
.\scripts\create-release.ps1 -Edition admin
```

用户版会物理排除 `frontend/app/admin`、`frontend/app/api/admin`、`frontend/app/api/billing` 和 `frontend/convex`。管理员版保留控制面源码，但仍排除真实密钥、日志和运行数据。

## MCP 服务入口

当前仓库推荐只启用一个 MCP 入口：

- 推荐入口：`mcp_server/broker_mcp_server.py`
- 兼容实现：`mcp_server/longport_mcp_server.py`（由推荐入口复用）

配置文件（`mcp_server/mcp_config.json`、`.cursor/mcp.json`）默认只保留：

- `broker-trading`

推荐稳定性设置：

- `OPENCLAW_MCP_TOOL_COMPAT=standard`
- `OPENCLAW_MCP_START_BACKGROUND_ON_TOOL_CALL=false`
- `OPENCLAW_MCP_SINGLE_INSTANCE=false`
- `OPENCLAW_MCP_TOOL_TIMEOUT_SECONDS=120`

排查命令：

```powershell
python diagnose_mcp.py
```
