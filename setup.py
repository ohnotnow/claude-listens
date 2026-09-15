#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "sounddevice>=0.5"]
# ///
"""First-time setup for claude-listens. Run it once, from a terminal:

    uv run setup.py

It walks through the once-ever steps in order, asking before anything that
touches state outside this clone: pick your microphones, download the
speech model, install the ears daemon as a launchd agent, register the voice
channel server with Claude Code for all projects, and point a sibling
claude-speaks clone at this recorder. Every step is safe to re-run.
"""

import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT / "ears"))
from earsd import DEFAULTS  # noqa: E402  (model name and config defaults)

CONFIG = PROJECT / "ears" / "config.json"
BIN = PROJECT / "bin"
SERVER = PROJECT / "src" / "server.py"
SPEAKS_CONFIG = PROJECT.parent / "claude-speaks" / "config.json"
DAEMON_READY_TIMEOUT_S = 180


def heading(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def ask(prompt: str, default: str = "") -> str:
    """input() with a default on empty or EOF, so piped answers work too."""
    try:
        answer = input(prompt).strip()
    except EOFError:
        print()
        answer = ""
    return answer or default


def ask_yes(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    answer = ask(f"{prompt} [{hint}] ").lower()
    if not answer:
        return default
    return answer.startswith("y")


def load_ears_config() -> dict:
    if CONFIG.is_file():
        try:
            return json.loads(CONFIG.read_text())
        except json.JSONDecodeError as exc:
            sys.exit(f"{CONFIG} is not valid JSON ({exc}); fix or delete it and re-run")
    return {}


def preflight() -> bool:
    heading("Checking the basics")
    if sys.platform != "darwin" or platform.machine() != "arm64":
        sys.exit("claude-listens needs macOS on Apple silicon (the speech model runs on MLX).")
    print("macOS on Apple silicon: yes")
    if not shutil.which("uv"):
        sys.exit("uv not found on PATH; install it from https://docs.astral.sh/uv/ and re-run.")
    print("uv: found")
    has_claude = shutil.which("claude") is not None
    print("claude: found" if has_claude else "claude: NOT found (the Claude Code step will print a command for later)")
    return has_claude


def choose_microphones() -> None:
    heading("Microphones")
    import sounddevice as sd

    names: list[str] = []
    for device in sd.query_devices():
        if device["max_input_channels"] > 0 and device["name"] not in names:
            names.append(device["name"])
    if not names:
        sys.exit("No input devices found. Plug in or pair a microphone and re-run.")

    print("Input devices connected right now:")
    for i, name in enumerate(names, 1):
        print(f"  {i}. {name}")
    print(
        "\nThe daemon uses the first device on your preference list that is connected\n"
        "when a recording starts, so AirPods can outrank a desk mic whenever they are\n"
        "paired. Enter numbers, most preferred first (e.g. '3 1'), or press Enter to\n"
        "use the system default input."
    )
    chosen: list[str] = []
    while True:
        raw = ask("Preferred microphones: ")
        if not raw:
            break
        try:
            picks = [int(tok) for tok in raw.replace(",", " ").split()]
            chosen = [names[p - 1] for p in picks]
            break
        except (ValueError, IndexError):
            print("Use the numbers from the list, separated by spaces.")

    cfg = load_ears_config()
    cfg["microphones"] = chosen
    CONFIG.write_text(json.dumps(cfg, indent=2) + "\n")
    print(f"Wrote {CONFIG}: microphones = {chosen or 'system default'}")


def download_model() -> None:
    heading("Speech model")
    model = load_ears_config().get("model", DEFAULTS["model"])
    cache = Path.home() / ".cache" / "huggingface" / "hub" / f"models--{model.replace('/', '--')}"
    if cache.is_dir():
        print(f"{model} is already downloaded.")
        return
    print(
        f"Downloading {model} (a few hundred MB, once). The daemon runs offline after\n"
        "this, so it has to happen before the daemon starts."
    )
    env = {k: v for k, v in os.environ.items() if k != "HF_HUB_OFFLINE"}
    result = subprocess.run(
        [
            "uv", "run", "--no-project", "--with", "parakeet-mlx>=0.5.2", "python", "-c",
            f"from parakeet_mlx import from_pretrained; from_pretrained({model!r})",
        ],
        env=env,
    )
    if result.returncode != 0:
        sys.exit("Model download failed; check your network and re-run.")
    print("Model downloaded.")


def install_daemon() -> None:
    heading("Ears daemon (launchd agent)")
    print(
        "This installs the recorder as a launchd agent that starts at login and\n"
        "restarts if it dies. The first time it records, macOS will ask whether\n"
        "python3 may use the microphone: click Allow. A denial does not error, it\n"
        "just records silence."
    )
    if not ask_yes("Install the ears daemon now?"):
        print(f"Skipped. Later: {BIN / 'ears'} install")
        return
    subprocess.run([str(BIN / "ears"), "install"], check=True)
    print("Waiting for the daemon to load the model", end="", flush=True)
    deadline = time.monotonic() + DAEMON_READY_TIMEOUT_S
    while time.monotonic() < deadline:
        status = subprocess.run([str(BIN / "ears"), "status"], capture_output=True, text=True)
        if '"idle"' in status.stdout:
            print(f"\nDaemon ready: {status.stdout.strip()}")
            return
        print(".", end="", flush=True)
        time.sleep(2)
    print(f"\nDaemon not ready after {DAEMON_READY_TIMEOUT_S}s; see ~/.claude-voice/ears.log")


def register_mcp(has_claude: bool) -> None:
    heading("Claude Code voice channel")
    add_cmd = ["claude", "mcp", "add", "--scope", "user", "voice", "--", "uv", "run", str(SERVER)]
    if not has_claude:
        print("Once Claude Code is installed, run:\n  " + " ".join(add_cmd))
        return
    existing = subprocess.run(["claude", "mcp", "get", "voice"], capture_output=True, text=True)
    if existing.returncode == 0 and str(SERVER) in existing.stdout:
        print("The voice server is already registered with Claude Code for all projects.")
        return
    if existing.returncode == 0:
        print("A different 'voice' MCP server is registered:\n" + existing.stdout.strip())
        if not ask_yes("Replace it with this clone's server?", default=False):
            return
        subprocess.run(["claude", "mcp", "remove", "--scope", "user", "voice"])
    print(
        "This registers the channel server in your Claude Code user config so every\n"
        "project can use it, with no per-project .mcp.json."
    )
    if ask_yes("Register it now?"):
        subprocess.run(add_cmd, check=True)
    else:
        print("Skipped. Later: " + " ".join(add_cmd))


def configure_speaks() -> None:
    heading("claude-speaks (the talking half)")
    arm_cmd = [str(BIN / "ears"), "arm"]
    if not SPEAKS_CONFIG.is_file():
        print(
            f"No claude-speaks clone found at {SPEAKS_CONFIG.parent}. Install it from\n"
            "https://github.com/ohnotnow/claude-speaks and add this to its config.json:\n"
            f'  "handsfree_arm_command": {json.dumps(arm_cmd)}'
        )
        return
    try:
        cfg = json.loads(SPEAKS_CONFIG.read_text())
    except json.JSONDecodeError as exc:
        print(f"{SPEAKS_CONFIG} is not valid JSON ({exc}); add handsfree_arm_command by hand.")
        return
    if cfg.get("handsfree_arm_command") == arm_cmd:
        print("claude-speaks already points at this recorder.")
        return
    if ask_yes(f"Point claude-speaks ({SPEAKS_CONFIG}) at this recorder?"):
        cfg["handsfree_arm_command"] = arm_cmd
        SPEAKS_CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
        print("Updated handsfree_arm_command.")
    else:
        print(f'Skipped. Add by hand: "handsfree_arm_command": {json.dumps(arm_cmd)}')


def summary() -> None:
    heading("Done. Day to day")
    print(
        "1. Launch Claude Code with the channels flag (worth an alias):\n"
        "     alias claudel='claude --dangerously-load-development-channels server:voice'\n"
        "   Accept the research-preview warning; it appears every launch.\n"
        '2. Tell Claude "go hands-free", ask it something, and answer out loud.\n'
        '   "Stop hands-free" turns the mic off again.\n'
        "\nRUNNING.md has the quirks and the troubleshooting table."
    )


def main() -> None:
    print("claude-listens setup. Ctrl-C at any point; every step is safe to re-run.")
    has_claude = preflight()
    choose_microphones()
    download_model()
    install_daemon()
    register_mcp(has_claude)
    configure_speaks()
    summary()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped. Re-run uv run setup.py to continue.")
