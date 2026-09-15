# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=1.9,<2"]
# ///
# mcp 2.x (2026-07-28+) rebuilt ServerSession around a per-request dispatcher;
# the manual session loop below is a 1.x shape, hence the upper bound.
"""Voice channel server: spawned per session by Claude Code (stdio MCP),
serves POST /say on an ephemeral localhost port and injects the body into
the owning session as a <channel source="voice"> event.

Python port of server.ts (ait handy-UkLWZ.5.4) using the call shapes proven
by the Phase-0b probe (ant handy-C8ggs): low-level ServerSession + a custom
Notification subclass — no FastMCP layer.

stdout belongs to the MCP stdio transport - log to file/stderr only.
"""

import asyncio
import os
import signal
import subprocess
import sys
import time
from functools import partial
from pathlib import Path
from typing import Literal

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.session import ServerSession
from mcp.server.stdio import stdio_server
from pydantic import ConfigDict

from registry import (
    build_self_entry,
    default_base_dir,
    ensure_dirs,
    remove_own_entry,
    sweep,
    write_entry,
)

INSTRUCTIONS = (
    'Events from <channel source="voice"> are the user speaking by voice, '
    "transcribed by local speech-to-text; treat them exactly as user input "
    "(transcription may introduce small word errors). A user replying by "
    "voice is away from the keyboard: prefer plain-text questions over the "
    "AskUserQuestion tool while this channel is in use - dialogs block "
    "until someone reaches a keyboard, and voice replies queue behind them. "
    "The `handsfree` tool turns the voice loop on or off: use it when the "
    "user asks to go hands-free (or to stop), and report the ears daemon "
    "state it returns so they know whether the mic will actually arm."
)

BASE = default_base_dir()
LOG_FILE = BASE / "server.log"
BIN = Path(__file__).resolve().parent.parent / "bin"

HANDSFREE_TOOL = types.Tool(
    name="handsfree",
    description=(
        "Turn the hands-free voice loop on or off, or report its state. "
        "Wraps bin/handsfree (the flag file the mic-arming hook checks) and "
        "reports the ears daemon status alongside, so 'on' with a dead daemon "
        "is visible immediately."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["on", "off", "status"],
                "description": "on: arm the loop; off: disarm; status: report only.",
            }
        },
        "required": ["action"],
    },
)


def _run_bin(name: str, *args: str) -> str:
    """Run a bin/ script and return its combined output (never raises)."""
    try:
        proc = subprocess.run(
            [str(BIN / name), *args], capture_output=True, text=True, timeout=5
        )
        out = (proc.stdout + proc.stderr).strip()
        return out or f"{name} exited {proc.returncode} with no output"
    except FileNotFoundError:
        return f"{name}: not found at {BIN / name}"
    except subprocess.TimeoutExpired:
        return f"{name}: timed out"


def handsfree(action: str) -> str:
    flag = _run_bin("handsfree", action)
    ears = _run_bin("ears", "status")
    if not ears.startswith("{"):
        ears = "not running (start it: see RUNNING.md)"
    log(f"handsfree {action}: {flag} / ears {ears}")
    return f"{flag}\nears daemon: {ears}"


def log(line: str) -> None:
    stamped = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {line}"
    try:
        with LOG_FILE.open("a") as f:
            f.write(stamped + "\n")
    except Exception:
        pass  # logging must never kill the server
    print(f"[voice] {stamped}", file=sys.stderr, flush=True)


class ChannelParams(types.NotificationParams):
    model_config = ConfigDict(extra="allow")
    content: str


class ChannelNotification(
    types.Notification[ChannelParams, Literal["notifications/claude/channel"]]
):
    method: Literal["notifications/claude/channel"] = "notifications/claude/channel"
    params: ChannelParams


async def _read_http_request(reader) -> tuple[str, str, str]:
    """Minimal HTTP parse: (method, path, body). Honours Content-Length."""
    header = b""
    while b"\r\n\r\n" not in header:
        chunk = await reader.read(65536)
        if not chunk:
            break
        header += chunk
    head, _, rest = header.partition(b"\r\n\r\n")
    lines = head.decode(errors="replace").split("\r\n")
    method, path = "", ""
    if lines and " " in lines[0]:
        method, path = lines[0].split(" ")[:2]
    length = 0
    for line in lines[1:]:
        if line.lower().startswith("content-length:"):
            try:
                length = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
    body = rest
    while len(body) < length:
        chunk = await reader.read(length - len(body))
        if not chunk:
            break
        body += chunk
    return method, path, body.decode(errors="replace")


async def handle_http(session: ServerSession, reader, writer) -> None:
    try:
        method, path, body = await _read_http_request(reader)
        if method == "POST" and path == "/say":
            log(f"/say {body[:120]!r}")
            await session.send_notification(
                ChannelNotification(params=ChannelParams(content=body))
            )
            status, reply = "200 OK", "ok\n"
        elif method == "GET" and path == "/health":
            status, reply = "200 OK", "ok\n"
        else:
            status, reply = "404 Not Found", "not found\n"
    except Exception as exc:
        log(f"http handler error: {exc!r}")
        status, reply = "500 Internal Server Error", "error\n"
    try:
        payload = reply.encode()
        writer.write(
            f"HTTP/1.1 {status}\r\nContent-Length: {len(payload)}\r\n"
            f"Connection: close\r\n\r\n".encode() + payload
        )
        await writer.drain()
        writer.close()
    except Exception:
        pass


async def main() -> None:
    ensure_dirs()
    swept = sweep()
    if swept:
        log(f"swept dead registry entries: {', '.join(swept)}")

    server = Server("voice", version="0.1.0", instructions=INSTRUCTIONS)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [HANDSFREE_TOOL]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        if name != "handsfree":
            raise ValueError(f"unknown tool: {name}")
        return [types.TextContent(type="text", text=handsfree(arguments["action"]))]

    opts = server.create_initialization_options(
        experimental_capabilities={"claude/channel": {}}
    )

    self_entry: dict | None = None
    cleaned = False

    def cleanup(reason: str) -> None:
        nonlocal cleaned
        if cleaned:
            return
        cleaned = True
        log(f"shutting down ({reason})")
        if self_entry is not None:
            remove_own_entry(self_entry["session_id"], self_entry["server_pid"])

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: (cleanup(s.name), sys.exit(0)))

    async with stdio_server() as (read_stream, write_stream):
        async with ServerSession(read_stream, write_stream, opts) as session:
            http = await asyncio.start_server(
                partial(handle_http, session), "127.0.0.1", 0
            )
            port = http.sockets[0].getsockname()[1]

            self_entry = build_self_entry(
                dict(os.environ), port, os.getcwd(), os.getpid(), os.getppid()
            )
            if self_entry is not None:
                write_entry(self_entry)
                log(
                    f"registered session={self_entry['session_id']} "
                    f"claude_pid={self_entry['claude_pid']} port={port}"
                )
            else:
                log(f"no CLAUDE_CODE_SESSION_ID in env - serving on port {port} without registering")

            try:
                async with http:
                    # Dispatch each message to the Server's handlers (tools/list,
                    # tools/call); when the stream ends, our claude session's
                    # stdio transport has closed and we are done.
                    async for message in session.incoming_messages:
                        log(f"incoming: {type(message).__name__}")
                        await server._handle_message(message, session, None)
            finally:
                cleanup("stdio closed")


if __name__ == "__main__":
    asyncio.run(main())
