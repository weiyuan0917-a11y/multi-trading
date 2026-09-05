# 安全说明

## 密钥与仓库

- Community 只读取公开行情提供商配置；不要提交 API 密钥、账户信息、Webhook、令牌或本地运行状态。
- 复制 `.env.example` 为本地 `.env` 后填写，提交前运行 `scripts/check-community-boundary.ps1 -Strict`。
- 根目录 `.gitignore` 已排除常见敏感路径；若曾误提交，请立即轮换密钥并清理 Git 历史。

## 网络暴露

- 默认服务仅用于本机研究和模拟交易；面向公网时请改为本机监听并使用反向代理（HTTPS、IP 限制或 VPN）。
- 生产环境建议收紧 FastAPI CORS 的 `allow_origins`。

## 报告漏洞

如你发现与本项目相关的安全问题，请通过私密渠道联系维护者（勿在公开 issue 中粘贴密钥或账户信息）。
