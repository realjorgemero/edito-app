"""PASO 1 – RECORTE de aire muerto (cabeza y cola).

Los clips generados (Veo, Runway, o grabados) llegan con 1-3 s de silencio
al principio y al final. Ese "aire muerto" mata el ritmo del video.

El invariante que importa (LA FÓRMULA DEL AIRE):

    aire_conservado ≥ max(duración_de_transición) + margen

Nosotros dejamos 0.80 s porque nuestra transición más larga solapa ~0.6 s
la frontera. Si recortás más ajustado, el crossfade se come la última
palabra de una toma y la primera de la siguiente – y es el error más caro
del recorte automático porque es casi inaudible como error: se oye como si
el actor hablara "mezclado consigo mismo".

Parámetros asimétricos a propósito:

  · cola:   piso de ruido -28 dB (conservador: la respiración cuenta como
            sonido – no se corta habla por accidente)
  · cabeza: piso de ruido -20 dB (agresivo: la respiración cuenta como
            silencio – se entra cerca del habla)
"""

from __future__ import annotations

import os
import re

from . import ff

AIRE_S = 0.80            # aire conservado a cada lado (ver fórmula arriba)
MIN_TRIM_S = 0.22        # recortar menos que esto no vale el re-encode
TAIL_NOISE_DB = -28
HEAD_NOISE_DB = -20
TAIL_MIN_SILENCE_S = 0.30
HEAD_MIN_SILENCE_S = 0.15
HEAD_MAX_START_S = 0.45  # solo aceptamos silencio de cabeza que empiece aquí
FADE_IN_S = 0.12         # declick de entrada
FADE_OUT_S = 0.30        # declick de salida


def _silences(path: str, noise_db: int, min_s: float) -> list[tuple[float, float]]:
    """Corre silencedetect y devuelve [(inicio, fin), ...]."""
    err = ff.run(["-i", path, "-af",
                  f"silencedetect=noise={noise_db}dB:d={min_s}",
                  "-f", "null", "-"])
    starts = [float(m) for m in re.findall(r"silence_start: ([\d.]+)", err)]
    ends = [float(m) for m in re.findall(r"silence_end: ([\d.]+)", err)]
    return list(zip(starts, ends + [1e9] * (len(starts) - len(ends))))


def decide_cut(path: str) -> tuple[float, float, str]:
    """Devuelve (head_cut_s, keep_end_s, motivo)."""
    total = ff.stream_duration(path, "audio") or ff.stream_duration(path, "video")

    # CABEZA: si hay un bloque de silencio que empieza cerca de t=0,
    # entramos AIRE_S antes de que termine.
    head_cut = 0.0
    for s, e in _silences(path, HEAD_NOISE_DB, HEAD_MIN_SILENCE_S):
        if s <= HEAD_MAX_START_S and e < total:
            head_cut = max(0.0, e - AIRE_S)
            break

    # COLA: el ÚLTIMO bloque de silencio que llega (casi) hasta el final.
    keep_end = total
    for s, e in _silences(path, TAIL_NOISE_DB, TAIL_MIN_SILENCE_S):
        if e >= total - 0.10:            # el silencio toca el final del clip
            keep_end = min(total, s + AIRE_S)

    motivo = []
    if head_cut < MIN_TRIM_S:
        head_cut, m = 0.0, "cabeza_sin_recorte"
    else:
        m = f"cabeza_-{head_cut:.2f}s"
    motivo.append(m)

    if (total - keep_end) < MIN_TRIM_S:
        keep_end, m = total, "cola_sin_recorte"
    else:
        m = f"cola_-{total - keep_end:.2f}s"
    motivo.append(m)

    return head_cut, keep_end, " ".join(motivo)


def manual_trim(src: str, workdir: str, index: int, start_s: float,
                end_s: float | None) -> str:
    """Recorte MANUAL: el usuario ya eligió el rango en el navegador (ver
    el panel de recorte por toma en app.py). No corre detección de
    silencio – corta [start_s, end_s] tal cual, con el mismo declick que
    el recorte automático para que el borde no truene. end_s=None deja el
    final del clip intacto (el usuario solo movió el inicio)."""
    out = os.path.join(workdir, f"mtrim_{index:03d}.mp4")
    args = []
    if start_s > 0:
        # ⚠ mismo trade-off que trim_shot: -ss antes de -i busca por
        # keyframe, no es frame-exacto.
        args += ["-ss", f"{start_s:.3f}"]
    args += ["-i", src]
    if end_s is not None:
        dur = max(0.05, end_s - start_s)
        fade_start = max(0.0, dur - FADE_OUT_S)
        args += ["-t", f"{dur:.3f}",
                 "-af", f"afade=t=in:st=0:d={FADE_IN_S},"
                        f"afade=t=out:st={fade_start:.3f}:d={FADE_OUT_S}"]
    else:
        args += ["-af", f"afade=t=in:st=0:d={FADE_IN_S}"]
    args += ["-c:v", "libx264", "-preset", "fast", "-crf", "20",
             "-c:a", "aac", "-ar", "44100", "-ac", "2",
             "-pix_fmt", "yuv420p", out]
    ff.run(args)
    return out


def trim_shot(src: str, workdir: str, index: int) -> tuple[str, float]:
    """Recorta un clip. Devuelve (ruta_resultante, head_cut_aplicado).

    head_cut se devuelve porque los word_timings del EDL son relativos al
    clip SIN recortar: quien los use después debe restarles head_cut.
    """
    head_cut, keep_end, motivo = decide_cut(src)
    if head_cut == 0.0 and motivo.endswith("cola_sin_recorte"):
        return src, 0.0                   # nada que recortar: ni un re-encode

    out = os.path.join(workdir, f"trim_{index:03d}.mp4")
    dur = keep_end - head_cut
    fade_start = max(0.0, dur - FADE_OUT_S)

    args = []
    if head_cut > 0:
        # ⚠ -ss antes de -i = seek por keyframe, NO frame-exacto. Para un
        # mini-editor alcanza; si necesitás precisión, poné -ss tras -i.
        args += ["-ss", f"{head_cut:.3f}"]
    args += ["-i", src, "-t", f"{dur:.3f}",
             "-af", f"afade=t=in:st=0:d={FADE_IN_S},"
                    f"afade=t=out:st={fade_start:.3f}:d={FADE_OUT_S}",
             "-c:v", "libx264", "-preset", "fast", "-crf", "20",
             "-c:a", "aac", "-ar", "44100", "-ac", "2",
             "-pix_fmt", "yuv420p", out]
    ff.run(args)
    print(f"  · shot {index}: {motivo}")
    return out, head_cut
