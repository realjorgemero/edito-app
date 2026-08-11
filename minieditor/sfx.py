"""PASO 4 – PISTA DE EFECTOS DE SONIDO.

Coloca sobre el montaje: el sting de apertura, un golpe por frontera y el
sting de cierre. Todo en UNA pasada de ffmpeg (un solo amix).

Los tres invariantes de sonido que hacen que esto suene "editado":

1. **El pico del SFX cae 40 ms ANTES del corte.**
   Un golpe exactamente encima del corte se lee como ruido; 40 ms antes se
   lee como acento. Por eso cada asset del catálogo trae `peak_offset_s`:
   dónde está su golpe DENTRO del archivo. La colocación es:

       start = cut_time - 0.040 - peak_offset_s

   Si tu stack no puede analizar audio, precomputá el pico una sola vez
   (ver assets/sfx/README.md) y guardalo junto al asset. El render nunca
   necesita DSP: solo resta dos números.

2. **Los buses se SUMAN, no se promedian** (amix con normalize=0).
   Las ganancias son absolutas: voz 1.0, SFX 0.82 (-1.7 dB), apertura 0.70
   (-3.1 dB), cierre 0.80 (-1.9 dB). La suma puede superar 1.0 – y está
   bien, PERO exige que el limitador (loudnorm con techo -1 dBTP) corra
   DESPUÉS de este paso. Nunca al revés.

3. **Declick en todos los SFX**: fade-in 30 ms, fade-out 120 ms.
   Un sample que arranca en seco hace click; uno que se corta en seco
   suena a error.

Mejora sobre el motor original: allí el sting de apertura se recortaba
desde el byte 0 sin alinear por pico – y su golpe estaba en el segundo
3.9, así que EL IMPACTO NUNCA SONABA. Acá la apertura también se alinea
por pico, con el golpe aterrizando en los primeros ~350 ms.
"""

from __future__ import annotations

import os

from . import ff
from .edl import Boundary, Edl

PREROLL_S = 0.040        # el pico cae 40 ms antes del corte
SFX_TAIL_S = 0.90        # cuánto SFX conservamos después de su pico
FADE_IN_S = 0.030
FADE_OUT_S = 0.120
OPENING_GAIN = 0.70      # -3.1 dB
OPENING_PEAK_AT_S = 0.35 # el golpe de apertura aterriza acá
CLOSING_GAIN = 0.80      # -1.9 dB
CLOSING_LEAD_S = 0.29    # el pico de cierre cae 290 ms antes del final


def _sfx_leg(idx: int, asset_peak_s: float, target_peak_s: float,
             gain: float, label: str) -> tuple[str, str]:
    """Construye la pata de filtro para UN efecto.

    Recorta el archivo ALREDEDOR de su pico, lo faddea, le pone ganancia
    y lo retrasa (adelay) para que el pico aterrice en target_peak_s.
    """
    trim_in = max(0.0, asset_peak_s - 0.85)          # ventana pre-pico
    peak_in_clip = asset_peak_s - trim_in
    clip_dur = peak_in_clip + SFX_TAIL_S
    start = max(0.0, target_peak_s - peak_in_clip)
    delay_ms = int(start * 1000)
    fade_out_at = max(0.0, clip_dur - FADE_OUT_S)

    f = (f"[{idx}:a]atrim=start={trim_in:.3f}:duration={clip_dur:.3f},"
         f"asetpts=PTS-STARTPTS,volume={gain:.3f},"
         f"afade=t=in:st=0:d={FADE_IN_S},"
         f"afade=t=out:st={fade_out_at:.3f}:d={FADE_OUT_S},"
         f"aresample=44100,adelay={delay_ms}|{delay_ms}[{label}]")
    return f, f"[{label}]"


def apply_sfx_track(video: str, edl: Edl, boundaries: list[Boundary],
                    cut_times: list[float], workdir: str) -> str:
    """Mezcla la pista de SFX sobre el montaje. Video se copia sin tocar."""
    total = ff.stream_duration(video, "video")
    legs: list[tuple[str, float, float, float]] = []  # (ruta, peak_offset, target, gain)

    if edl.opening_sting and os.path.exists(edl.opening_sting):
        legs.append((edl.opening_sting, _peak_of(edl.opening_sting),
                     OPENING_PEAK_AT_S, OPENING_GAIN))

    for b, cut in zip(boundaries, cut_times):
        if b.sfx and os.path.exists(b.sfx):
            legs.append((b.sfx, b.sfx_peak_offset_s, cut - PREROLL_S, b.sfx_gain))

    if edl.closing_sting and os.path.exists(edl.closing_sting):
        legs.append((edl.closing_sting, _peak_of(edl.closing_sting),
                     total - CLOSING_LEAD_S, CLOSING_GAIN))

    if not legs:
        return video

    filters, labels = [], []
    for i, (path, peak, target, gain) in enumerate(legs, start=1):
        f, lbl = _sfx_leg(i, peak, target, gain, f"s{i}")
        filters.append(f)
        labels.append(lbl)

    # Suma directa: voz a 1.0 + todos los SFX con su ganancia absoluta.
    # duration=first – la pista de SFX jamás alarga el video (el video es
    # el reloj maestro).
    mix = (f"[0:a]{''.join(labels)}amix=inputs={len(legs)+1}"
           f":duration=first:normalize=0[a]")
    filters.append(mix)

    out = os.path.join(workdir, "with_sfx.mp4")
    args = ["-i", video]
    for path, *_ in legs:
        args += ["-i", path]
    args += ["-filter_complex", ";".join(filters),
             "-map", "0:v", "-map", "[a]",
             "-c:v", "copy",
             "-c:a", "aac", "-b:a", "256k", "-ar", "44100", "-ac", "2",
             "-movflags", "+faststart", out]
    ff.run(args)
    return out


def _peak_of(path: str) -> float:
    """Pico del asset: primero busca un sidecar precomputado, si no, mide.

    El sidecar es `<asset>.peak` con un float en segundos. Generalo con
    `python -m minieditor.peaks` – así el render nunca hace DSP (y un stack
    sin análisis de audio puede usar el mismo catálogo).
    """
    sidecar = path + ".peak"
    if os.path.exists(sidecar):
        with open(sidecar) as f:
            return float(f.read().strip())
    try:
        from .peaks import measure_peak
        return measure_peak(path)
    except Exception:
        return 0.0   # fail-open: el SFX suena desde su inicio
