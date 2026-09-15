# Running the hands-free loop (state as of 2026-07-24)

The operational runbook for the loop. The ait tracker in the old Handy fork
repo (`~/Documents/code/forks/Handy`) is the source of truth for remaining
work; the ant store there holds the why (start with the pivot note and the
foundation).

## The shape

Two repos: [claude-speaks](https://github.com/ohnotnow/claude-speaks) speaks
and arms the mic; claude-listens (this repo) records, transcribes, and routes.
The Handy fork is retired — stock Handy is back to being the daily dictation
app and plays no part in the loop.

## The one thing to know first

The ears daemon must be running. `bin/ears install` writes a launchd agent
(`~/Library/LaunchAgents/com.claude-listens.ears.plist`, RunAtLoad +
KeepAlive) and starts it, so it survives reboots and never depends on a
Claude session. Never start it as a child of a Claude session by hand: a
daemon started that way dies with the session (learned twice). If you must
run it in the foreground for debugging, `bin/ears uninstall` first, or the
two will fight over port 18790.

Cold start takes ~20-30 s (model load + MLX kernel warm-up). `bin/ears
status` should say `{"state": "idle"}`. launchd's own stdout/stderr capture
goes to `~/.claude-voice/ears-daemon.out`; the daemon's log is `ears.log`.

## Daily operation

1. **Ears daemon running** (`bin/ears status`).
2. **Launch the session with the channel.** `setup.py` registers the `voice`
   server at user scope (`claude mcp add --scope user voice -- uv run
   .../src/server.py`), so any project works; a per-project `.mcp.json`
   entry does the same for one project:

   ```bash
   claude --dangerously-load-development-channels server:voice
   ```

   Accept the research-preview warning (every launch) and the MCP consent
   (once per project). **Personal account only** — some org accounts have
   channels disabled; if the flag is rejected, that's why.
3. **Say "go hands-free"** to Claude (the `handsfree` MCP tool flips the flag
   and reports the ears daemon state), or run `handsfree on` / bind
   `bin/handsfree` in Raycast.
4. **Talk.** The loop: Claude replies → Marvin speaks → mic arms (Tink) →
   you speak → ~2.5 s of silence sends it (Pop) → repeat. Mid-thought pauses
   under 2.5 s are safe. An optional spoken "send send" at the end is
   stripped. Say nothing for 15 s and the mic cancels itself; recordings cap
   at 90 s.
5. **`handsfree off`** when you're done (plus `bin/ears cancel` if a mic is
   mid-flight). Do this *before* walking away to watch television, or the
   loop will faithfully transcribe the television.

Panic buttons: `handsfree off` · `ears cancel` · `killall afplay` (Marvin).

## Quirks worth remembering

- **First arm under launchd prompts for the microphone.** With no Terminal
  in the ancestry, macOS attributes the mic request to the daemon's own
  interpreter (`python3` in uv's cache) and asks once (seen 2026-09-15;
  Allow, then a spoken test transcribed fine). The grant is
  tied to that interpreter path, so a rebuilt uv environment may ask again.
  A denied grant does not error: recordings come back silent and cancel
  after 15 s. Fix in System Settings, Privacy & Security, Microphone.
- **AskUserQuestion dialogs need a keyboard.** Your voice reply queues
  behind the open dialog and is delivered once it's answered.
- **Resumed sessions have split identities** — the Stop hook sees the
  original conversation id while the restarted process registers under a
  fresh one. claude-speaks bridges this by process ancestry automatically;
  if arming mysteriously stops after a session restart, check
  `stop-hook.log` for the ancestry-match line.
- **Mic choice is per-recording:** first connected match on the
  `microphones` preference list in `ears/config.json`, falling back to the
  system default. AirPods connected-but-not-in-ears will still win the list
  — the 15 s no-speech cancel is the safety net.
- Two channel sessions at once work fine — replies route to whichever
  session's hook armed the mic (one-shot target, bound at arm time).

## When something's off — where to look

| Symptom | Look at |
| --- | --- |
| Mic never arms | `grep handsfree ~/Documents/code/claude-speaks/stop-hook.log` (flag? config key? registry entry? ancestry line? playback wait?) |
| Arms but never stops | `~/.claude-voice/ears.log` (watchdog lines; VAD threshold in `ears/config.json`) |
| Stops but nothing sends | `~/.claude-voice/ears.log` (transcript? reply command exit?) then `~/.claude-voice/handy-reply.log` (target consumed? curl failed = stale target from a dead session) |
| Sends but nothing lands | `~/.claude-voice/server.log` (`/say` received?) and `ls ~/.claude-voice/registry/` (entry for the session? port right?) |
| Whole loop dead | Session launched with the flag? Personal account? `bin/ears status`? `bin/handsfree status`? |

## Config knobs (`ears/config.json`, all optional)

| Key | Default | Meaning |
| --- | --- | --- |
| `microphones` | `[]` | Name fragments, first connected wins per recording |
| `silence_auto_stop_ms` | `2500` | Trailing silence that ends a recording |
| `no_speech_cancel_ms` | `15000` | Cancel if nothing said at all |
| `max_recording_s` | `90` | Hard cap per recording |
| `vad_threshold` | `0.5` | Silero speech probability threshold |
| `reply_command` | `bin/handy-reply` | Receives the transcript as argv[1] |
| `sounds` | Tink/Pop/Basso | Arm / stop / cancel cues (`null` to silence) |
| `control_port` | `18790` | The `bin/ears` CLI's port |

claude-speaks side: `handsfree_arm_command` in its `config.json` (no default —
hands-free declines to arm without it).

## Next steps (mirrors ait — `ait ready` in the fork repo)

1. **Phase 2 — claude-to-claude** (`handy-UkLWZ.3.x`): named sessions +
   `send_to` tool, then deterministic anti-loop caps. The registry's `name`
   field is reserved for it.
2. Unscheduled niceties: TTL check on the
   one-shot reply target (`armed_at` is already recorded); input-side word
   replacements in `bin/handy-reply` ("Clod" → "Claude").
