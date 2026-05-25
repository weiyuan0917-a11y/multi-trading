# MultiTrading Cloud Console Deployment

This document describes the target deployment split:

- **Vercel / Cloud Console**: Next.js UI, Clerk auth, Convex data, subscription and entitlement state.
- **MultiTrading Local Agent**: local FastAPI process on the user's machine for setup, research, broker accounts, trading, backtests, OpenBB, and TradingAgents.

The goal is to keep broker credentials and trading data local while allowing the web console to manage users and paid plans.

For mainland China users, the default development mode is now **hybrid local-first**:
the app starts with local account login and does not wait for Clerk/Convex on startup.
Cloud login can still be enabled explicitly for overseas/Vercel deployments.

## Architecture

```text
Browser
  |
  | Cloud console calls
  v
Vercel Next.js  ---> Clerk auth
       |          Convex user/subscription data
       |          Billing webhooks
       |
       | Local Agent calls from browser
       v
127.0.0.1:8010 MultiTrading Local Agent
       |
       | Local only
       v
Broker APIs / OpenBB / TradingAgents / local files
```

Current frontend API split:

- `frontend/lib/cloud-api.ts`: auth, user profile, subscription, billing, entitlements.
- `frontend/lib/local-agent-api.ts`: setup, research, trade, broker, market data, backtest.
- `frontend/lib/api.ts`: legacy compatibility layer. New code should not import it.

## Environment Files

Use two different environment locations:

- `frontend/.env.local`: local Next.js development.
- Vercel Project Environment Variables: cloud deployment.
- root `.env` or `data/user_env/<username>.env`: Local Agent secrets on the user's machine.

Do not put broker credentials, OpenBB tokens, worker API keys, or local user data in Vercel.

## Frontend Variables

Copy `frontend/.env.local.example` to `frontend/.env.local` for local development.

Required during local development:

```env
NEXT_PUBLIC_LOCAL_AGENT_API_BASE=http://127.0.0.1:8010
NEXT_PUBLIC_API_BASE=http://127.0.0.1:8010
NEXT_PUBLIC_AUTH_MODE=hybrid
NEXT_PUBLIC_MT_PLAN=free
```

Auth modes:

- `hybrid`: local-first. Clerk/Convex can be configured, but they do not block startup.
- `local`: local account only.
- `clerk`: cloud-first Clerk login.

Optional cloud-to-local owner binding:

```env
NEXT_PUBLIC_LOCAL_AGENT_OWNER_ID=davies
NEXT_PUBLIC_LOCAL_AGENT_OWNER_EMAIL=weiyuan0917@gmail.com
NEXT_PUBLIC_LOCAL_AGENT_OWNER_PLAN=premium
NEXT_PUBLIC_LOCAL_AGENT_OWNER_ROLE=admin
NEXT_PUBLIC_LOCAL_AGENT_OWNER_IS_ADMIN=true
```

Use this only when a Clerk user should continue reading an existing Local Agent owner. With the example above, Local Agent requests include `X-MT-Local-Owner: davies` only after the signed-in Clerk email matches `weiyuan0917@gmail.com`, and the frontend treats that session as Premium/Admin until cloud entitlements are wired.

Cloud console placeholders:

```env
NEXT_PUBLIC_CLOUD_API_BASE=
NEXT_PUBLIC_CONSOLE_API_BASE=
NEXT_PUBLIC_CLOUD_API_ALLOW_LOCAL_FALLBACK=false
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=
CLERK_SECRET_KEY=
CLERK_WEBHOOK_SIGNING_SECRET=
NEXT_PUBLIC_CLERK_SIGN_IN_URL=/auth
NEXT_PUBLIC_CLERK_SIGN_UP_URL=/auth
NEXT_PUBLIC_CLERK_SIGN_IN_FALLBACK_REDIRECT_URL=/onboarding
NEXT_PUBLIC_CLERK_SIGN_UP_FALLBACK_REDIRECT_URL=/onboarding
NEXT_PUBLIC_CONVEX_URL=
NEXT_PUBLIC_CONVEX_HTTP_ACTIONS_URL=
CLERK_JWT_ISSUER_DOMAIN=
CONVEX_BOOTSTRAP_ADMIN_EMAIL=
CONVEX_BOOTSTRAP_LOCAL_OWNER_ID=
```

`NEXT_PUBLIC_API_BASE` is kept only for legacy compatibility. Prefer:

- `NEXT_PUBLIC_LOCAL_AGENT_API_BASE` for Local Agent calls.
- `NEXT_PUBLIC_CLOUD_API_BASE` or `NEXT_PUBLIC_CONSOLE_API_BASE` for cloud control-plane calls.

## Vercel Variables

Set these in Vercel for Production, Preview, and Development as needed:

```env
NEXT_PUBLIC_LOCAL_AGENT_API_BASE=http://127.0.0.1:8010
NEXT_PUBLIC_CLOUD_API_BASE=https://<your-cloud-api-or-console-domain>
NEXT_PUBLIC_CLOUD_API_ALLOW_LOCAL_FALLBACK=false
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_...
CLERK_SECRET_KEY=sk_...
CLERK_WEBHOOK_SIGNING_SECRET=whsec_...
NEXT_PUBLIC_CONVEX_URL=https://<your-convex-deployment>.convex.cloud
NEXT_PUBLIC_CONVEX_HTTP_ACTIONS_URL=https://<your-convex-deployment>.convex.site
NEXT_PUBLIC_MT_PLAN=free
```

For billing, choose one provider and keep unused variables empty. Example for Stripe:

```env
NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY=pk_...
STRIPE_SECRET_KEY=sk_...
STRIPE_WEBHOOK_SECRET=whsec_...
```

Security notes:

- Never expose `CLERK_SECRET_KEY`, `STRIPE_SECRET_KEY`, or webhook secrets through `NEXT_PUBLIC_*`.
- Keep `NEXT_PUBLIC_CLOUD_API_ALLOW_LOCAL_FALLBACK=false` in production.
- Do not configure broker credentials in Vercel.

## Local Agent Variables

These stay on the user's machine:

```env
LONGPORT_APP_KEY=
LONGPORT_APP_SECRET=
LONGPORT_ACCESS_TOKEN=
BROKER_PROVIDER=longbridge
DEFAULT_ACCOUNT_ID=default

OPENBB_ENABLED=true
OPENBB_BASE_URL=http://127.0.0.1:6900

AUTO_TRADER_WORKER_USE_API_PROXY=true
AUTO_TRADER_API_BASE_URL=http://127.0.0.1:8010

LOCAL_AGENT_OWNER_ID=davies
LOCAL_AGENT_ALLOWED_OWNERS=davies
LOCAL_AGENT_ALLOW_USER_OWNERS=true
```

If Vercel frontend calls a Local Agent running on the user's browser machine, the browser requests `http://127.0.0.1:8010` from that user's device. This keeps trading traffic local, but the Local Agent must allow the Vercel origin in CORS.

Example:

```env
CORS_ALLOW_ORIGINS=http://127.0.0.1:3010,http://localhost:3010,https://your-app.vercel.app
```

## Subscription Plans

Current product tiers:

- **Free**: research, backtest, TradingAgents, OpenBB; no live auto trading.
- **Pro**: Free + stock auto trading.
- **Premium**: Pro + option auto trading + multi-broker + multi-account.

Frontend entitlement keys are defined in `frontend/lib/entitlements.ts`.

For frontend-only display during early local development, use:

```env
NEXT_PUBLIC_MT_PLAN=free
```

You may temporarily set:

```env
NEXT_PUBLIC_MT_PLAN=premium
```

This only affects frontend gating. The Local Agent's paid API access is enforced by a valid signed local License. For real cloud billing, plan and role should come from cloud auth/session data and signed License issuance, not from local storage or public env overrides.

## Local License Cache

The Local Agent has a signed local license cache for China-friendly startup:

- `GET /license/local`: read the current local user's cached license.
- `POST /license/local/preview`: verify and preview a License before import.
- `PUT /license/local`: import a license for the current local owner.
- `DELETE /license/local`: clear the cached license.

The cache is stored under `data/auth/local_licenses.json`. Backend entitlement checks merge a valid local license with the local user/API key identity, so the backend can enforce Pro/Premium access without calling Clerk on every startup.

New deployments should use RSA-PSS-SHA256 signatures. The cloud/issuer side signs with a private key; the Local Agent verifies with the public key.

Issuer / Convex environment:

```env
CONVEX_LOCAL_LICENSE_PRIVATE_KEY_PEM=
LOCAL_LICENSE_PRIVATE_KEY_PEM=
CONVEX_LOCAL_LICENSE_TTL_DAYS=7
```

Local Agent environment:

```env
LOCAL_LICENSE_PUBLIC_KEY_PEM=
CONVEX_LOCAL_LICENSE_PUBLIC_KEY_PEM=
# Or point to a public key file:
LOCAL_LICENSE_PUBLIC_KEY_PATH=
LOCAL_LICENSE_ALLOW_UNSIGNED=false
```

Compatibility variables for older HMAC Licenses still exist:

```env
LOCAL_LICENSE_SIGNING_SECRET=
CONVEX_LOCAL_LICENSE_SIGNING_SECRET=
```

Use them only during migration from old License payloads. Unsigned import should only be enabled on a trusted development machine.

## Email License Delivery MVP

Mainland-friendly activation should not require every customer to log in through Clerk / Convex. After payment succeeds, the cloud side can issue a signed License and send it to the customer's email.

Convex HTTP action:

- `POST /billing/license-webhook`
- Required header: `X-MT-Webhook-Secret: <MT_BILLING_WEBHOOK_SECRET>`
- Body can be a normalized payment payload, or a provider payload with equivalent `metadata`.

Minimal normalized body:

```json
{
  "email": "customer@example.com",
  "owner_id": "local_owner_id",
  "plan": "pro",
  "status": "active",
  "provider": "manual",
  "provider_event_id": "evt_unique_id",
  "current_period_end": 1790000000
}
```

Convex server-side environment variables:

```env
MT_BILLING_WEBHOOK_SECRET=
RESEND_API_KEY=
LICENSE_EMAIL_FROM=MultiTrading <license@example.com>
LICENSE_EMAIL_REPLY_TO=
LICENSE_EMAIL_SUBJECT_PREFIX=MultiTrading
CONVEX_LOCAL_LICENSE_PRIVATE_KEY_PEM=
CONVEX_LOCAL_LICENSE_TTL_DAYS=7
```

If `RESEND_API_KEY` or `LICENSE_EMAIL_FROM` is empty, the webhook still records and signs the License, but marks email delivery as `skipped`. This is useful for testing the billing flow before enabling real email delivery.

Admin page:

- Local URL: `http://127.0.0.1:3010/admin/licenses`
- The page requires a local Admin session. The browser calls the Next.js route below; the secret is only read on the server.
- Next.js server route: `/api/admin/license-deliveries`
- Convex HTTP actions:
  - `POST /billing/license-webhook`: issue and email one License.
  - `GET /billing/license-deliveries`: list recent License deliveries.

Next.js environment variables for the admin page:

```env
NEXT_PUBLIC_CONVEX_SITE_URL=http://127.0.0.1:3211
MT_BILLING_WEBHOOK_SECRET=
```

For local testing, `MT_BILLING_WEBHOOK_SECRET` in `frontend/.env.local` must match the same variable in Convex. In production, set this in Vercel and Convex with the same strong random value.

## Semi-Manual QR Payment MVP

For mainland-friendly payments, the app also supports a semi-manual scan-code flow:

1. Customer opens `/billing`.
2. Customer chooses Pro / Premium and monthly / yearly billing.
3. Customer enters email and local `owner_id`.
4. The page creates a pending order and shows WeChat / Alipay QR payment instructions.
5. Customer pays with the order number as payment remark.
6. Admin opens `/admin/orders`, verifies the payment in WeChat / Alipay, then clicks "confirm payment and issue License".
7. Convex signs the License and sends it via Resend email.

Billing is implemented behind a payment provider abstraction:

| Provider | Status | Notes |
| --- | --- | --- |
| `manual_qr` | available | Static QR code, admin manually confirms payment and issues License. |
| `wechat_native` | planned | Reserved for WeChat Native payment orders and callbacks. |
| `alipay_qr` | planned | Reserved for Alipay QR / face-to-face payment callbacks. |
| `aggregate_qr` | planned | Reserved for aggregate payment providers. |

Prices currently encoded in `frontend/convex/billing.ts`:

- Pro monthly: CNY 99
- Pro yearly: CNY 999
- Premium monthly: CNY 199
- Premium yearly: CNY 1999

Payment QR code configuration:

```env
NEXT_PUBLIC_PAYMENT_QR_WECHAT_URL=
NEXT_PUBLIC_PAYMENT_QR_ALIPAY_URL=
```

If these are empty, place the images at:

```text
frontend/public/payments/wechat-qr.png
frontend/public/payments/alipay-qr.jpg
```

Admin order API:

- Next.js route: `/api/admin/manual-orders`
- Convex HTTP actions:
  - `POST /billing/manual-orders`: create pending order.
  - `GET /billing/manual-orders`: list orders.
  - `POST /billing/manual-order-admin`: confirm/cancel orders.

## Deployment Steps

1. Prepare the repository.
   - Confirm `npm.cmd run lint` passes in `frontend`.
   - Confirm `npm.cmd run build` passes in `frontend`.
   - Confirm broker credentials are not committed.

2. Create the Vercel project.
   - Root directory: `frontend`.
   - Build command: `npm run build`.
   - Install command: `npm install`.
   - Output: default Next.js output.

3. Configure Vercel environment variables.
   - Add the frontend variables listed above.
   - Keep local broker and worker secrets out of Vercel.

4. Configure Clerk.
   - Create a Clerk application.
   - Add `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` and `CLERK_SECRET_KEY`.
   - Add a webhook secret when syncing users/plans to Convex or your cloud API.

5. Configure Convex.
   - Create a Convex deployment.
   - Add `NEXT_PUBLIC_CONVEX_URL`.
   - Add `NEXT_PUBLIC_CONVEX_HTTP_ACTIONS_URL` if HTTP actions are used later.
   - Add `CLERK_JWT_ISSUER_DOMAIN` for Convex Clerk auth.
   - Add `CONVEX_BOOTSTRAP_ADMIN_EMAIL` and `CONVEX_BOOTSTRAP_LOCAL_OWNER_ID` for the first local owner bootstrap.
   - Store server-only secrets in Convex or Vercel environment variables, depending on where the webhook/API handler runs.

6. Start the Local Agent on a user machine.
   - Run the backend locally on `127.0.0.1:8010`.
   - Add the deployed Vercel URL to `CORS_ALLOW_ORIGINS`.
   - Verify `GET /health` works locally.

7. Verify the split.
   - Login and entitlement calls should use `cloud-api`.
   - Setup, research, trade, broker, and backtest calls should use `local-agent-api`.
   - Browser devtools should show cloud calls going to the cloud base URL and Local Agent calls going to `127.0.0.1:8010`.

## Clerk Minimal Login Skeleton

The frontend now supports guarded cloud login modes:

- `NEXT_PUBLIC_AUTH_MODE=hybrid` or `local`: the app uses the local `/auth/login` flow and never waits for Clerk on startup.
- `NEXT_PUBLIC_AUTH_MODE=clerk`: the app wraps the UI with `ClerkProvider` and `/auth` renders Clerk sign-in/sign-up widgets.
- `CLERK_SECRET_KEY` is server-only. Keep it in `frontend/.env.local` for local development and in Vercel environment variables for deployment.
- Local Agent calls still go to `NEXT_PUBLIC_LOCAL_AGENT_API_BASE`; Clerk does not receive broker credentials.
- To bind a Clerk account to existing local data and permissions, set `NEXT_PUBLIC_LOCAL_AGENT_OWNER_ID`, `NEXT_PUBLIC_LOCAL_AGENT_OWNER_EMAIL`, `NEXT_PUBLIC_LOCAL_AGENT_OWNER_PLAN`, `NEXT_PUBLIC_LOCAL_AGENT_OWNER_ROLE`, and `NEXT_PUBLIC_LOCAL_AGENT_OWNER_IS_ADMIN` in the frontend, and allow that owner with `LOCAL_AGENT_ALLOWED_OWNERS` in the Local Agent environment.
- New Clerk users without an active `localOwnerBindings` record, or without completed onboarding, are routed to `/onboarding` instead of `/setup`.
- `/onboarding` starts with a required username step. That username becomes the Local Agent `owner_id`; broker keys, Setup config, history, and generated personal API keys are stored under that owner.
- Later steps cover broker API, LLM, Feishu, market API, notification/MCP settings, Research data sources, and personal API Key. Only the owner step is required for Free users. Pro / Premium / Admin users must confirm personal API Key setup before finishing.
- `LOCAL_AGENT_ALLOW_USER_OWNERS=true` lets a local user-created owner pass Local Agent owner validation. Set it to `false` and use `LOCAL_AGENT_ALLOWED_OWNERS` or a future pairing-code flow if you need a stricter deployment.

Relevant files:

- `frontend/lib/clerk-mode.ts`
- `frontend/app/layout.tsx`
- `frontend/app/auth/page.tsx`
- `frontend/app/onboarding/page.tsx`
- `frontend/proxy.ts`
- `frontend/components/clerk-auth-shell.tsx`
- `frontend/components/clerk-top-bar.tsx`

## Convex Minimal Data Layer

The first Convex skeleton stores cloud identity, subscription, entitlement, and local owner binding data without moving broker credentials off the user's machine.

Tables:

- `users`: Clerk user id, email, display name, role, onboarding completion, last seen timestamp.
- `subscriptions`: Free / Pro / Premium plan state and future billing provider ids.
- `entitlements`: resolved feature switches used by the UI.
- `localOwnerBindings`: maps a Clerk user to a Local Agent owner such as `davies`.

Functions:

- `users:upsertCurrentUser`: called by the frontend after Clerk login.
- `users:me`: returns the current cloud session, subscription, entitlements, and local owner binding.
- `users:selfBindLocalOwner`: lets the signed-in user bind a valid owner id during onboarding.
- `users:completeOnboarding`: marks onboarding as complete after required steps are done.
- `users:adminSetSubscription`: manual admin subscription override.
- `users:adminBindLocalOwner`: manual admin local owner binding.

Current development URLs:

```env
NEXT_PUBLIC_CONVEX_URL=https://compassionate-lemur-572.convex.cloud
NEXT_PUBLIC_CONVEX_HTTP_ACTIONS_URL=https://compassionate-lemur-572.convex.site
```

Bootstrap variables:

```env
CLERK_JWT_ISSUER_DOMAIN=https://<your-clerk-issuer-domain>
CONVEX_BOOTSTRAP_ADMIN_EMAIL=weiyuan0917@gmail.com
CONVEX_BOOTSTRAP_LOCAL_OWNER_ID=davies
```

After the Convex deployment is configured, run from `frontend`:

```powershell
npm.cmd run convex:dev
```

Then sign in with Clerk. The frontend will sync the user into Convex. If the email matches `CONVEX_BOOTSTRAP_ADMIN_EMAIL`, Convex stores the user as Premium/Admin and binds it to `CONVEX_BOOTSTRAP_LOCAL_OWNER_ID`.


## Expected Impact On Future Code Changes

This split is meant to reduce future churn:

- UI work can continue in Next.js without touching Local Agent internals.
- Broker adapters and research workers can continue locally.
- New subscription rules should be added to `frontend/lib/entitlements.ts` first, then backed by cloud data.
- New trading or research API calls should import from `frontend/lib/local-agent-api.ts`.
- New user, billing, organization, or plan calls should import from `frontend/lib/cloud-api.ts`.
