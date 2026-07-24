# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "numpy",
#     "parakeet-mlx>=0.5.2",
#     "sounddevice>=0.5",
# ]
# ///
"""Phase-0b probe (ait handy-UkLWZ.4.1): warm parakeet-mlx latency on live mic clips.

Throwaway — measures, prints, exits. Not production code.

    uv run probe/ears-latency.py --setup-only   # load model, ambient capture, no speech needed
    uv run probe/ears-latency.py                # full run: 3 spoken rounds, audible prompts

The full run talks to you via `say` and beeps before/after each recording,
so you don't need eyes on the terminal: speak a sentence after the Tink,
stop before the Pop (5 s window).
"""

import argparse
import os
import resource
import subprocess
import tempfile
import time
import wave
from pathlib import Path

SAMPLE_RATE = 16_000
MODEL = "mlx-community/parakeet-tdt-0.6b-v2"


def say(text: str) -> None:
    subprocess.run(["say", text], check=False)


def beep(name: str) -> None:
    subprocess.run(["afplay", f"/System/Library/Sounds/{name}.aiff"], check=False)


def resolve_device(name_fragment: str | None):
    """First input device whose name contains the fragment (case-insensitive)."""
    import sounddevice as sd

    if not name_fragment:
        return None, SAMPLE_RATE
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and name_fragment.lower() in d["name"].lower():
            return i, int(d["default_samplerate"])
    raise SystemExit(f"no input device matching {name_fragment!r}")


def record(seconds: float, device, native_rate: int):
    import numpy as np
    import sounddevice as sd

    frames = sd.rec(
        int(seconds * native_rate),
        samplerate=native_rate,
        channels=1,
        dtype="int16",
        device=device,
    )
    sd.wait()
    if native_rate == SAMPLE_RATE:
        return frames
    # crude decimation is fine for a probe; the daemon will resample properly
    step = native_rate // SAMPLE_RATE
    return np.ascontiguousarray(frames[::step])


def to_wav(frames, path: Path) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(frames.tobytes())


def rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024**2


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--setup-only", action="store_true", help="no spoken rounds")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--online", action="store_true", help="allow HF hub network access")
    ap.add_argument("--device", help="input device name fragment, e.g. 'UGREEN'")
    args = ap.parse_args()

    device, native_rate = resolve_device(args.device)
    if device is not None:
        import sounddevice as sd

        print(f"input device: {sd.query_devices(device)['name']!r} @ {native_rate}Hz")

    if not args.online:
        os.environ["HF_HUB_OFFLINE"] = "1"

    tmpdir = Path(tempfile.mkdtemp(prefix="ears-probe-"))
    results: list[tuple[str, float, str]] = []

    t0 = time.perf_counter()
    from parakeet_mlx import from_pretrained

    model = from_pretrained(MODEL)
    cold_load = time.perf_counter() - t0
    print(f"cold model load: {cold_load:.2f}s   rss={rss_mb():.0f}MB")

    # First inference compiles MLX kernels — part of daemon startup, not per-clip cost.
    import numpy as np

    warm_wav = tmpdir / "warmup.wav"
    to_wav(np.zeros(SAMPLE_RATE, dtype=np.int16), warm_wav)
    t0 = time.perf_counter()
    model.transcribe(str(warm_wav))
    print(f"warm-up transcribe (1s silence): {time.perf_counter() - t0:.2f}s")

    # Ambient capture: proves the mic path works, times a warm no-speech clip.
    print("recording 2s ambient (no need to speak)...")
    frames = record(2.0, device, native_rate)
    peak = int(abs(frames).max())
    ambient_wav = tmpdir / "ambient.wav"
    t0 = time.perf_counter()
    to_wav(frames, ambient_wav)
    text = model.transcribe(str(ambient_wav)).text.strip()
    dt = time.perf_counter() - t0
    print(f"ambient: stop->text {dt:.2f}s  peak={peak}  text={text!r}")
    results.append(("ambient-2s", dt, text))

    if not args.setup_only:
        say(f"Probe starting. {args.rounds} rounds. Speak a sentence after each ding.")
        for n in range(1, args.rounds + 1):
            say(f"Round {n}.")
            time.sleep(0.3)
            beep("Tink")
            frames = record(args.seconds, device, native_rate)
            beep("Pop")
            clip = tmpdir / f"round{n}.wav"
            t0 = time.perf_counter()
            to_wav(frames, clip)
            text = model.transcribe(str(clip)).text.strip()
            dt = time.perf_counter() - t0
            peak = int(abs(frames).max())
            print(f"round {n}: stop->text {dt:.2f}s  peak={peak}  text={text!r}")
            results.append((f"round-{n}", dt, text))
        say("Probe finished. Thank you.")

    print(f"\nfinal rss={rss_mb():.0f}MB   clips kept in {tmpdir}")
    print("\nSUMMARY")
    print(f"  cold load          {cold_load:.2f}s")
    for label, dt, text in results:
        print(f"  {label:<12} {dt:.2f}s  {text!r}")


if __name__ == "__main__":
    main()
