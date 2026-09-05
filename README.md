![MultiTrading Logo](assets/windows/multitrading-logo-source.png)

# MultiTrading Community

MultiTrading Community is the open research edition of MultiTrading. It keeps
stock and option research, synthetic option-chain/Greeks examples, backtests,
strategy signals, `ExecutionIntent`, local Paper orders, and a research-only
MCP service. It is intentionally unable to connect to a broker, read broker
credentials, query a real account, submit a real order, or cancel one.

The separately delivered Pro client is not contained in this repository.

## Community capabilities

- Stock/option research and deterministic offline quote examples.
- Option-chain, Greeks, pricing research and strategy experiments.
- Historical price-list backtests and performance summaries.
- `ExecutionIntent` generation plus local Paper account, positions and fills.
- MCP tools for quote research, option research, backtest, intents and Paper simulation.

The synthetic sample data is for development and research; it is not a real-time
market feed or investment advice.

## Run locally

```powershell
python -m pip install -r requirements.txt
./scripts/start-api.ps1 -Dev

cd frontend
npm install
npm run dev
```

The API is available at `http://127.0.0.1:8010`, and the local UI at
`http://127.0.0.1:3010`.

## API and Paper execution

- `GET /market/quote/{symbol}`: research quote snapshot.
- `GET /research/options/{symbol}`: research option-chain/Greeks example.
- `POST /research/backtest`: simple price-series backtest.
- `POST /research/execution-intents`: produce a non-executing intent.
- `POST /paper/orders` and `GET /paper/account`: local simulated fill and state.

`paper` and `rehearsal` are the only Community execution modes. Any need for
broker connectivity belongs in a separate, private Pro implementation.

## MCP

Configure the research-only server with:

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

Its tool list contains public research, backtest, `ExecutionIntent`, and Paper
simulation only. It does not register any broker or real-execution tool.

## Boundary checks

Before publishing, run:

```powershell
./scripts/check-community-boundary.ps1 -Strict
python -m pytest -q tests
```

The removal plan and the public/private boundary are in
[docs/community-hardening-checklist.md](docs/community-hardening-checklist.md)
and [docs/community-pro-boundary.md](docs/community-pro-boundary.md).

## Release

The latest separately delivered client installer is available from the
[V1.0.206 release](https://github.com/weiyuan0917-a11y/multi-trading/releases/tag/V1.0.206).
It is not built from this Community repository.

## License and contact

Copyright 2026 Multi-Trading contributors. Community source is released under
the [Apache License 2.0](LICENSE). The MultiTrading name, logo and visual
identity are not licensed as trademarks. See [NOTICE](NOTICE) and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Contact: QQ `178522360`.
