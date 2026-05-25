"""
Broker MCP Server compatibility entrypoint.

This is the neutral-name launcher for broker trading MCP.
It reuses the existing implementation in longport_mcp_server.py
to keep backward compatibility with current integrations.
"""
from __future__ import annotations

import asyncio

from longport_mcp_server import main


if __name__ == "__main__":
    asyncio.run(main())
