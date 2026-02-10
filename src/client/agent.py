from __future__ import annotations

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent


async def create_mcp_agent(
    access_token: str,
    server_url: str,
    model_name: str = "gpt-4o",
) -> tuple:
    """Create a LangGraph ReAct agent backed by MCP tools from the patterns server."""
    client = MultiServerMCPClient(
        {
            "patterns_server": {
                "transport": "streamable_http",
                "url": server_url,
                "headers": {"Authorization": f"Bearer {access_token}"},
            },
        }
    )

    tools = await client.get_tools()
    model = ChatOpenAI(model=model_name, temperature=0)
    agent = create_react_agent(model, tools)

    return agent, client
