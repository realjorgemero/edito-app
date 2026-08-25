"""PASO 7 – GATES DE ACEPTACIÓN.

La lección más cara del motor original: se entregaron ediciones MUDAS
marcadas como "done", porque la métrica de cobertura contaba entradas de
un pre-paso y nunca abría el MP4 final.

    Una métrica que se calcula sin mirar el resultado es una métrica
    que miente.

Por eso los gates miden SIEMPRE sobre el entregable final, y por eso se
construyen ANTES que cualquier efecto nuevo. Si agregás una feature, el
gate te dice al instante si rompiste algo lejos.
"""

from __future__ import annotations

import json
import re
import subprocess

from . import ff
from .loudnorm import CEILING_TP, TARGET_I


def _rms_db(path: str, start: float, dur: float) -> float:
    """RMS (dB) de una ventana de audio del archivo."""
    err = ff.run(["-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", path,
                  "-af", "volumedetect", "-vn", "-f", "null", "-"])
    m = re.search(r"mean_volume: ([-\d.]+) dB", err)
    return float(m.group(1)) if m else -99.0


def _luma(path: str, at_s: float) -> float:
    """Luminancia media (0-255) de UN frame, tomado a at_s segundos."""
    p = subprocess.run(
        [ff.FFMPEG, "-v", "quiet", "-ss", f"{at_s:.3f}", "-i", path,
         "-frames:v", "1", "-vf", "scale=64:-1,format=gray",
         "-f", "rawvideo", "-"],
        capture_output=True, timeout=60, creationflags=ff.NO_WINDOW_FLAGS)
    return sum(p.stdout) / len(p.stdout) if p.stdout else 0.0


def run_gates(path: str, expected_duration_s: float | None = None,
              cut_times: list[float] | None = None,
              check_edges: bool = False, check_hook_punch: bool = False) -> dict:
    """check_edges: activalo solo si el render incluye stings de apertura
    y cierre – sin diseño sonoro, el aire conservado por el recorte hace
    que el primer medio segundo sea silencio A PROPÓSITO, y un gate que
    lo marque como error sería una falsa alarma. Igual con cut_times:
    pasá solo las fronteras que llevan SFX."""
    results: dict[str, dict] = {}

    def check(name: str, ok: bool, detail: str):
        results[name] = {"ok": bool(ok), "detail": detail}

    info = ff.probe(path)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), {})

    # G1 – contenedor: canvas, fps, pix_fmt
    check("G1_contenedor",
          v.get("width") == 1080 and v.get("height") == 1920
          and v.get("r_frame_rate") == "30/1" and v.get("pix_fmt") == "yuv420p",
          f"{v.get('width')}x{v.get('height')} @{v.get('r_frame_rate')} {v.get('pix_fmt')}")

    # G2 – loudness, medido sobre EL ENTREGABLE (no un intermedio).
    # El techo de true peak que se verifica es el de PLATAFORMA (-1.0),
    # no el target interno del loudnorm (que apunta más abajo a propósito).
    err = ff.run(["-i", path, "-af",
                  f"loudnorm=I={TARGET_I}:TP={CEILING_TP}:LRA=11:print_format=json",
                  "-vn", "-f", "null", "-"])
    blocks = re.findall(r"\{[^{}]+\}", err)
    if blocks:
        m = json.loads(blocks[-1])
        try:
            i_val, tp = float(m["input_i"]), float(m["input_tp"])
            check("G2_loudness",
                  (TARGET_I - 1.0) <= i_val <= (TARGET_I + 1.0) and tp <= CEILING_TP + 0.05,
                  f"{i_val:.1f} LUFS, TP {tp:.1f} dB (target {TARGET_I} / techo {CEILING_TP})")
        except ValueError:
            check("G2_loudness", False, "medición inválida (inf/nan)")
    else:
        check("G2_loudness", False, "no se pudo medir")

    # G3 – sincronía A/V: duración de STREAM, nunca format.duration
    dv = ff.stream_duration(path, "video")
    da = ff.stream_duration(path, "audio")
    check("G3_sync_av", abs(dv - da) <= 0.12, f"|V-A| = {abs(dv-da)*1000:.0f} ms")

    # G4 – duración vs plan (Σtomas - Σtransiciones)
    if expected_duration_s:
        check("G4_duracion", abs(dv - expected_duration_s) <= 0.25,
              f"real {dv:.2f}s vs plan {expected_duration_s:.2f}s")

    # G5/G6 – la pieza no empieza ni termina muda (solo con diseño sonoro)
    if check_edges:
        check("G5_apertura_no_muda", _rms_db(path, 0.0, 0.5) > -60,
              f"RMS[0,0.5s] = {_rms_db(path, 0.0, 0.5):.1f} dB")
        check("G6_cierre_no_mudo", _rms_db(path, max(0, dv - 0.5), 0.5) > -60,
              f"RMS últimos 0.5s = {_rms_db(path, max(0, dv-0.5), 0.5):.1f} dB")

    # G7 – portada no negra (frame a 1000 ms, el que usa TikTok por defecto)
    luma = _luma(path, 1.0)
    check("G7_portada_no_negra", luma > 22, f"luminancia media {luma:.0f}/255")

    # G9 – hook punch: el crop del zoom de apertura no debe aterrizar en
    # un fotograma negro/roto (t=0.2s: ya pasó el fade-in de 0.15s y sigue
    # dentro de la ventana de punch de 0.35s – ver normalize.py)
    if check_hook_punch:
        hp_luma = _luma(path, 0.2)
        check("G9_hook_punch", hp_luma > 15, f"luminancia media {hp_luma:.0f}/255")

    # G8 – energía en cada frontera con SFX
    if cut_times:
        quiet = [f"{c:.2f}s" for c in cut_times
                 if _rms_db(path, max(0, c - 0.2), 0.4) <= -55]
        check("G8_energia_fronteras", not quiet,
              "todas suenan" if not quiet else f"mudas en: {quiet}")

    return results


def print_report(results: dict) -> bool:
    all_ok = True
    for name, r in results.items():
        mark = "✅" if r["ok"] else "❌"
        print(f"  {mark} {name}: {r['detail']}")
        all_ok = all_ok and r["ok"]
    return all_ok
