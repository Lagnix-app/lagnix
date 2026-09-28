"""Короткі синтезовані звуки інтерфейсу PulseFPS (наведення, клік, успіх,
помилка) — генеруються процедурно у WAV (жодних сторонніх аудіофайлів) і
відтворюються через pygame.mixer.

Чому не winsound: `winsound.PlaySound(..., SND_MEMORY | SND_ASYNC)`
кидає `RuntimeError: Cannot play asynchronously from memory` — ця
комбінація прапорів у Python узагалі не підтримується (winsound не може
гарантувати, що буфер у пам'яті переживе асинхронне відтворення). А
`SND_ASYNC` без `SND_MEMORY` (з файлу) грає лише ОДИН звук одночасно:
наступний виклик обриває попередній. pygame.mixer грає кожен Sound на
окремому каналі мікшера — кілька звуків накладаються, жоден не обривається,
і має нормальну гучність через Sound.set_volume() (яку сама WAV-генерація
далі не чіпає — гучність повністю в runtime, повзунок діє миттєво).

Гучність керується вимикачем "Звуки" й повзунком у вкладці «Налаштування»
(config.json: sounds_enabled/sounds_volume) — set_enabled()/set_volume()
одразу застосовують зміну до вже завантажених звуків і до наступного
відтворення. Усі помилки (немає аудіопристрою, зіпсований WAV тощо)
пишуться в logs.txt через core.logging_setup, а не проковтуються мовчки.
"""

import math
import os
import struct
import wave

from core.logging_setup import get_logger
from core.settings import load_settings, update_setting

ASSETS_SOUNDS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "sounds"
)

_SAMPLE_RATE = 44100
SOUND_NAMES = ("hover", "click", "success", "error")

_logger = get_logger(__name__)

try:
    import pygame
    _HAS_PYGAME = True
except ImportError:
    pygame = None
    _HAS_PYGAME = False
    _logger.error("pygame недоступний — звуки інтерфейсу вимкнені (pip install pygame-ce)")

_settings = load_settings()
_enabled = bool(_settings.get("sounds_enabled", True))
_volume = float(_settings.get("sounds_volume", 0.35))

_mixer_ready = False
_mixer_failed = False
_sounds: dict = {}


# ------------------------------------------------------------- синтез WAV

def _envelope(i: int, n: int, attack: int, release: int) -> float:
    if i < attack:
        return i / max(attack, 1)
    if i > n - release:
        return max(0.0, (n - i) / max(release, 1))
    return 1.0


def _tone(freq: float, duration_s: float, volume: float = 1.0, attack_s: float = 0.004, release_s: float = 0.015) -> list:
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


def _concat(*sample_lists) -> list:
    out = []
    for samples in sample_lists:
        out.extend(samples)
    return out


def _write_wav(path: str, samples: list) -> None:
    # нормалізація до сталого рівня гучності (~0.85 від максимуму), щоб усі
    # 4 звуки були приблизно однаково чутні — реальна гучність далі
    # регулюється в рантаймі через Sound.set_volume(), не тут.
    peak = max((abs(v) for v in samples), default=1.0) or 1.0
    scale = 0.85 / peak
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

    # тихий короткий "тік" наведення на пункт меню (~45 мс)
    hover = _tone(1500, 0.045, volume=0.6, attack_s=0.003, release_s=0.02)
    _write_wav(os.path.join(ASSETS_SOUNDS_DIR, "hover.wav"), hover)

    # клік — короткий двотоновий "клац" (~48 мс)
    click = _concat(
        _tone(720, 0.02, volume=0.9, attack_s=0.001, release_s=0.01),
        _tone(480, 0.028, volume=0.7, attack_s=0.001, release_s=0.016),
    )
    _write_wav(os.path.join(ASSETS_SOUNDS_DIR, "click.wav"), click)

    # успіх — три висхідні короткі ноти (~115 мс)
    success = _concat(
        _tone(660, 0.035, volume=0.85, attack_s=0.003, release_s=0.014),
        _tone(880, 0.035, volume=0.85, attack_s=0.003, release_s=0.014),
        _tone(1175, 0.045, volume=0.8, attack_s=0.003, release_s=0.02),
    )
    _write_wav(os.path.join(ASSETS_SOUNDS_DIR, "success.wav"), success)

    # помилка/попередження — дві низхідні приглушені ноти (~125 мс)
    error = _concat(
        _tone(380, 0.055, volume=0.85, attack_s=0.003, release_s=0.018),
        _tone(300, 0.07, volume=0.8, attack_s=0.003, release_s=0.026),
    )
    _write_wav(os.path.join(ASSETS_SOUNDS_DIR, "error.wav"), error)


def ensure_sounds_exist() -> None:
    names = tuple(f"{n}.wav" for n in SOUND_NAMES)
    if all(os.path.exists(os.path.join(ASSETS_SOUNDS_DIR, n)) for n in names):
        return
    try:
        _generate_all()
    except Exception:
        _logger.exception("Не вдалося згенерувати звукові файли в %s", ASSETS_SOUNDS_DIR)


# --------------------------------------------------------------- відтворення

def _ensure_mixer() -> bool:
    global _mixer_ready, _mixer_failed
    if _mixer_ready:
        return True
    if _mixer_failed or not _HAS_PYGAME:
        return False
    try:
        pygame.mixer.init(frequency=_SAMPLE_RATE, size=-16, channels=2)
        _mixer_ready = True
    except Exception:
        _mixer_failed = True
        _logger.exception("Не вдалося ініціалізувати аудіомікшер pygame.mixer")
    return _mixer_ready


def _get_sound(name: str):
    cached = _sounds.get(name)
    if cached is not None:
        return cached
    if not _ensure_mixer():
        return None
    ensure_sounds_exist()
    path = os.path.join(ASSETS_SOUNDS_DIR, f"{name}.wav")
    try:
        snd = pygame.mixer.Sound(path)
        snd.set_volume(_volume)
    except Exception:
        _logger.exception("Не вдалося завантажити звук %s", path)
        return None
    _sounds[name] = snd
    return snd


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
    for snd in _sounds.values():
        try:
            snd.set_volume(_volume)
        except Exception:
            _logger.exception("Не вдалося застосувати гучність до вже завантаженого звуку")


def get_volume() -> float:
    return _volume


def _play(name: str, force: bool = False) -> None:
    if not force and not _enabled:
        return
    if _volume <= 0.0:
        return
    snd = _get_sound(name)
    if snd is None:
        return
    try:
        snd.set_volume(_volume)
        snd.play()
    except Exception:
        _logger.exception("Не вдалося відтворити звук %s", name)


def play_hover(force: bool = False) -> None:
    _play("hover", force=force)


def play_click(force: bool = False) -> None:
    _play("click", force=force)


def play_success(force: bool = False) -> None:
    _play("success", force=force)


def play_error(force: bool = False) -> None:
    _play("error", force=force)
