# Running the hands-free loop (state as of 2026-07-23)

A snapshot for the human (and the next Claude) of how to drive what we built,
what's temporary, and what happens next. The ait tracker in the Handy fork
(`~/Documents/code/forks/Handy`) is the source of truth for remaining work;
the ant store there holds the why.

## The one thing to know first

The silence auto-stop lives **only in the fork build**. The release
`/Applications/Handy.app` does everything else (remote toggle, ExternalScript
delivery) but an armed mic stays hot until you manually run the toggle or
cancel. The dev build we tested with died with the Claude session that
spawned it.

**So the first job next time** (ait `handy-UkLWZ.2.7`): from the fork repo,

```bash
cd ~/Documents/code/forks/Handy
bun run tauri build       # CMAKE_POLICY_VERSION_MINIMUM=3.5 prefix if cmake moans
```

then install the bundle from `src-tauri/target/release/bundle/macos/` in place
of the release app. Keeping it at `/Applications/Handy.app` matters: that
exact path is baked into claude-speaks' `handsfree.py` (`HANDY_BIN`).

## Daily operation

1. **Handy running** (fork build, once installed).
2. **Launch the session with the channel** from a project whose `.mcp.json`
   has the `voice` server (currently only the Handy fork repo — for other
   projects, copy `.mcp.json` there or add the server with its absolute path
   to `mcpServers` in `~/.claude.json`):

   ```bash
   claude --dangerously-load-development-channels server:voice
   ```

   Accept the research-preview warning (every launch) and the MCP consent
   (once per project). The dim "Channels (experimental)…" banner = ready.
3. **`handsfree on`** — or bind `bin/handsfree` in Raycast (it defaults to
   toggle, same recipe as shut-marvin-up).
4. **Talk.** The loop: Claude replies → Marvin speaks it → mic arms itself →
   you speak → ~2.5 s of silence sends it → repeat. Mid-thought pauses under
   2.5 s are safe. An optional spoken "send send" at the end is stripped
   before sending. If you say nothing for 15 s the mic cancels itself and the
   loop idles until you next type or toggle a recording.
5. **`handsfree off`** when you're done (plus
   `/Applications/Handy.app/Contents/MacOS/handy --cancel` if an armed mic
   is mid-flight).

Panic buttons: `handsfree off` · `handy --cancel` · `killall afplay` (Marvin).

Quirks worth remembering: AskUserQuestion dialogs still need a keyboard —
your voice reply queues behind the dialog and is delivered once it's
answered (the channel server's instructions tell Claude to prefer plain-text
questions in voice sessions). Two sessions with the channel open work fine —
replies route to whichever session's hook armed the mic.

## When something's off — where to look

| Symptom | Look at |
| --- | --- |
| Mic never arms | `grep handsfree ~/Documents/code/claude-speaks/stop-hook.log` (flag? registry entry? playback wait?) |
| Arms but nothing sends | `~/.claude-voice/handy-reply.log` (target consumed? curl failed?) and `grep 'Silence' ~/Library/Logs/com.pais.handy/handy.log` (watchdog armed? threshold?) |
| Sends but nothing lands | `~/.claude-voice/server.log` (`/say` received?) and `ls ~/.claude-voice/registry/` (entry for the session? port right?) |
| Whole loop dead | Is the session launched with the flag? Is Handy the fork build? `handsfree status`? |

Config knobs live in Handy's settings store
(`~/Library/Application Support/com.pais.handy/settings_store.json`,
**everything nested under the top-level `"settings"` key, edit only while
Handy is quit**): `silence_auto_stop_ms` (currently 2500),
`paste_method: "external_script"`,
`external_script_path: ~/Documents/code/claude-listens/bin/handy-reply`.

## Next steps (mirrors ait — `ait ready` in the Handy fork)

1. **`handy-UkLWZ.2.7` — proper build + tuning**: install the fork bundle
   (above), then tune at the real sink: threshold comfort, two-session
   routing, actual dishes. Record final numbers as an ant note.
2. **`handy-UkLWZ.2.8` — un-hide External Script on macOS** (one-liner in
   `PasteMethod.tsx:69`) so nobody hand-edits the store again.
3. **Phase 2 — claude-to-claude** (`handy-UkLWZ.3.x`): named sessions +
   `send_to` tool, then deterministic anti-loop caps. The registry's `name`
   field is already reserved for it.

## Uncommitted work, by repo (nothing committed anywhere — all yours)

- **Handy fork** (branch `experiment/claude-chat`): `settings.rs`,
  `managers/audio.rs`, `transcription_coordinator.rs` (the auto-stop),
  `.mcp.json` (untracked; contains a local absolute path — consider
  `.git/info/exclude` rather than committing it to the public fork).
- **claude-speaks** (branch `experiment/handy`): `handsfree.py` (new),
  `audio.py` (returns the player process), `main.py` (two lines).
- **claude-listens** (fresh repo, branch `main`): everything.
