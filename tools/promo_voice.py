"""Озвучка промо-відео: Piper TTS (офлайн) + голос робота + синтезовані біпи/кліки -> *_voice.mp4.

Голоси (ліцензії перевірено на сторінках моделей rhasspy/piper-voices):
  en: en_US-ljspeech-high — датасет LJ Speech, public domain (комерційне використання дозволене);
  uk: uk_UA-ukrainian_tts-medium — датасет CC0.
Моделі лежать у promo/_voices/ (завантажуються з huggingface.co/rhasspy/piper-voices, у git не потрапляють).

Час реплік береться з promo/_work/<сценарій>_<мова>/timeline.json (ті самі позначки, що й у субтитрів),
тож голос збігається з текстом на екрані. Потрібні: pip install piper-tts numpy imageio-ffmpeg.

  python tools/promo_voice.py <sad_robot|one_click> <en|uk>
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import wave

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "promo")
WORK = os.path.join(OUT_DIR, "_work")
VOICES = os.path.join(OUT_DIR, "_voices")
SR = 48000
NAMES = {"sad_robot": "sad-robot", "one_click": "one-click"}

MODELS = {"en": ("en_US-ljspeech-high", None), "uk": ("uk_UA-ukrainian_tts-medium", None)}
SPEAKERS = {"narrator": {"en": None, "uk": 2}, "robot": {"en": None, "uk": 0}}   # uk: tetiana / lada

# (хто, час відносно позначки, позначка, текст) — «Lagnix»/«FPS» у uk записано вимовою
LINES = {
    ("sad_robot", "en"): [
        ("narrator", 0.25, "start", "I tried to uninstall my own app…", 0.88),
        ("robot", 0.55, "window_shown", "Do you really want to remove me?"),
        ("robot", 0.05, "happy", "Yay! You stayed!"),
        ("narrator", 0.05, "outro", "Lagnix. Free FPS booster. Link in bio.", 0.75),
    ],
    ("sad_robot", "uk"): [
        ("narrator", 0.25, "start", "Я спробував видалити свою програму…"),
        ("robot", 0.55, "window_shown", "Ти справді хочеш мене видалити?"),
        ("robot", 0.05, "happy", "Ура! Ти залишився!"),
        ("narrator", 0.45, "outro", "Лагнікс. Безкоштовний ефпіес бустер. Посилання в профілі."),
    ],
    ("one_click", "en"): [
        ("narrator", 0.25, "start", "RAM full before a match?"),
        ("robot", 0.15, "monitor_again", "One click — background apps closed."),
        ("narrator", 0.1, "safe", "Safe for Valorant and CS2."),
        ("narrator", 0.45, "outro", "Lagnix. Free. Link in bio."),
    ],
    ("one_click", "uk"): [
        ("narrator", 0.25, "start", "Рам забита перед грою?"),
        ("robot", 0.15, "monitor_again", "Один клік — фонові програми закрито."),
        ("narrator", 0.1, "safe", "Безпечно для Валорант і сі-ес ту."),
        ("narrator", 0.45, "outro", "Лагнікс. Безкоштовно. Посилання в профілі."),
    ],
}


# ------------------------------------------------------------------ синтез і ефекти

def read_wav(path: str) -> tuple[np.ndarray, int]:
    with wave.open(path, "rb") as w:
        data = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
        return data, w.getframerate()


def resample(x: np.ndarray, factor: float) -> np.ndarray:
    """Швидкість x factor (factor>1 — коротше й вище)."""
    n = int(len(x) / factor)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def synth(text: str, lang: str, who: str, length_scale: float, out: str) -> np.ndarray:
    model = os.path.join(VOICES, MODELS[lang][0] + ".onnx")
    cmd = [sys.executable, "-m", "piper", "-m", model, "-f", out, "--length-scale", str(length_scale),
           "--sentence-silence", "0.12"]
    speaker = SPEAKERS[who][lang]
    if speaker is not None:
        cmd += ["-s", str(speaker)]
    subprocess.run(cmd, input=text.encode("utf-8"), check=True, capture_output=True)
    x, sr = read_wav(out)
    return resample(x, sr / SR) if sr != SR else x


def trim(x: np.ndarray, thr: float = 0.012) -> np.ndarray:
    idx = np.where(np.abs(x) > thr)[0]
    return x[idx[0]:idx[-1] + 1] if len(idx) else x


def robot_voice(x: np.ndarray) -> np.ndarray:
    """Мила «роботизація»: легка кільцева модуляція + биткраш (зниження частоти й бітності)."""
    t = np.arange(len(x)) / SR
    ring = x * np.sin(2 * np.pi * 70 * t)
    y = x * 0.78 + ring * 0.22
    held = np.repeat(y[::3], 3)[:len(y)]                    # sample-hold ≈ 16 кГц
    levels = 2 ** 9
    crushed = np.round(held * levels) / levels
    y = y * 0.55 + crushed * 0.45
    # м'який «пластиковий» тембр: невеликий підйом верхів
    hf = y - np.convolve(y, np.ones(24) / 24, mode="same")
    return (y + hf * 0.35).astype(np.float32)


def make_voice(text: str, lang: str, who: str, tmp: str, speed: float = 0.97) -> np.ndarray:
    if who == "robot":
        pitch = 1.22                                        # +~3.5 півтонів
        x = synth(text, lang, who, pitch * speed, tmp)  # довше на етапі синтезу — після підвищення темп нормальний
        x = robot_voice(resample(trim(x), pitch))
    else:
        x = synth(text, lang, who, speed, tmp)
        x = trim(x)
    fade = int(0.01 * SR)
    x[:fade] *= np.linspace(0, 1, fade)
    x[-fade:] *= np.linspace(1, 0, fade)
    target_rms = 0.11 if who == "narrator" else 0.10        # ≈ -19 dBFS: зверху лишається місце для музики
    rms = float(np.sqrt(np.mean(x ** 2))) or 1.0
    return np.clip(x * target_rms / rms, -0.8, 0.8)


# ------------------------------------------------------------------ звукові ефекти

def tone(freq, dur, vol=0.3, shape="sine", decay=6.0):
    t = np.arange(int(dur * SR)) / SR
    f = np.broadcast_to(np.asarray(freq, np.float32), t.shape) if np.ndim(freq) else freq
    phase = 2 * np.pi * np.cumsum(f * np.ones_like(t)) / SR
    wave_ = np.sin(phase) if shape == "sine" else np.sign(np.sin(phase)) * 0.6
    env = np.exp(-t * decay) * np.minimum(1, t * 600)
    return (wave_ * env * vol).astype(np.float32)


def click(vol=0.35):
    n = int(0.012 * SR)
    rng = np.random.default_rng(3)
    noise = rng.standard_normal(n).astype(np.float32) * np.exp(-np.arange(n) / (SR * 0.0025))
    noise = noise - np.convolve(noise, np.ones(8) / 8, mode="same")        # лише верхи
    body = tone(1500, 0.05, vol * 0.35, decay=70)
    body[:n] += noise * vol * 0.5
    return body


def blip(vol=0.22):
    return np.concatenate([tone(880, 0.07, vol, "sq", 9), tone(1320, 0.09, vol, "sq", 9)])


def boop_up(vol=0.25):
    t = np.linspace(0, 1, int(0.22 * SR))
    return tone(500 + 900 * t ** 1.5, 0.22, vol, "sine", 4)


def jingle(vol=0.25):
    notes = (784, 988, 1175, 1568)
    return np.concatenate([tone(f, 0.11 if i < 3 else 0.28, vol, "sine", 5) for i, f in enumerate(notes)])


def pop(vol=0.28):
    t = np.linspace(0, 1, int(0.09 * SR))
    return tone(900 - 500 * t, 0.09, vol, "sine", 25)


def add(track: np.ndarray, at: float, sound: np.ndarray) -> None:
    i = int(at * SR)
    if i < 0 or i >= len(track):
        return
    seg = sound[:len(track) - i]
    track[i:i + len(seg)] += seg


# ------------------------------------------------------------------ збірка

def anchors(scenario: str, tl: dict, duration: float) -> dict:
    caps = {c[2]: c[0] for c in tl["captions"]}
    cues = tl["cues"]
    a = {"start": 0.0, "outro": cues["outro"], "happy": cues.get("happy", 0.0),
         "monitor_again": cues.get("monitor_again", 0.0), "safe": caps.get("safe", 0.0),
         "window_shown": tl["captions"][0][1] + 0.0}
    return a


def main(scenario: str, lang: str) -> None:
    tl = json.load(open(os.path.join(WORK, f"{scenario}_{lang}", "timeline.json"), encoding="utf-8"))
    name = NAMES[scenario]
    video = os.path.join(OUT_DIR, f"{name}_{lang}.mp4")
    duration = tl["cues"]["outro"] + 3.0
    track = np.zeros(int((duration + 0.2) * SR), np.float32)
    a = anchors(scenario, tl, duration)
    tmp = os.path.join(WORK, "_tts.wav")

    spans = []
    for who, off, anchor, text, *speed in LINES[(scenario, lang)]:
        x = make_voice(text, lang, who, tmp, *speed)
        start = a[anchor] + off
        add(track, start, x)
        spans.append((start, start + len(x) / SR, who, text))
        print(f"  {start:5.2f}–{start + len(x) / SR:5.2f} с  [{who}] {text}")
    for (s0, e0, *_), (s1, *_r) in zip(spans, spans[1:]):
        if e0 > s1 + 0.05:
            print(f"  УВАГА: репліка закінчується ({e0:.2f}) після початку наступної ({s1:.2f})")

    # --- біпи й кліки (тихіше за голос)
    for c in tl["clicks"]:
        add(track, c, click())
    add(track, a["window_shown"] + 0.0, pop(0.22))
    add(track, a["outro"] + 0.05, boop_up())                      # робот з'являється у фіналі
    if scenario == "sad_robot":
        add(track, a["window_shown"] + 0.42, blip(0.14))           # робот «озивається» перед фразою
        add(track, tl["cues"]["jerk"], pop(0.2))
        add(track, a["happy"], jingle())
    else:
        add(track, a["monitor_again"] - 0.05, blip(0.12))
        closed = tl["cues"]["closed"]
        for k in range(3):
            add(track, closed - 1.65 + k * 0.55, pop(0.24))       # програми зникають по одній
        add(track, tl["cues"]["game_tab"] + 0.35, boop_up(0.16))   # з'являється вікно підтвердження

    peak = float(np.max(np.abs(track))) or 1.0
    if peak > 0.9:
        track *= 0.9 / peak
    wav_path = os.path.join(WORK, f"{scenario}_{lang}_voice.wav")
    with wave.open(wav_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((track * 32767).astype(np.int16).tobytes())

    import imageio_ffmpeg
    out = os.path.join(OUT_DIR, f"{name}_{lang}_voice.mp4")
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", video, "-i", wav_path,
                    "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                    "-shortest", out], check=True)
    print(out)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
    else:
        main(sys.argv[1], sys.argv[2])
