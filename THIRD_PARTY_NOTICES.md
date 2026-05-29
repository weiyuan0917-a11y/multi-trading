# Third-Party Notices

Multi-Trading depends on and optionally integrates with third-party software,
services, SDKs, and data providers. This file is a best-effort summary for
attribution and compliance; package managers and upstream repositories remain
the source of truth for exact license terms.

## Key Upstream Projects

- **TradingAgents**  
  Repository: https://github.com/TauricResearch/TradingAgents  
  Usage: optional research and multi-agent analysis integration via
  `requirements-tradingagents.txt`.  
  License: the upstream GitHub repository currently identifies the project as
  Apache-2.0. Keep upstream license files and notices when redistributing
  TradingAgents or modified copies of it.

- **OpenBB**  
  Website/repository: https://openbb.co/ and https://github.com/OpenBB-finance/OpenBB  
  Usage: optional external research/data API integration. Multi-Trading does
  not vendor OpenBB source code in this repository.  
  License and terms: OpenBB Platform licensing has changed over time and may
  include AGPL and/or commercial terms. Review the current OpenBB license and
  service terms before bundling, modifying, hosting, or redistributing OpenBB.

- **Model Context Protocol**  
  Repository: https://github.com/modelcontextprotocol  
  Usage: MCP server/tool integration.

- **FastAPI / Starlette / Uvicorn**  
  Usage: Python API server.

- **Next.js / React / Tailwind CSS / ECharts**  
  Usage: web frontend and visualization.

- **Longbridge OpenAPI SDK**  
  Usage: broker API integration through a LongPort-compatible provider.

## Data and Service Terms

Financial data providers, broker APIs, LLM providers, notification platforms,
and market data vendors may impose independent API terms, rate limits,
redistribution limits, and regulatory obligations. Users and redistributors
must review and comply with those terms separately from this repository's
software license.

## No Endorsement

Names of third-party projects and companies are used only for attribution and
interoperability descriptions. They do not imply endorsement, sponsorship, or
official affiliation.
