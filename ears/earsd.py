# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "numpy",
#     "onnxruntime",
#     "parakeet-mlx>=0.5.2",
#     "sounddevice>=0.5",
# ]
# ///
"""earsd — the ears of the hands-free Claude Code voice loop (ait handy-UkLWZ.5.1).

Resident daemon: keeps a parakeet-mlx model warm, records from the first
connected microphone on the config preference list, stops itself on trailing
silence (silero VAD watchdog), transcribes, and hands the text to
reply_command as argv[1] — the same contract Handy's ExternalScript used,
so bin/handy-reply works unchanged.

    uv run ears/earsd.py                 # foreground; logs to ~/.claude-voice/ears.log too
    bin/ears status|toggle|arm|cancel    # control from anywhere

Design evidence: ant handy-9X77J (latency, mic preference list),
ant handy-cwm6b (the pivot this replaces the Handy fork under).
"""

import json
import os
import queue
import subprocess
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

EARS_DIR = Path(__file__).resolve().parent
BASE = Path.home() / ".claude-voice"
LOG = BASE / "ears.log"
SAMPLE_RATE = 16_000
CHUNK = 512  # 32 ms @ 16 kHz — silero window size

DEFAULTS = {
    "microphones": [],
    "control_port": 18790,
    "silence_auto_stop_ms": 2500,
    "no_speech_cancel_ms": 15000,
    "max_recording_s": 90,
    "vad_threshold": 0.5,
    "reply_command": str(EARS_DIR.parent / "bin" / "handy-reply"),
    "sounds": {"start": "Tink", "stop": "Pop", "cancel": "Basso"},
    "model": "mlx-community/parakeet-tdt-0.6b-v2",
    "vad_model": "models/silero_vad_v4.onnx",
}


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    path = EARS_DIR / "config.json"
    if path.exists():
        cfg.update(json.loads(path.read_text()))
    return cfg


def write_wav(path: Path, pcm: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm.tobytes())


def play_sound(cfg: dict, key: str) -> None:
    name = cfg["sounds"].get(key)
    if name:
        subprocess.Popen(
            ["afplay", f"/System/Library/Sounds/{name}.aiff"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def resolve_device(prefs: list[str]):
    """First connected input device matching the preference list, else default.

    Resolved at every arm, not at startup — devices come and go (ant handy-9X77J).
    """
    import sounddevice as sd

    devices = sd.query_devices()
    for pref in prefs:
        for i, d in enumerate(devices):
            if d["max_input_channels"] > 0 and pref.lower() in d["name"].lower():
                return i, d["name"]
    i = sd.default.device[0]
    if i is None or i < 0:
        raise RuntimeError("no input device available")
    return i, sd.query_devices(i)["name"]


class SileroVad:
    """Streaming silero VAD over onnxruntime; handles v4 (h/c) and v5 (state)."""

    def __init__(self, model_path: Path):
        import onnxruntime as ort

        self.sess = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        self.input_names = {i.name for i in self.sess.get_inputs()}
        self.output_names = [o.name for o in self.sess.get_outputs()]
        self.v4 = "h" in self.input_names and "c" in self.input_names
        self.sr_1d = False
        self.reset()
        # self-test on silence; some builds want sr as [1] not scalar
        try:
            self.prob(np.zeros(CHUNK, dtype=np.float32))
        except Exception:
            self.sr_1d = True
            self.reset()
            self.prob(np.zeros(CHUNK, dtype=np.float32))
        self.reset()

    def reset(self) -> None:
        self.h = np.zeros((2, 1, 64), dtype=np.float32)
        self.c = np.zeros((2, 1, 64), dtype=np.float32)
        self.state = np.zeros((2, 1, 128), dtype=np.float32)

    def prob(self, chunk: np.ndarray) -> float:
        sr = np.array([SAMPLE_RATE] if self.sr_1d else SAMPLE_RATE, dtype=np.int64)
        feed = {"input": chunk.reshape(1, -1).astype(np.float32), "sr": sr}
        if self.v4:
            feed["h"], feed["c"] = self.h, self.c
        else:
            feed["state"] = self.state
        out = dict(zip(self.output_names, self.sess.run(None, feed)))
        if self.v4:
            self.h, self.c = out["hn"], out["cn"]
        else:
            self.state = out["stateN"]
        return float(out["output"].reshape(-1)[0])


class Ears:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.state = "loading"
        self.device_name = None
        self.stop_flag = threading.Event()
        self.cancel_flag = threading.Event()
        self.model = None
        self.jobs: queue.Queue = queue.Queue()
        self.vad = SileroVad(EARS_DIR / cfg["vad_model"])

    def transcribe_worker(self) -> None:
        """Owns ALL MLX calls — model load, warm-up, every transcription.

        MLX streams are per-thread; touching the model from any other thread
        raises "There is no Stream(cpu, 1) in current thread".
        """
        t0 = time.monotonic()
        from parakeet_mlx import from_pretrained

        self.model = from_pretrained(self.cfg["model"])
        warm = BASE / "ears-warmup.wav"
        write_wav(warm, np.zeros(SAMPLE_RATE, dtype=np.int16))
        self.model.transcribe(str(warm))
        log(f"model warm in {time.monotonic() - t0:.2f}s")
        with self.lock:
            self.state = "idle"
        log("ready")
        while True:
            wav_path = self.jobs.get()
            try:
                self._transcribe_and_reply(wav_path)
            except Exception as exc:
                log(f"transcription error: {exc!r}")
                play_sound(self.cfg, "cancel")
            finally:
                with self.lock:
                    self.state = "idle"

    # -- control -----------------------------------------------------------
    def toggle(self) -> str:
        with self.lock:
            if self.state == "idle":
                self._start_locked()
                return "recording"
            if self.state == "recording":
                self.stop_flag.set()
                return "stopping"
            return self.state

    def arm(self) -> str:
        with self.lock:
            if self.state == "idle":
                self._start_locked()
                return "recording"
            return self.state

    def cancel(self) -> str:
        with self.lock:
            if self.state == "recording":
                self.cancel_flag.set()
                return "cancelling"
            return self.state

    def _start_locked(self) -> None:
        self.state = "recording"
        self.stop_flag = threading.Event()
        self.cancel_flag = threading.Event()
        threading.Thread(target=self._session, daemon=True).start()

    # -- recording session -------------------------------------------------
    def _session(self) -> None:
        handed_off = False
        try:
            audio = self._record()
            if audio is not None:
                wav_path = BASE / "ears-last.wav"
                pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
                write_wav(wav_path, pcm)
                with self.lock:
                    self.state = "transcribing"
                self.jobs.put(wav_path)
                handed_off = True
        except Exception as exc:
            log(f"session error: {exc!r}")
            play_sound(self.cfg, "cancel")
        finally:
            self.device_name = None
            if not handed_off:
                with self.lock:
                    self.state = "idle"

    def _record(self):
        import sounddevice as sd

        cfg = self.cfg
        dev, name = resolve_device(cfg["microphones"])
        self.device_name = name
        log(f"recording from {name!r}")
        self.vad.reset()
        q: queue.Queue = queue.Queue()

        def callback(indata, frames, tinfo, status):
            if status:
                log(f"stream status: {status}")
            q.put(indata[:, 0].copy())

        stream = sd.InputStream(
            device=dev,
            samplerate=SAMPLE_RATE,
            blocksize=CHUNK,
            channels=1,
            dtype="float32",
            callback=callback,
        )
        play_sound(cfg, "start")
        buf: list[np.ndarray] = []
        thr = cfg["vad_threshold"]
        with stream:
            start = last_speech = time.monotonic()
            spoken = False
            while True:
                if self.cancel_flag.is_set():
                    log("cancelled by request")
                    play_sound(cfg, "cancel")
                    return None
                if self.stop_flag.is_set():
                    log("stopped by request")
                    break
                try:
                    chunk = q.get(timeout=0.25)
                    buf.append(chunk)
                    if len(chunk) == CHUNK and self.vad.prob(chunk) > thr:
                        last_speech = time.monotonic()
                        spoken = True
                except queue.Empty:
                    pass
                now = time.monotonic()
                if spoken and (now - last_speech) * 1000 >= cfg["silence_auto_stop_ms"]:
                    log(f"trailing silence reached ({cfg['silence_auto_stop_ms']}ms); stopping")
                    break
                if not spoken and (now - start) * 1000 >= cfg["no_speech_cancel_ms"]:
                    log(f"no speech within {cfg['no_speech_cancel_ms']}ms; cancelling")
                    play_sound(cfg, "cancel")
                    return None
                if now - start >= cfg["max_recording_s"]:
                    log(f"max recording length ({cfg['max_recording_s']}s); stopping")
                    break
        play_sound(cfg, "stop")
        if not buf:
            return None
        return np.concatenate(buf)

    def _transcribe_and_reply(self, wav_path: Path) -> None:
        t0 = time.monotonic()
        text = self.model.transcribe(str(wav_path)).text.strip()
        log(f"transcribed in {time.monotonic() - t0:.2f}s: {text!r}")
        if not text:
            log("empty transcript; not invoking reply command")
            return
        cmd = self.cfg["reply_command"]
        try:
            r = subprocess.run([cmd, text], capture_output=True, text=True, timeout=30)
            log(f"reply command exit={r.returncode}" + (f" stderr={r.stderr.strip()!r}" if r.returncode else ""))
        except Exception as exc:
            log(f"reply command failed: {exc!r}")


class Handler(BaseHTTPRequestHandler):
    ears: Ears = None

    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/status":
            self._send(200, {"state": self.ears.state, "device": self.ears.device_name})
        else:
            self._send(404, {"error": "unknown path"})

    def do_POST(self):
        actions = {"/toggle": self.ears.toggle, "/arm": self.ears.arm, "/cancel": self.ears.cancel}
        if self.path in actions:
            self._send(200, {"result": actions[self.path]()})
        else:
            self._send(404, {"error": "unknown path"})

    def log_message(self, *args):
        pass


def main() -> None:
    BASE.mkdir(mode=0o700, exist_ok=True)
    if "EARS_ONLINE" not in os.environ:
        os.environ["HF_HUB_OFFLINE"] = "1"
    cfg = load_config()
    ears = Ears(cfg)
    log(f"starting pid={os.getpid()} port={cfg['control_port']}")

    threading.Thread(target=ears.transcribe_worker, daemon=True).start()
    Handler.ears = ears
    server = ThreadingHTTPServer(("127.0.0.1", cfg["control_port"]), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
