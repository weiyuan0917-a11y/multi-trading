# 用户版与管理员版

本项目按“单一私有主仓库，两个发布版本”组织。

## 版本边界

| 能力 | 用户版 `user` | 管理员版 `admin` |
| --- | --- | --- |
| 本地设置、券商、行情、LLM、飞书 | 包含 | 包含 |
| QQQ 0DTE / 1DTE 自动交易 | 包含 | 可包含，用于内部测试 |
| 股票自动交易 | 包含 | 可包含，用于内部测试 |
| Agent Strategy Lab | 包含 | 包含 |
| License 导入与公钥验签 | 包含 | 包含 |
| License 签发、续期、撤销 | 不包含 | 包含 |
| 收款订单管理 | 不包含 | 包含 |
| Convex billing / admin actions 源码 | 不包含 | 包含 |
| License 私钥 | 不配置 | 仅管理员部署配置 |

## 运行时开关

前端 edition 由环境变量控制：

```env
NEXT_PUBLIC_MT_EDITION=user
```

管理员控制台使用：

```env
NEXT_PUBLIC_MT_EDITION=admin
```

`user` 是默认值。用户版运行时会隐藏 `/admin/orders` 与 `/admin/licenses` 导航，并对 `/admin/*`、`/api/admin/*` 返回 404。

这个开关只是防误开。真正发给用户的包应使用发布脚本物理删除管理员源码。

## 发布包生成

从仓库根目录执行：

```powershell
.\scripts\create-release.ps1 -Edition user
.\scripts\create-release.ps1 -Edition admin
```

默认输出：

```text
dist\releases\multi-trading-user
dist\releases\multi-trading-admin
```

用户版发布脚本会删除：

- `frontend/app/admin`
- `frontend/app/api/admin`
- `frontend/app/api/billing`
- `frontend/convex`
- `frontend/.convex`
- `.secrets`
- `.env`、`frontend/.env.local`
- `data/user_env`
- `data/auth`
- `data/accounts`
- logs、PID、ledger、tail、K 线缓存等运行数据

管理员版发布包仍不复制真实密钥、运行日志、ledger 或本机账户文件，但保留管理员源码，供你部署到私有 Vercel/Convex/后台环境。

用户版 `/billing` 页面不会在本机创建收款订单。它应配置云端购买入口：

```env
NEXT_PUBLIC_BILLING_PORTAL_URL=https://your-cloud-console.example.com/billing
```

如果你已经有公开的云端下单 API，也可以配置：

```env
NEXT_PUBLIC_BILLING_ORDER_API_URL=https://your-cloud-console.example.com/api/billing/manual-orders
```

## 密钥规则

用户版只应配置公钥：

```env
LOCAL_LICENSE_PUBLIC_KEY_PEM=
```

管理员版/发行端才配置私钥：

```env
CONVEX_LOCAL_LICENSE_PRIVATE_KEY_PEM=
```

不要把私钥、`MT_BILLING_WEBHOOK_SECRET`、真实订单数据或 License 投递记录放进用户发布包。
