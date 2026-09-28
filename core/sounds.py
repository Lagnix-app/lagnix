"""Короткі синтезовані звуки інтерфейсу PulseFPS (наведення, клік, успіх,
помилка) — генеруються процедурно у WAV (жодних сторонніх файлів) і
відтворюються асинхронно через winsound, щоб не гальмувати інтерфейс.

Гучність керується вимикачем "Звуки" й повзунком у вкладці «Налаштування»
(config.json: sounds_enabled/sounds_volume) — set_enabled()/set_volume()
одразу застосовують зміну до наступного відтворення.
"""

import io
import math
import os
import struct
import threading
import wave

from core.settings import load_settings, update_setting

ASSETS_SOUNDS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "sounds"
)

_SAMPLE_RATE = 44100

try:
    import winsound
    _HAS_WINSOUND = True
except ImportError:
    _HAS_WINSOUND = False

_settings = load_settings()
_enabled = bool(_settings.get("sounds_enabled", True))
_volume = float(_settings.get("sounds_volume", 0.35))

_CACHE: dict[str, tuple[wave._wave_params, bytes]] = {}
_LOCK = threading.Lock()


# ------------------------------------------------------------- синтез WAV

def _envelope(i: int, n: int, attack: int, release: int) -> float:
    if i < attack:
        return i / max(attack, 1)
    if i > n - release:
        return max(0.0, (n - i) / max(release, 1))
    return 1.0


def _tone(freq: float, duration_s: float, volume: float = 1.0, attack_s: float = 0.006, release_s: float = 0.02) -> list:
    n = int(_SAMPLE_RATE * duration_s)
    attack = int(_SAMPLE_RATE * attack_s)
    release = int(_SAMPLE_RATE * release_s)
    samples = []
    for i in range(n):
        t = i / _SAMPLE_RATE
        env = _envelope(i, n, attack, release)
        value = math.sin(2 * math.pi * freq * t) * env * volume
        samples.append(value)
    return samples


def _mix(*sample_lists) -> list:
    length = max(len(s) for s in sample_lists)
    out = [0.0] * length
    for samples in sample_lists:
        for i, v in enumerate(samples):
            out[i] += v
    return out


def _concat(*sample_lists) -> list:
    out = []
    for samples in sample_lists:
        out.extend(samples)
    return out


def _write_wav(path: str, samples: list) -> None:
    peak = max((abs(v) for v in samples), default=1.0) or 1.0
    scale = 0.92 / peak if peak > 0.92 else 1.0
    frames = struct.pack(
        f"<{len(samples)}h",
        *(max(-32768, min(32767, int(v * scale * 32767))) for v in samples),
    )
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(_SAMPLE_RATE)
        wf.writeframes(frames)


def _generate_all() -> None:
    os.makedirs(ASSETS_SOUNDS_DIR, exist_ok=True)

    # тихий короткий "тік" наведення на пункт меню
    hover = _tone(1500, 0.02, volume=0.5, attack_s=0.002, release_s=0.012)
    _write_wav(os.path.join(ASSETS_SOUNDS_DIR, "hover.wav"), hover)

    # клік — короткий двотоновий "клац"
    click = _concat(
        _tone(720, 0.018, volume=0.8, attack_s=0.001, release_s=0.01),
        _tone(480, 0.03, volume=0.6, attack_s=0.001, release_s=0.02),
    )
    _write_wav(os.path.join(ASSETS_SOUNDS_DIR, "click.wav"), click)

    # успіх — висхідний дзвіночок
    success = _concat(
        _tone(660, 0.09, volume=0.7),
        _tone(880, 0.09, volume=0.7),
        _tone(1175, 0.14, volume=0.6, release_s=0.08),
    )
    _write_wav(os.path.join(ASSETS_SOUNDS_DIR, "success.wav"), success)

    # помилка/попередження — низхідний приглушений сигнал
    error = _concat(
        _tone(380, 0.09, volume=0.7),
        _tone(300, 0.16, volume=0.65, release_s=0.09),
    )
    _write_wav(os.path.join(ASSETS_SOUNDS_DIR, "error.wav"), error)


def ensure_sounds_exist() -> None:
    names = ("hover.wav", "click.wav", "success.wav", "error.wav")
    if all(os.path.exists(os.path.join(ASSETS_SOUNDS_DIR, n)) for n in names):
        return
    _generate_all()


# --------------------------------------------------------------- відтворення

def set_enabled(enabled: bool) -> None:
    global _enabled
    _enabled = bool(enabled)
    update_setting("sounds_enabled", _enabled)


def is_enabled() -> bool:
    return _enabled


def set_volume(volume: float) -> None:
    global _volume
    _volume = max(0.0, min(1.0, volume))
    update_setting("sounds_volume", _volume)


def get_volume() -> float:
    return _volume


def _scale_pcm16(frames: bytes, volume: float) -> bytes:
    count = len(frames) // 2
    samples = struct.unpack(f"<{count}h", frames[: count * 2])
    scaled = (max(-32768, min(32767, int(s * volume))) for s in samples)
    return struct.pack(f"<{count}h", *scaled)


def _load(name: str):
    cached = _CACHE.get(name)
    if cached is not None:
        return cached
    ensure_sounds_exist()
    path = os.path.join(ASSETS_SOUNDS_DIR, f"{name}.wav")
    with wave.open(path, "rb") as wf:
        params = wf.getparams()
        frames = wf.readframes(wf.getnframes())
    _CACHE[name] = (params, frames)
    return _CACHE[name]


def _play(name: str) -> None:
    if not _enabled or not _HAS_WINSOUND or _volume <= 0.0:
        return
    try:
        with _LOCK:
            params, frames = _load(name)
        scaled = _scale_pcm16(frames, _volume)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(params.nchannels)
            wf.setsampwidth(params.sampwidth)
            wf.setframerate(params.framerate)
            wf.writeframes(scaled)
        winsound.PlaySound(buf.getvalue(), winsound.SND_MEMORY | winsound.SND_ASYNC)
    except Exception:
        pass  # звук — приємний бонус, а не критична функціональність


def play_hover() -> None:
    _play("hover")


def play_click() -> None:
    _play("click")


def play_success() -> None:
    _play("success")


def play_error() -> None:
    _play("error")
