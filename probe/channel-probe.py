# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=1.9"]
# ///
"""Phase-0b probe (ait handy-UkLWZ.4.2): can the Python MCP SDK serve a Claude Code channel?

Throwaway. Mirrors probe/ancestry-probe.ts: stdio MCP server declaring the
experimental claude/channel capability, plus a localhost HTTP listener that
forwards POSTed text into the session as notifications/claude/channel.

Register in .mcp.json as "voicepy", launch with
    claude --dangerously-load-development-channels server:voicepy
then:  curl -d "probe hello from python" localhost:18789
Log: ~/.claude-voice/probe-py.log
"""

import asyncio
import os
import time
from pathlib import Path
from typing import Literal

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.session import ServerSession
from mcp.server.stdio import stdio_server
from pydantic import ConfigDict

PORT = 18789
LOG = Path.home() / ".claude-voice" / "probe-py.log"
LOG.parent.mkdir(mode=0o700, exist_ok=True)


def log(msg: str) -> None:
    with LOG.open("a") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")


class ChannelParams(types.NotificationParams):
    model_config = ConfigDict(extra="allow")
    content: str


class ChannelNotification(
    types.Notification[ChannelParams, Literal["notifications/claude/channel"]]
):
    method: Literal["notifications/claude/channel"] = "notifications/claude/channel"
    params: ChannelParams


async def handle_http(session: ServerSession, reader, writer) -> None:
    raw = b""
    for _ in range(10):
        chunk = await reader.read(65536)
        raw += chunk
        if not chunk or b"\r\n\r\n" in raw:
            break
    body = raw.split(b"\r\n\r\n", 1)[1].decode(errors="replace").strip() if b"\r\n\r\n" in raw else ""
    text = body or "probe: empty body"
    try:
        await session.send_notification(ChannelNotification(params=ChannelParams(content=text)))
        log(f"notification written to transport: {text!r}")
        status, reply = b"200 OK", b"ok"
    except Exception as exc:
        log(f"send_notification FAILED: {exc!r}")
        status, reply = b"500 Internal Server Error", b"send failed"
    writer.write(
        b"HTTP/1.1 " + status + b"\r\nContent-Length: " + str(len(reply)).encode() + b"\r\nConnection: close\r\n\r\n" + reply
    )
    await writer.drain()
    writer.close()


async def main() -> None:
    opts = Server("voicepy-probe").create_initialization_options(
        experimental_capabilities={"claude/channel": {}}
    )
    log(f"starting pid={os.getpid()} ppid={os.getppid()} session={os.environ.get('CLAUDE_CODE_SESSION_ID')}")
    async with stdio_server() as (read_stream, write_stream):
        async with ServerSession(read_stream, write_stream, opts) as session:
            http = await asyncio.start_server(
                lambda r, w: handle_http(session, r, w), "127.0.0.1", PORT
            )
            log(f"http listening on {PORT}")
            async with http:
                async for message in session.incoming_messages:
                    # Probe drains everything; log what arrives so we learn
                    # what Claude Code actually sends a channel-only server.
                    log(f"incoming: {message!r}")


if __name__ == "__main__":
    asyncio.run(main())
