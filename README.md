# claude-listens

**Work in progress / research preview.** The hands-free voice loop for Claude
Code: you hear Claude's reply out loud, answer by just talking, and your words
land back in the right Claude Code session — no keyboard, no window focus.

It pairs with a sibling project,
[claude-speaks](https://github.com/ohnotnow/claude-speaks) — the mouth of the
operation. claude-speaks reads Claude's replies aloud (and is perfectly happy
on its own if all you want is to *hear* Claude); this repo owns everything
else: the ears, the session routing, and the glue. If you just want spoken
replies, install claude-speaks and stop there. If you want the full
conversation loop, you need both.

Everything is Python, run with [uv](https://docs.astral.sh/uv/) — no other
runtime. All speech processing is local (Parakeet via Apple MLX for
speech-to-text, silero VAD for end-of-speech detection); nothing you say
leaves your machine.

## How a reply travels

1. Claude finishes a turn; claude-speaks reads it aloud.
2. Playback ends; claude-speaks' hands-free hook writes a one-shot *reply
   target* (which session gets your answer) and arms the microphone via
   `bin/ears toggle`.
3. The ears daemon records from the first connected mic on your preference
   list, stops itself after ~2.5 s of trailing silence, transcribes locally,
   and hands the text to `bin/handy-reply` (named for the
   [Handy](https://github.com/cjpais/Handy) era this project grew out of).
4. That glue script consumes the reply target and POSTs the text to the
   owning session's channel server, which injects it as a
   `<channel source="voice">` event. Claude answers; Marvin speaks; the mic
   re-arms. Round and round, zero keypresses.

## Components

- `src/server.py` — MCP channel server, spawned per session by Claude Code.
  Declares the experimental `claude/channel` capability, binds an ephemeral
  localhost port; `POST /say` forwards plain text into the session as a
  `<channel source="voice">` event. Registers itself in the session registry.
- `src/registry.py` — registry entries under `~/.claude-voice/registry/`,
  keyed by Claude Code session id, recording the owning claude pid and port.
  Swept when the owning process dies. Behaviour spec: `src/registry_test.py`
  (`uv run --with pytest python -m pytest src/registry_test.py`).
- `ears/earsd.py` — the resident speech-to-text daemon. Keeps a
  [parakeet-mlx](https://github.com/senstella/parakeet-mlx) model warm
  (Apple Silicon), records on demand, and stops itself with a silero-VAD
  silence watchdog. Copy `ears/config.example.json` to `ears/config.json`
  and set your microphone preference list — the first *connected* match wins
  at each recording, so AirPods-while-washing-up beats the desk mic
  automatically.
- `bin/ears` — control CLI for the daemon (`status|toggle|arm|cancel`),
  Raycast-friendly.
- `bin/handsfree` — toggles the hands-free flag file (`on|off|toggle|status`).
  The mic never arms without this flag.
- `bin/handy-reply` — recorder-to-session glue: consumes the one-shot reply
  target, cleans the transcript (strips a trailing spoken "send send", drops
  empties), POSTs it to the right session.

## Shared files

Everything lives under `~/.claude-voice/` (created on demand, 0700): the
`handsfree` flag, `registry/<session-id>.json`, the one-shot
`reply-target.json`, and per-component logs (`server.log`, `ears.log`,
`handy-reply.log`).

## Using it

See [RUNNING.md](RUNNING.md) for the day-to-day runbook. The short version:

1. Start the ears daemon (it survives your Claude sessions):

   ```bash
   nohup bash -c 'cd /path/to/claude-listens && exec uv run ears/earsd.py' \
     >> ~/.claude-voice/ears-daemon.out 2>&1 &
   ```

2. Register the channel server in your project's `.mcp.json` and launch —
   channels are a Claude Code research preview, so every launch needs the
   flag and shows a consent warning:

   ```json
   {
     "mcpServers": {
       "voice": { "command": "uv", "args": ["run", "/path/to/claude-listens/src/server.py"] }
     }
   }
   ```

   ```bash
   claude --dangerously-load-development-channels server:voice
   ```

3. `bin/handsfree on`, ask Claude something, and answer out loud.

_Note_: if you are running this often - add a shell alias like `alias claudel="claude --dangerously-load-development-channels server:voice"`

## Sharp edges

- Channels are experimental: the flag, the consent dialog, and the API may
  all change underneath us. Some organisation accounts have channels
  disabled entirely — if the flag is rejected, try a personal account.
- macOS + Apple Silicon only (MLX does the transcription; `afplay` and
  `ps` conventions are assumed).
- `AskUserQuestion` dialogs still need a keyboard: a spoken reply queues
  behind the open dialog and is delivered once it's answered. The channel
  server's instructions ask Claude to prefer plain-text questions in voice
  sessions.
- The mic is only ever hot after an explicit arm, and never silently: the
  daemon plays a sound on arm, stop, and cancel, cancels itself after 15 s
  of no speech, and caps recordings at 90 s.

## Status

Phase 1 (the loop) is live. Phase 2 — claude-to-claude messaging over the
same registry — is design-reserved but unbuilt (`name` field in the
registry). `probe/` holds the throwaway Phase-0 experiments that decided
the architecture; they are kept as archaeology, not as working code.
