"""PASO 3 – MONTAJE: unir las tomas con transiciones.

Se hace en UNA sola pasada de ffmpeg con un filter_complex encadenado.
(El motor de producción del que nace este mini-editor concatena par a par
– N re-encodes, pérdida generacional que se acumula hacia el principio
del video, y coste O(n²). Una pasada elimina las tres cosas. Aprendé del
error, no lo repitas.)

⚠ Invariante D-07 – el más fácil de romper sin darte cuenta:
   UNA sola duración por frontera, compartida por el crossfade de VIDEO
   (xfade) y el de AUDIO (acrossfade). Si difieren 50 ms, cada frontera
   desplaza la pista de audio y el error SE ACUMULA: 6 fronteras × 50 ms
   = 300 ms, ya por encima del umbral de percepción de desincronía.
   Acá es la misma variable `b.duration_s` en los dos filtros.

⚠ La aritmética que hay que presupuestar:

   duración_total = Σ(tomas) - Σ(transiciones)

   La transición COME tiempo. 6 tomas con 5 transiciones de 0.4 s pierden
   2 s de metraje. Presupuestalo al escribir el guion.

⚠ La definición de "corte" (de acá cuelga TODO el sonido):

   cut_time = inicio_del_crossfade + duración/2

   El corte que el oído reconoce es el punto MEDIO del crossfade, no su
   inicio (suena tarde) ni su final (suena temprano).
"""

from __future__ import annotations

import os

from . import ff
from .edl import Boundary

# Gestos disponibles (xfade de ffmpeg). El slug es lo que va en el EDL.
TRANSITIONS = {
    "fade": "fade",              # neutro
    "fadegrays": "fadegrays",    # desaturado – nuestro fallback universal
    "fadewhite": "fadewhite",    # destello: para revelaciones
    "zoomin": "zoomin",          # zoom punch: para sorpresa/energía
    "circleopen": "circleopen",  # iris: para CTA/apertura de valor
    "distance": "distance",      # distorsión: para engaño/ruptura
}


def concat_shots(paths: list[str], boundaries: list[Boundary],
                 workdir: str) -> tuple[str, list[float]]:
    """Une los clips. Devuelve (ruta_resultado, cut_times).

    cut_times: el instante (en el timeline final) del punto medio de cada
    crossfade – el ancla donde el paso de SFX colgará los golpes.
    """
    if len(paths) == 1:
        return paths[0], []

    durs = [ff.stream_duration(p, "video") for p in paths]

    # Offsets encadenados: el crossfade i empieza donde termina el metraje
    # acumulado hasta el shot i, menos lo que ya comieron las transiciones
    # previas, menos la duración de esta transición.
    offsets, cut_times = [], []
    for i, b in enumerate(boundaries):
        off = sum(durs[: i + 1]) - sum(bb.duration_s for bb in boundaries[: i + 1])
        offsets.append(off)
        cut_times.append(off + b.duration_s / 2)   # ← punto MEDIO

    # filter_complex encadenado: video con xfade, audio con acrossfade,
    # SIEMPRE con la misma duración por frontera (invariante D-07).
    parts = []
    v_prev, a_prev = "[0:v]", "[0:a]"
    for i, b in enumerate(boundaries):
        kind = TRANSITIONS.get(b.transition, "fade")
        v_out = f"[v{i}]" if i < len(boundaries) - 1 else "[v]"
        a_out = f"[a{i}]" if i < len(boundaries) - 1 else "[a]"
        parts.append(
            f"{v_prev}[{i+1}:v]xfade=transition={kind}"
            f":duration={b.duration_s}:offset={offsets[i]:.3f}{v_out}"
        )
        parts.append(f"{a_prev}[{i+1}:a]acrossfade=d={b.duration_s}{a_out}")
        v_prev, a_prev = v_out, a_out

    out = os.path.join(workdir, "montage.mp4")
    args = []
    for p in paths:
        args += ["-i", p]
    args += ["-filter_complex", ";".join(parts),
             "-map", "[v]", "-map", "[a]",
             "-c:v", "libx264", "-preset", "fast", "-crf", "20",
             "-c:a", "aac", "-b:a", "256k", "-ar", "44100", "-ac", "2",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", out]
    ff.run(args)
    return out, cut_times
