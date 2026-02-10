from __future__ import annotations

import os

import chainlit as cl
from dotenv import load_dotenv

from client.agent import create_mcp_agent

load_dotenv()


@cl.oauth_callback
def oauth_callback(
    provider_id: str,
    token: str,
    raw_user_data: dict[str, str],
    default_user: cl.User,
) -> cl.User | None:
    # Store the OAuth access token so on_chat_start can relay it to the MCP server.
    # Fine-grained authorization is enforced server-side (defense in depth).
    default_user.metadata["oauth_token"] = token
    return default_user


@cl.on_chat_start
async def on_chat_start() -> None:
    user = cl.user_session.get("user")
    oauth_token: str | None = None

    if user and hasattr(user, "metadata"):
        oauth_token = user.metadata.get("oauth_token")

    if not oauth_token:
        # Fallback: check if a token was set directly in the session (e.g. dev mode)
        oauth_token = cl.user_session.get("oauth_token")

    if not oauth_token:
        await cl.Message(
            content="Authentication required. Please log in via the OAuth provider."
        ).send()
        return

    server_url = os.environ.get("MCP_SERVER_URL", "http://localhost:8001/mcp")

    try:
        agent, mcp_client = await create_mcp_agent(
            access_token=oauth_token,
            server_url=server_url,
        )
    except Exception as exc:
        await cl.Message(
            content=f"Failed to connect to MCP server: {exc}"
        ).send()
        return

    cl.user_session.set("agent", agent)
    cl.user_session.set("mcp_client", mcp_client)

    name = user.identifier if user else "user"
    await cl.Message(
        content=(
            f"Connected to MCP server. Welcome, {name}!\n\n"
            "I have access to the following tools:\n"
            "- **list_entities** — List cached entities (filtered by your entitlements)\n"
            "- **get_entity** — Retrieve a specific entity by ID\n"
            "- **refresh_cache** — Force a cache refresh (admin only)\n\n"
            "How can I help?"
        )
    ).send()


@cl.on_message
async def on_message(message: cl.Message) -> None:
    agent = cl.user_session.get("agent")
    if agent is None:
        await cl.Message(
            content="Session not initialized. Please refresh the page and log in."
        ).send()
        return

    msg = cl.Message(content="")
    await msg.send()

    try:
        response = await agent.ainvoke(
            {"messages": [{"role": "user", "content": message.content}]},
        )
        final_content = response["messages"][-1].content
        msg.content = final_content
        await msg.update()
    except Exception as exc:
        msg.content = f"Error: {exc}"
        await msg.update()
