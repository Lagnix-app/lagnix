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

Гучність: "Загальна гучність" (клік/успіх/помилка; типово 25%) і "Звук
наведення" — ЧАСТКА від загальної (типово 50%), тож наведення завжди тихіше
й разом із загальною стає 0. settings.json: sounds_volume / sounds_hover_ratio
(старе абсолютне sounds_hover_volume переноситься автоматично).
Наведення на пункт меню грає один з кількох варіантів звуку (щоб не
набридало) і не частіше ніж раз на 80 мс — цього досить, щоб не сипати
звуками, коли курсор проходить між внутрішніми під-віджетами однієї й
тієї самої кнопки (там миттєво йде Leave/Enter-Leave/Enter, набагато
швидше за 80 мс).

Усі помилки (немає аудіопристрою, зіпсований WAV тощо) пишуться в
logs.txt через core.logging_setup, а не проковтуються мовчки.
"""

import json
import math
import os
import random
import struct
import time
import wave

from core.logging_setup import get_logger
from core.settings import SETTINGS_PATH as SETTINGS_FILE
from core.settings import load_settings, update_setting

ASSETS_SOUNDS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "sounds"
)

_SAMPLE_RATE = 44100

HOVER_VARIANTS = ("hover_1", "hover_2", "hover_3")
SOUND_NAMES = HOVER_VARIANTS + ("click", "success", "error")

_HOVER_MIN_INTERVAL_S = 0.08

_logger = get_logger(__name__)

try:
    import pygame
    _HAS_PYGAME = True
except ImportError:
    pygame = None
    _HAS_PYGAME = False
    _logger.error("pygame недоступний — звуки інтерфейсу вимкнені (pip install pygame-ce)")

def _clamp(value) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _initial_hover_ratio(settings: dict) -> float:
    """Частка наведення; зі старого абсолютного sounds_hover_volume — перерахунок."""
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        raw = {}
    if "sounds_hover_ratio" in raw:
        return _clamp(raw["sounds_hover_ratio"])
    old_hover, volume = raw.get("sounds_hover_volume"), _clamp(settings.get("sounds_volume", 0.25))
    if old_hover is None or volume <= 0:
        return 0.5
    return _clamp(round(float(old_hover) / volume, 2))


_settings = load_settings()
_enabled = bool(_settings.get("sounds_enabled", True))
_volume = _clamp(_settings.get("sounds_volume", 0.25))
_hover_ratio = _initial_hover_ratio(_settings)

_mixer_ready = False
_mixer_failed = False
_sounds: dict = {}
_last_hover_at = float("-inf")


# ------------------------------------------------------------- синтез WAV

def _fade_envelope(i: int, n: int, attack: int, decay_k: float) -> float:
    """М'яка атака (лінійна) + експоненційне затухання до кінця ноти —
    без "різких клацань" на межах, які дає лінійний release."""
    frac = i / max(n - 1, 1)
    attack_env = min(1.0, i / max(attack, 1))
    decay_env = math.exp(-decay_k * frac)
    return attack_env * decay_env


def _tone(freq: float, duration_s: float, volume: float = 1.0, attack_s: float = 0.006, decay_k: float = 4.0) -> list:
    n = int(_SAMPLE_RATE * duration_s)
    attack = int(_SAMPLE_RATE * attack_s)
    samples = []
    for i in range(n):
        t = i / _SAMPLE_RATE
        env = _fade_envelope(i, n, attack, decay_k)
        value = math.sin(2 * math.pi * freq * t) * env * volume
        samples.append(value)
    return samples


def _chirp(freq_start: float, freq_end: float, duration_s: float, volume: float = 1.0,
           attack_s: float = 0.0025, decay_k: float = 5.5, harmonic_ratio: float = 0.28) -> list:
    """Синусоїда зі спадною частотою (freq_start -> freq_end) — "сатисфайний"
    м'який тік/пуп замість монотонного піску. Фаза інтегрується правильно
    (не просто freq*t), інакше при зміні частоти лізуть биття/тріски.
    Додає другу гармоніку (harmonic_ratio) для дерев'яного, не "пластикового"
    тембру."""
    n = int(_SAMPLE_RATE * duration_s)
    attack = int(_SAMPLE_RATE * attack_s)
    samples = []
    for i in range(n):
        t = i / _SAMPLE_RATE
        frac = i / max(n - 1, 1)
        env = _fade_envelope(i, n, attack, decay_k)
        phase = 2 * math.pi * (freq_start * t + (freq_end - freq_start) * (t * t) / (2 * duration_s))
        value = math.sin(phase) * env * volume
        value += math.sin(2 * phase) * env * volume * harmonic_ratio
        samples.append(value)
    return samples


def _concat(*sample_lists) -> list:
    out = []
    for samples in sample_lists:
        out.extend(samples)
    return out


def _mix(*layers_with_offset: tuple) -> list:
    """Змішує кілька фрагментів (samples, offset_s), даючи їм лунати внахлест
    — для акорду успіху (ноти "накладаються", а не грають строго по черзі)."""
    total_len = 0
    for samples, offset_s in layers_with_offset:
        offset = int(_SAMPLE_RATE * offset_s)
        total_len = max(total_len, offset + len(samples))
    out = [0.0] * total_len
    for samples, offset_s in layers_with_offset:
        offset = int(_SAMPLE_RATE * offset_s)
        for i, v in enumerate(samples):
            out[offset + i] += v
    return out


def _write_wav(path: str, samples: list) -> None:
    # нормалізація до сталого рівня гучності (~0.85 від максимуму), щоб усі
    # звуки були приблизно однаково чутні — реальна гучність далі
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

    # наведення — 3 м'які варіанти "пуп"/дерев'яний тік (~35-45 мс):
    # синусоїда, що швидко спадає по частоті, з м'якою атакою й
    # експоненційним затуханням + ледь помітна друга гармоніка.
    hover_specs = (
        (1600, 950, 0.038),
        (1400, 850, 0.045),
        (1250, 780, 0.035),
    )
    for (freq_start, freq_end, dur), name in zip(hover_specs, HOVER_VARIANTS):
        variant = _chirp(freq_start, freq_end, dur, volume=0.9, attack_s=0.0025, decay_k=5.5, harmonic_ratio=0.28)
        _write_wav(os.path.join(ASSETS_SOUNDS_DIR, f"{name}.wav"), variant)

    # клік — нижчий, щільніший "тук" (~60 мс): основний тон + сабовий "гуп"
    click = _mix(
        (_tone(360, 0.06, volume=0.9, attack_s=0.002, decay_k=6.0), 0.0),
        (_tone(150, 0.05, volume=0.55, attack_s=0.002, decay_k=7.0), 0.0),
    )
    _write_wav(os.path.join(ASSETS_SOUNDS_DIR, "click.wav"), click)

    # успіх — м'який висхідний акорд із 3 нот (~250 мс), ноти накладаються
    success = _mix(
        (_tone(523.25, 0.14, volume=0.75, attack_s=0.01, decay_k=3.4), 0.0),     # C5
        (_tone(659.25, 0.14, volume=0.75, attack_s=0.01, decay_k=3.4), 0.05),    # E5
        (_tone(783.99, 0.15, volume=0.75, attack_s=0.012, decay_k=3.0), 0.10),   # G5
    )
    _write_wav(os.path.join(ASSETS_SOUNDS_DIR, "success.wav"), success)

    # помилка — низький м'який двотон, без різкості (~220 мс)
    error = _mix(
        (_tone(220, 0.13, volume=0.8, attack_s=0.012, decay_k=3.0), 0.0),
        (_tone(174.6, 0.16, volume=0.75, attack_s=0.014, decay_k=2.6), 0.08),
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

def _is_hover(name: str) -> bool:
    return name in HOVER_VARIANTS


def _hover_volume() -> float:
    return _volume * _hover_ratio


def _volume_for(name: str) -> float:
    return _hover_volume() if _is_hover(name) else _volume


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
        snd.set_volume(_volume_for(name))
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


def _apply_volumes() -> None:
    for name, snd in _sounds.items():
        try:
            snd.set_volume(_volume_for(name))
        except Exception:
            _logger.exception("Не вдалося застосувати гучність до вже завантаженого звуку %s", name)


def set_volume(volume: float, persist: bool = True) -> None:
    """persist=False — лише застосувати (під час перетягування повзунка); зберігається
    при відпусканні — так файл не переписується десятки разів на секунду."""
    global _volume
    _volume = _clamp(round(volume, 2))
    _apply_volumes()
    if persist:
        update_setting("sounds_volume", _volume)


def get_volume() -> float:
    return _volume


def set_hover_ratio(ratio: float, persist: bool = True) -> None:
    global _hover_ratio
    _hover_ratio = _clamp(round(ratio, 2))
    _apply_volumes()
    if persist:
        update_setting("sounds_hover_ratio", _hover_ratio)


def get_hover_ratio() -> float:
    return _hover_ratio


def reset_volumes(volume: float, ratio: float) -> None:
    """Після «Скинути налаштування» — значення вже записані у файл, лише застосувати."""
    global _volume, _hover_ratio
    _volume, _hover_ratio = _clamp(volume), _clamp(ratio)
    _apply_volumes()


def _play(name: str, force: bool = False) -> None:
    if not force and not _enabled:
        return
    if _volume_for(name) <= 0.0:
        return
    snd = _get_sound(name)
    if snd is None:
        return
    try:
        snd.set_volume(_volume_for(name))
        snd.play()
    except Exception:
        _logger.exception("Не вдалося відтворити звук %s", name)


def play_hover(force: bool = False) -> None:
    global _last_hover_at
    # perf_counter: гарантовано високоточний годинник (monotonic() на Windows
    # став таким лише з Python 3.13, раніше мав крок ~15.6 мс).
    now = time.perf_counter()
    if not force and (now - _last_hover_at) < _HOVER_MIN_INTERVAL_S:
        return
    _last_hover_at = now
    _play(random.choice(HOVER_VARIANTS), force=force)


def play_click(force: bool = False) -> None:
    _play("click", force=force)


def play_success(force: bool = False) -> None:
    _play("success", force=force)


def play_error(force: bool = False) -> None:
    _play("error", force=force)
