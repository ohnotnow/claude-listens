# claude-listens

**Work in progress / research preview.** The delivery path for a hands-free
Claude Code voice loop: you hear Claude's reply out loud, answer by just
talking, and your words land back in the right Claude Code session — no
keyboard, no window focus.

This repo is one of three, each with a strict role:

- **[claude-speaks](https://github.com/ohnotnow/claude-speaks)** — the mouth.
  Stop/Notification hook that reads Claude's replies aloud via TTS, and (with
  hands-free mode on) arms the microphone after speaking.
- **A [Handy](https://github.com/cjpais/Handy) fork** — the ears. Local
  speech-to-text; gains a single fork-local feature (VAD silence auto-stop) so
  a recording ends itself when you stop talking.
- **claude-listens** (this repo) — the postal service. A Claude Code
  [channel](https://code.claude.com/docs/en/channels-reference) server that
  injects transcripts into the owning session, plus the session registry and
  glue scripts.

## Components

- `src/server.ts` — MCP channel server, spawned per session by Claude Code.
  Binds an ephemeral localhost port; `POST /say` forwards plain text into the
  session as a `<channel source="voice">` event. Registers itself in the
  session registry.
- `src/registry.ts` — registry entries under `~/.claude-voice/registry/`,
  keyed by Claude Code session id, swept when the owning process dies.
- `bin/handy-reply` — Handy's "External Script" hook: consumes the one-shot
  reply target, cleans the transcript (strips a trailing "send send", drops
  empties), POSTs it to the right session.
- `bin/handsfree` — toggles the hands-free flag file (`on|off|toggle|status`);
  Raycast-ready for a hotkey.

## Shared files

Everything lives under `~/.claude-voice/` (created on demand, 0700): the
`handsfree` flag, `registry/<session-id>.json`, the one-shot
`reply-target.json`, and component logs.

## Using it

Channels are a Claude Code research preview: register the server in your
project's `.mcp.json` and launch with

```bash
claude --dangerously-load-development-channels server:voice
```

```json
{
  "mcpServers": {
    "voice": { "command": "bun", "args": ["/path/to/claude-listens/src/server.ts"] }
  }
}
```

Status: Phase 1 under construction — see the ait tracker in the Handy fork
for the plan. `probe/` holds the Phase-0 throwaway experiments that decided
the design.
