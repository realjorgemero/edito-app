#!/usr/bin/env python3
"""Genera un proyecto de demostración COMPLETO, sin descargar nada.

Crea en examples/:

  · 3 clips sintéticos de "avatar" (color + texto + voz simulada con tonos,
    con aire muerto real al principio y al final, para que el recorte
    tenga trabajo que hacer)
  · 2 SFX sintetizados (whoosh + impacto) con su sidecar .peak
  · edl.json apuntando a todo lo anterior, con word_timings de un guion
    de ejemplo

Después de correr esto:

    python pipeline.py examples/edl.json -o demo.mp4
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

# La consola de Windows usa por defecto un codepage (cp1252) que no sabe
# imprimir los símbolos que usamos en los mensajes (→, ·, …). Sin esto,
# el primer print() con un símbolo así revienta con UnicodeEncodeError.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EX = os.path.join(ROOT, "examples")
sys.path.insert(0, ROOT)

from minieditor.peaks import measure_peak  # noqa: E402

CLIP_DUR = 6.0
SIL_HEAD = 1.2   # aire muerto real en cada clip
SIL_TAIL = 1.2

# drawtext necesita una fuente explícita: muchos builds de ffmpeg para
# Windows traen fontconfig habilitado pero sin fonts.conf configurado, y
# sin fontfile fallan con "Fontconfig error: Cannot load default config
# file". El ':' de "C:/Windows/Fonts/..." además choca con el separador
# de opciones del filtergraph (el mismo problema que resuelve
# minieditor/captions.py) – por eso va escapado.
_FONT_CANDIDATES = [
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]
FONT_FILE = next((f for f in _FONT_CANDIDATES if os.path.exists(f)), None)

CLIPS = [
    ("0x1B4F72", "TOMA 1 · HOOK", 220),
    ("0x7D3C98", "TOMA 2 · PROBLEMA", 300),
    ("0x148F77", "TOMA 3 · CTA", 260),
]

# Guion de ejemplo por toma. Los tiempos simulan el habla dentro de la
# ventana con sonido [SIL_HEAD, CLIP_DUR - SIL_TAIL] del clip FUENTE.
SCRIPTS = [
    "Nadie te contó esto sobre editar videos",
    "El problema no es tu cámara, es tu montaje",
    "Descarga la receta y empieza sin excusas hoy",
]


def run(cmd: list[str]) -> None:
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        sys.exit(f"fallo: {' '.join(cmd)}\n{p.stderr[-800:]}")


def make_clip(path: str, color: str, label: str, freq: int) -> None:
    """Video de color con texto + 'voz' de tono modulado y silencios."""
    speech_start, speech_end = SIL_HEAD, CLIP_DUR - SIL_TAIL
    # tono modulado en amplitud (simula ritmo de habla), mudo fuera de la
    # ventana de "habla"
    audio = (f"sine=frequency={freq}:duration={CLIP_DUR},"
             f"volume=5,"          # la fuente sine de ffmpeg es muy baja
             f"tremolo=f=3:d=0.8,"
             f"volume=0:enable='lt(t,{speech_start})+gt(t,{speech_end})'")
    vf = "null"  # sin fuente disponible: el clip queda sin etiqueta (fail-open)
    if FONT_FILE:
        fontfile = FONT_FILE.replace(":", "\\:")
        vf = (f"drawtext=text='{label}':fontfile='{fontfile}':fontsize=48:"
              "fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2")

    run(["ffmpeg", "-y", "-f", "lavfi",
         "-i", f"color=c={color}:s=540x960:d={CLIP_DUR}:r=30",
         "-f", "lavfi", "-i", audio,
         "-vf", vf,
         "-c:v", "libx264", "-preset", "fast", "-crf", "23",
         "-c:a", "aac", "-ar", "44100", "-ac", "2",
         "-pix_fmt", "yuv420p", "-shortest", path])


def make_sfx() -> tuple[str, str]:
    """Whoosh (barrido) e impacto (golpe con cola), sintetizados."""
    whoosh = os.path.join(EX, "sfx", "whoosh.wav")
    impact = os.path.join(EX, "sfx", "impact.wav")
    os.makedirs(os.path.dirname(whoosh), exist_ok=True)

    # whoosh: ruido filtrado con swell – pico cerca del final
    run(["ffmpeg", "-y", "-f", "lavfi",
         "-i", "anoisesrc=d=1.0:c=pink:a=0.8",
         "-af", "lowpass=f=1200,afade=t=in:st=0:d=0.55,afade=t=out:st=0.75:d=0.25",
         "-ar", "44100", whoosh])

    # impacto: golpe seco con cola que decae – pico al inicio
    run(["ffmpeg", "-y", "-f", "lavfi",
         "-i", "sine=frequency=80:duration=0.9",
         "-af", "volume=1.2,afade=t=out:st=0.05:d=0.85",
         "-ar", "44100", impact])

    for f in (whoosh, impact):
        peak = measure_peak(f)
        with open(f + ".peak", "w") as fh:
            fh.write(f"{peak:.3f}")
        print(f"  · {os.path.basename(f)}: pico en {peak:.3f}s")

    return whoosh, impact


def word_timings(script: str) -> list[dict]:
    """Reparte las palabras del guion en la ventana de 'habla' del clip."""
    words = script.split()
    t0, t1 = SIL_HEAD + 0.15, CLIP_DUR - SIL_TAIL - 0.15
    step = (t1 - t0) / len(words)
    return [{"w": w, "t0": round(t0 + i * step, 3),
             "t1": round(t0 + (i + 1) * step - 0.05, 3)}
            for i, w in enumerate(words)]


def main() -> None:
    os.makedirs(os.path.join(EX, "clips"), exist_ok=True)

    print("generando clips…")
    clip_paths = []
    for i, (color, label, freq) in enumerate(CLIPS):
        p = os.path.join(EX, "clips", f"toma_{i}.mp4")
        make_clip(p, color, label, freq)
        clip_paths.append(p)
        print(f"  · {os.path.basename(p)}")

    print("sintetizando SFX…")
    whoosh, impact = make_sfx()

    edl = {
        "canvas_w": 1080, "canvas_h": 1920, "fps": 30,
        "captions": True,
        "caption_accent": "#F4DC1A",
        "opening_sting": os.path.relpath(impact, ROOT),
        "closing_sting": os.path.relpath(impact, ROOT),
        "shots": [
            {"source": os.path.relpath(p, ROOT), "script": SCRIPTS[i],
             "word_timings": word_timings(SCRIPTS[i])}
            for i, p in enumerate(clip_paths)
        ],
        "boundaries": [
            {"transition": "fadewhite", "duration_s": 0.35,
             "sfx": os.path.relpath(impact, ROOT),
             "sfx_peak_offset_s": measure_peak(impact), "sfx_gain": 0.82},
            {"transition": "zoomin", "duration_s": 0.32,
             "sfx": os.path.relpath(whoosh, ROOT),
             "sfx_peak_offset_s": measure_peak(whoosh), "sfx_gain": 0.82},
        ],
    }

    edl_path = os.path.join(EX, "edl.json")
    with open(edl_path, "w", encoding="utf-8") as f:
        json.dump(edl, f, ensure_ascii=False, indent=2)

    print(f"listo → {os.path.relpath(edl_path, ROOT)}")
    print("ahora:  python pipeline.py examples/edl.json -o demo.mp4")


if __name__ == "__main__":
    main()
