# claude-listens

**Work in progress / research preview.** A hands-free voice loop for Claude
Code: you hear Claude's reply out loud, answer by just talking, and your
words land back in the right Claude Code session. No keyboard, no window
focus needed.

The speaking half is a sibling project,
[claude-speaks](https://github.com/ohnotnow/claude-speaks) — install that on
its own if all you want is to *hear* Claude. This repo does the listening:
recording, local transcription, and routing the transcript back to the
session that asked. For the full loop you need both.

Everything is Python, run with [uv](https://docs.astral.sh/uv/). All speech
processing is local (Parakeet via Apple MLX for speech-to-text, silero VAD
for end-of-speech detection): nothing you say leaves your machine.

## How a reply travels

1. Claude finishes a turn; claude-speaks reads it aloud.
2. When playback ends, claude-speaks writes a one-shot *reply target*
   (which session gets your answer) and arms the mic via `bin/ears toggle`.
3. The ears daemon records, stops after ~2.5 s of trailing silence, and
   transcribes locally.
4. `bin/handy-reply` (named for the [Handy](https://github.com/cjpais/Handy)
   era this project grew out of) consumes the reply target and POSTs the
   text to the owning session's channel server, which injects it as a
   `<channel source="voice">` event.

The moving parts (channel server, session registry, ears daemon, the
`bin/` glue) are laid out in
[TECHNICAL_OVERVIEW.md](TECHNICAL_OVERVIEW.md), along with the wire contract
for the channels preview. Runtime state (flag file, registry, logs) lives
under `~/.claude-voice/`.

## Using it

[RUNNING.md](RUNNING.md) is the day-to-day runbook (quirks, config knobs,
troubleshooting). The short version:

1. Start the ears daemon, detached, so it outlives your Claude sessions:

   ```bash
   nohup bash -c 'cd /path/to/claude-listens && exec uv run ears/earsd.py' \
     >> ~/.claude-voice/ears-daemon.out 2>&1 &
   ```

   Copy `ears/config.example.json` to `ears/config.json` and list your
   preferred microphones. The first connected match wins each recording, so
   your AirPods beat the desk mic whenever they're paired.

2. Register the channel server in your project's `.mcp.json` and launch
   with the research-preview flag (expect a consent warning every time):

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

   Worth an alias if you're doing this a lot:
   `alias claudel="claude --dangerously-load-development-channels server:voice"`

3. `bin/handsfree on`, ask Claude something, and answer out loud.

## Sharp edges

- Channels are experimental: the flag, the consent dialog, and the API may
  all change underneath us. Some organisation accounts have channels
  disabled entirely; if the flag is rejected, try a personal account.
- macOS + Apple Silicon only (MLX does the transcription).
- `AskUserQuestion` dialogs still need a keyboard: a spoken reply queues
  behind the open dialog and is delivered once it's answered.
- The mic only arms while the `handsfree` flag is on, and the daemon plays
  a sound on arm, stop, and cancel. No speech for 15 s cancels the
  recording; 90 s is the hard cap.

## Status

Phase 1 (the loop) works. Phase 2, claude-to-claude messaging over the same
registry, is designed but unbuilt. `probe/` holds the throwaway Phase-0
experiments that decided the architecture.
