# Community / Pro Boundary

The repository root is the Community edition. It ships research, backtests,
strategy experiments, `ExecutionIntent`, Paper execution and a research-only
MCP server. It deliberately contains no broker SDK, broker adapter, credential
store, real-account endpoint, subscription system, customer installer or real
order/cancel implementation.

Community accepts only `paper` and `rehearsal` execution modes. Its in-memory
Paper broker has no external adapter interface. A fork may change its own code,
but it cannot acquire the private Pro credentials, license service or official
execution infrastructure from this repository.

Keep Pro in a separate access-controlled repository or deployment. Pro may use
the public research and intent contracts, but must own regulated execution,
credential lifecycle, customer authorization, operational approval and broker
integration.

Run `./scripts/check-community-boundary.ps1 -Strict` before each Community
release. The script is a release gate, not a replacement for rotating any
credential that may previously have existed in Git history.
