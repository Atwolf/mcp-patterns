from __future__ import annotations

import logging
import os
import sys

from dotenv import load_dotenv
from fastmcp import FastMCP

from server.lifespan import app_lifespan
from server.resources import register_resources
from server.tools import register_tools

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

mcp = FastMCP(
    name="MCP Patterns Server",
    lifespan=app_lifespan,
)

register_tools(mcp)
register_resources(mcp)


def main() -> None:
    port = int(os.environ.get("MCP_SERVER_PORT", "8001"))
    mcp.run(transport="streamable-http", port=port)


if __name__ == "__main__":
    main()
