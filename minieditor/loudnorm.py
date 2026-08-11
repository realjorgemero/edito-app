"""PASO 5 – LOUDNESS (EBU R128, dos pasadas).

El invariante #1 de todo el sistema:

    -14.0 LUFS integrados · techo -1.0 dBTP · AL FINAL de todo el audio.

Por qué importa: es el estándar operativo de TikTok/IG/YouTube. Un anuncio
a -20 LUFS suena "apagado" al lado del siguiente video del feed, y se
salta. No es un detalle de mezcla: es competitividad en el feed.

Por qué DOS pasadas: una sola pasada de loudnorm es un normalizador
dinámico que "respira" y modula la voz. Dos pasadas = medir primero,
aplicar después una corrección lineal. Es la diferencia entre
"normalizado" y "procesado".

Por qué AL FINAL: cualquier cambio de tiempo posterior (velocidad, otro
mix) invalida la medición. En el motor de producción esto está roto – el
loudnorm corre antes del cambio de velocidad y nunca se re-mide, así que
todo render acelerado sale fuera de target. Acá el orden es correcto.

Por qué el techo -1 dBTP: el encode AAC introduce picos inter-muestra que
no existían en PCM. El margen no es paranoia, es lo que AAC necesita.
"""

from __future__ import annotations

import json
import os
import re

from . import ff

TARGET_I = -14.0
TARGET_LRA = 11.0
CEILING_TP = -1.0    # el techo REAL de plataforma (lo verifica el gate G2)
TARGET_TP = -1.5     # apuntamos 0.5 dB POR DEBAJO del techo: el encode AAC
                     # posterior introduce picos inter-muestra que no existían
                     # en PCM, y sin ese margen el entregable queda rozando
                     # (o pasando) el techo. Medido en este mismo repo:
                     # apuntando a -1.0, el MP4 final salía a -0.9.


def _measure(path: str) -> dict | None:
    err = ff.run(["-i", path, "-af",
                  f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}"
                  ":print_format=json",
                  "-vn", "-f", "null", "-"])
    m = re.findall(r"\{[^{}]+\}", err)
    if not m:
        return None
    data = json.loads(m[-1])
    # Clips muy cortos o silenciosos miden inf/nan – mejor no normalizar
    # que envenenar la segunda pasada.
    for k in ("input_i", "input_tp", "input_lra", "input_thresh"):
        try:
            float(data[k])
        except (KeyError, ValueError):
            return None
    return data


def apply_loudnorm(video: str, workdir: str) -> str:
    """Mide y aplica loudnorm lineal. Video se copia sin re-encodear."""
    m = _measure(video)
    out = os.path.join(workdir, "loudnorm.mp4")
    af = (f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}")
    if m:
        af += (f":measured_I={float(m['input_i'])}"
               f":measured_TP={float(m['input_tp'])}"
               f":measured_LRA={float(m['input_lra'])}"
               f":measured_thresh={float(m['input_thresh'])}"
               f":offset={float(m.get('target_offset', 0))}"
               f":linear=true")
    ff.run(["-i", video, "-c:v", "copy", "-af", af,
            "-c:a", "aac", "-b:a", "256k", "-ar", "44100", "-ac", "2",
            "-movflags", "+faststart", out])
    return out
