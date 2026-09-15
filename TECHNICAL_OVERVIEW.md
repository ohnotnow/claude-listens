# Technical Overview

Last updated: 2026-09-15

## What this is

A hands-free voice loop for Claude Code: Claude's reply is spoken aloud (by the
sibling project [claude-speaks](https://github.com/ohnotnow/claude-speaks)), the
mic arms, you answer by speaking, and the transcript lands in the *correct*
Claude Code session via the experimental "channels" feature. All speech
processing is local (no cloud STT).

## Stack

- Python ≥ 3.11, run with `uv`. No `pyproject.toml` — every entry point is a
  single-file script with PEP 723 inline metadata (`# /// script` blocks), so
  `uv run <file>` resolves its own dependencies.
- `mcp` ≥ 1.9, < 2 (Python MCP SDK, low-level API, deliberately not
  FastMCP). Pinned below 2.x: the 2026-07-28 SDK rebuilt `ServerSession`
  around a per-request dispatcher and `src/server.py`'s manual session loop
  no longer constructs (verified 2026-09-15 against mcp 2.2.0)
- `parakeet-mlx` ≥ 0.5.2 — speech-to-text on Apple MLX (Apple silicon only)
- `onnxruntime` + bundled silero VAD v4 model — silence detection
- `sounddevice` / `numpy` — audio capture
- POSIX sh / bash for the `bin/` glue; macOS-specific bits (`afplay`, MLX)
- `probe/` also has a Bun + TypeScript MCP SDK probe from the discovery phase

## Directory structure

```
setup.py             interactive once-ever setup: mics, model, daemon, MCP registration
src/server.py        MCP channel server, spawned per session by Claude Code
src/registry.py      session registry (~/.claude-voice/registry/), + tests
ears/earsd.py        resident STT daemon: record → VAD auto-stop → transcribe
ears/config.json     local knobs (gitignored; see config.example.json)
bin/ears             control CLI for the daemon (status|toggle|arm|cancel)
bin/handsfree        privacy flag file toggle — mic never arms without it
bin/handy-reply      transcript → correct session (consumes one-shot target)
probe/               throwaway phase-0 probes — archaeology, not maintained
RUNNING.md           day-to-day runbook, quirks, troubleshooting table
```

## The Claude Code channels preview — how we use it

This is the project's reason to exist, and the part worth stealing. "Channels"
let an MCP server inject events *into* a running Claude Code session, rather
than only answering tool calls. It is a research preview: flag, consent dialog
and API may all change under you.

### Enabling it

1. Register a perfectly ordinary stdio MCP server. `setup.py` does it at user
   scope, so every project gets it:

   ```bash
   claude mcp add --scope user voice -- uv run /path/to/claude-listens/src/server.py
   ```

   The per-project equivalent is an entry in that project's `.mcp.json`
   (not checked into this repo):

   ```json
   { "mcpServers": { "voice": { "command": "uv", "args": ["run", "/path/to/claude-listens/src/server.py"] } } }
   ```

2. Every launch needs the flag, naming the server: `claude
   --dangerously-load-development-channels server:voice`. A consent warning
   appears each time. Some organisation accounts have channels disabled — if
   the flag is rejected, that's why (personal account works).

### The wire contract (as discovered by the probes)

- The server declares an **experimental capability** in its initialize
  response: `{"claude/channel": {}}`. In the Python SDK that's
  `Server(...).create_initialization_options(experimental_capabilities={"claude/channel": {}})`
  (`src/server.py:201`); in the TypeScript SDK it's
  `capabilities: { experimental: { "claude/channel": {} } }`
  (`probe/ancestry-probe.ts:59`).
- To inject text, send a JSON-RPC **notification** with method
  `notifications/claude/channel` and params `{"content": "<text>"}`.
- The text arrives in the conversation as a `<channel source="voice">` event.
  (Server name in `.mcp.json` and the declared MCP server name are both
  "voice" here; we never isolated which one feeds `source=`.)
- The server's `instructions` string is the place to tell Claude how to treat
  the events — ours says "treat as user input, transcription may err, prefer
  plain-text questions over AskUserQuestion" (`src/server.py:43`).

### Python SDK specifics

FastMCP has no notification support, so `src/server.py` drops to the low-level
`Server` + `ServerSession`. `session.send_notification()` wants a typed
notification, hence the small `ChannelNotification` / `ChannelParams` pydantic
subclasses (`src/server.py:114`) with `method` pinned by a `Literal`. The TS SDK
needs none of that — `mcp.notification({method, params})` just works.

The server also exposes one ordinary MCP tool, `handsfree` (`on|off|status`),
so the user can say "go hands-free" instead of running `bin/handsfree` from
the project directory. It shells out to `bin/handsfree` and `bin/ears status`
rather than reimplementing them, and returns both results so a dead ears
daemon is visible at the moment you switch on. Because the session loop is
driven by hand (we need the `ServerSession` for `/say`), each incoming message
is passed to `Server._handle_message()` to reach the `list_tools` /
`call_tool` handlers; that is what `Server.run()` does internally in 1.x.

Two hard rules for any stdio MCP server: **stdout belongs to the transport**
(log to stderr/file only), and drain `session.incoming_messages` — when that
stream ends, the owning session has exited and you should clean up.

### Session identity and lifecycle (the earned lessons)

- Claude Code spawns the server with `CLAUDE_CODE_SESSION_ID` in its
  environment. We key the registry on it.
- **Claude Code spawns the server twice at session start** (observed
  2026-07-23). The first instance's shutdown must not delete the entry the
  second just wrote — hence `remove_own_entry()` only deletes when the stored
  `server_pid` matches (`src/registry.py:43`).
- **Resumed sessions have a split identity**: hook payloads keep the original
  conversation id, but the restarted process spawns its channel server with a
  *fresh* `CLAUDE_CODE_SESSION_ID`. claude-speaks bridges the two by process
  ancestry — the registry's `claude_pid` is an ancestor of the Stop hook
  process (`claude-speaks/handsfree.py:_lookup_by_ancestry`).
- Registry entries are swept on every server start: any entry whose
  `claude_pid` is no longer alive is deleted.
- `AskUserQuestion` dialogs block channel delivery — spoken replies queue
  until someone answers at the keyboard.

### The probes

`probe/` is the discovery record — throwaway scripts kept as archaeology, not
maintained code: `ancestry-probe.ts` (what env and ancestry does Claude Code
give an MCP server? does the TS SDK deliver channel events?),
`channel-probe.py` (can the Python SDK do the same?), and `ears-latency.py`
(is warm parakeet-mlx fast enough?). If a Claude Code release breaks the loop,
they're the smallest reproductions of the wire contract to start from — but
expect to dust them off, not just run them.

## The loop (data flow)

```
Claude turn ends ─ Stop hook (claude-speaks) speaks the reply aloud
   │  handsfree flag set? registry lookup: session id → port (ancestry fallback)
   ▼
write one-shot ~/.claude-voice/reply-target.json { port, session_id, armed_at }
   │  then run handsfree_arm_command
   ▼
bin/ears arm ──► earsd records (Tink) ── silero VAD: 2.5 s silence ends it (Pop)
   │  parakeet-mlx transcribes, hands text to reply_command as argv[1]
   ▼
bin/handy-reply: consume target, strip spoken "send send", POST to
127.0.0.1:<port>/say
   │
   ▼
src/server.py sends notifications/claude/channel
   └──► lands in the session as <channel source="voice">
```

The one-shot target file is the routing heart: bound at arm time, consumed on
first use, deleted regardless of outcome. Two concurrent channel sessions work
— replies go to whichever session armed the mic. `earsd`'s `arm` is idempotent
and returns `"armed"` only to the caller that actually started the recording,
so a second session can never claim an in-flight transcript.

## Shared files (`~/.claude-voice/`, mode 0700)

| Path | What |
| --- | --- |
| `handsfree` | flag file: loop is live (privacy gate) |
| `registry/<session-id>.json` | `session_id`, `claude_pid`, `server_pid`, `port`, `cwd`, `started_at`, `name` (reserved for Phase 2 named sessions) |
| `reply-target.json` | one-shot routing target, written at arm, consumed by handy-reply |
| `server.log`, `ears.log`, `handy-reply.log` | per-component logs — the troubleshooting table in RUNNING.md maps symptoms to these |

## HTTP endpoints (all 127.0.0.1 only)

| Server | Port | Endpoints |
| --- | --- | --- |
| `src/server.py` (per session) | ephemeral, recorded in registry | `POST /say` (inject body as channel event), `GET /health` |
| `ears/earsd.py` (one resident daemon) | 18790 (`control_port`) | `GET /status`, `POST /toggle`, `POST /arm`, `POST /cancel` |

## Where the logic lives

| Location | Purpose |
| --- | --- |
| `src/server.py` | channel capability, `handsfree` MCP tool, ephemeral-port HTTP listener, registry registration/cleanup |
| `src/registry.py` | registry contract, dead-session sweep, double-spawn guard |
| `ears/earsd.py` | model kept warm in one thread (MLX streams are per-thread!), VAD watchdog, mic preference list resolved per recording |
| `bin/handy-reply` | one-shot target consumption, transcript cleaning ("send send" end-marker) |
| `claude-speaks/handsfree.py` | (sibling repo) arm decision, playback wait, ancestry fallback |

## Testing

- Framework: pytest, run via `uv run --with pytest pytest src/registry_test.py -q`
- `src/registry_test.py` is a behaviour spec ported test-for-test from the
  original TypeScript version, plus the double-spawn guard cases. The sweep's
  liveness check is injectable so tests never poke real pids.
- `bin/handy-reply`'s header documents a three-command manual test using a
  throwaway Bun HTTP server.

## Local development

```bash
# the STT daemon, as a launchd agent (RunAtLoad + KeepAlive). Never run it as
# a child of a Claude session: it dies with the session.
bin/ears install         # bin/ears uninstall to remove; foreground: uv run ears/earsd.py

bin/ears status          # {"state": "idle"} when ready (~20-30 s cold start)
claude --dangerously-load-development-channels server:voice
bin/handsfree on         # and off again before you walk away
```

First daemon start downloads the parakeet model; afterwards it runs offline
(`HF_HUB_OFFLINE=1` unless `EARS_ONLINE` is set). Config knobs and the
symptom→log troubleshooting table live in RUNNING.md.
