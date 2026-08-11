"""PASO 8 (opcional) – PORTADA INTELIGENTE.

TikTok/Reels usan por defecto un frame fijo del video como miniatura del
feed. En vez de confiar en que ESE instante caiga bien, elegimos el mejor
candidato dentro de la ventana donde la plataforma mira (primeros ~2.5s)
y lo exportamos aparte como JPG – vos lo subís a mano como portada
custom; el mini-editor nunca toca el MP4 por esto.

Heurística 100% determinista, sin dependencias nuevas: para cada
candidato medimos luminancia media (evita frames negros o quemados) y
varianza de luminancia (proxy de "detalle" – un frame nítido y con
contraste tiene más varianza que uno borroso o a mitad de una
transición). Gana el de mayor varianza entre los que no están ni muy
oscuros ni muy quemados.

Es un ORNAMENTO (invariante 4 de AGENT.md): si el análisis falla o no
hay ningún candidato válido, no hay portada y el render sigue – nunca
tumba el video final por esto.
"""

from __future__ import annotations

import os
import subprocess

from . import ff

CANDIDATE_TIMES_S = (0.5, 1.0, 1.5, 2.0, 2.5)
MIN_LUMA, MAX_LUMA = 25, 235   # descarta candidatos negros o quemados


def _frame_stats(path: str, at_s: float) -> tuple[float, float] | None:
    """(luma media, varianza) de UN frame a baja resolución, o None si
    ese instante no trajo nada (video más corto que el candidato)."""
    p = subprocess.run(
        [ff.FFMPEG, "-v", "quiet", "-ss", f"{at_s:.3f}", "-i", path,
         "-frames:v", "1", "-vf", "scale=64:-1,format=gray",
         "-f", "rawvideo", "-"],
        capture_output=True, timeout=60)
    if not p.stdout:
        return None
    n = len(p.stdout)
    mean = sum(p.stdout) / n
    variance = sum((b - mean) ** 2 for b in p.stdout) / n
    return mean, variance


def pick_cover(video: str, out_path: str, duration_s: float) -> str | None:
    """Elige el mejor frame entre CANDIDATE_TIMES_S y lo exporta a
    out_path (JPG, resolución completa). None si nada calificó."""
    best_t, best_score = None, -1.0
    for t in CANDIDATE_TIMES_S:
        if t >= duration_s:
            continue
        stats = _frame_stats(video, t)
        if not stats:
            continue
        mean, variance = stats
        if not (MIN_LUMA <= mean <= MAX_LUMA):
            continue
        if variance > best_score:
            best_score, best_t = variance, t

    if best_t is None:
        return None

    p = subprocess.run(
        [ff.FFMPEG, "-y", "-v", "quiet", "-ss", f"{best_t:.3f}", "-i", video,
         "-frames:v", "1", "-q:v", "2", out_path],
        capture_output=True, timeout=60)
    return out_path if p.returncode == 0 and os.path.exists(out_path) else None
