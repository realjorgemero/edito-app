#!/usr/bin/env python3
"""mini-editor – pipeline de edición de video publicitario vertical.

Uso:
    python pipeline.py examples/edl.json -o final.mp4

El orden de los pasos NO es negociable en tres puntos (ver README):
  · la limpieza de voz va ANTES de sumar cualquier SFX
  · el loudnorm es el ÚLTIMO paso de audio
  · los subtítulos se calculan contra el timeline FINAL y se queman al final
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile

# La consola de Windows usa por defecto un codepage (cp1252) que no sabe
# imprimir los símbolos que usamos en los mensajes (→, ✅, ⚠). Sin esto,
# el primer print() con un símbolo así revienta con UnicodeEncodeError.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from minieditor import captions, concat, gates, loudnorm, normalize, sfx, thumbnail, trim
from minieditor.edl import Edl
from minieditor.ff import stream_duration


def render(edl_path: str, out_path: str, keep_workdir: bool = False,
           auto_subs: bool = False) -> bool:
    edl = Edl.load(edl_path)
    workdir = tempfile.mkdtemp(prefix="miniedit_")
    # ⚠ En contenedores, /tmp puede ser tmpfs: tus temporales son MEMORIA.
    # Este mini-editor genera pocos intermedios, pero tenelo presente.
    print(f"workdir: {workdir}")

    try:
        # ── PASO 1 · RECORTE de aire muerto ─────────────────────────
        print("1/7 recorte de tomas")
        paths, head_cuts = [], []
        for i, shot in enumerate(edl.shots):
            if shot.trim:
                p, hc = trim.trim_shot(shot.source, workdir, i)
            else:
                p, hc = shot.source, 0.0
            paths.append(p)
            head_cuts.append(hc)

        # ── PASO 2 · NORMALIZAR canvas/fps + limpiar voz ────────────
        print("2/7 normalización de canvas + cadena de voz")
        paths = [normalize.normalize_shot(p, workdir, i, edl.canvas_w,
                                          edl.canvas_h, edl.fps, fade_in=(i == 0),
                                          hook_punch=(i == 0 and edl.hook_punch))
                 for i, p in enumerate(paths)]
        durs = [stream_duration(p, "video") for p in paths]

        # ── PASO 3 · MONTAJE con transiciones ───────────────────────
        print("3/7 montaje (xfade, una sola pasada)")
        video, cut_times = concat.concat_shots(paths, edl.boundaries, workdir)
        expected = sum(durs) - sum(b.duration_s for b in edl.boundaries)

        # ── PASO 4 · PISTA DE SFX ────────────────────────────────────
        print("4/7 pista de efectos de sonido")
        video = sfx.apply_sfx_track(video, edl, edl.boundaries, cut_times, workdir)

        # ── PASO 5 · LOUDNESS (último paso de AUDIO, siempre) ───────
        print("5/7 loudnorm dos pasadas (-14 LUFS / -1 dBTP)")
        video = loudnorm.apply_loudnorm(video, workdir)

        # ── PASO 6 · SUBTÍTULOS (lo último de todo) ──────────────────
        if edl.captions:
            words = _timeline_words(edl, head_cuts, durs)
            if not words and auto_subs:
                # Subs-last: transcribir el video FINAL (timestamps ya
                # absolutos, deriva cero). Ver minieditor/asr.py.
                from minieditor import asr
                print("6/7 transcribiendo con whisper (primera vez descarga el modelo)…")
                words = asr.transcribe_words(video)
            if words:
                print(f"6/7 subtítulos ({len(words)} palabras)")
                ass = captions.build_ass(
                    words, os.path.join(workdir, "subs.ass"),
                    edl.canvas_w, edl.canvas_h, edl.caption_accent)
                video = captions.burn(video, ass, workdir)
            else:
                print("6/7 subtítulos: sin word_timings y sin --auto-subs – se omiten")
        else:
            print("6/7 subtítulos: desactivados en el EDL")

        # ── PASO 7 · GATES sobre el ENTREGABLE ──────────────────────
        print("7/7 gates de aceptación")
        sfx_cuts = [c for c, b in zip(cut_times, edl.boundaries) if b.sfx]
        results = gates.run_gates(
            video, expected_duration_s=expected, cut_times=sfx_cuts or None,
            check_edges=bool(edl.opening_sting or edl.closing_sting),
            check_hook_punch=edl.hook_punch)
        ok = gates.print_report(results)

        # ── PASO 8 (opcional) · PORTADA sugerida ────────────────────
        # Ornamento: si falla, no se lleva puesto el render principal.
        try:
            cover = thumbnail.pick_cover(
                video, os.path.splitext(out_path)[0] + "_cover.jpg",
                stream_duration(video, "video"))
            if cover:
                print(f"   portada sugerida → {cover}")
        except Exception:
            pass

        shutil.copy2(video, out_path)
        print(("✅ OK → " if ok else "⚠ terminado CON GATES EN ROJO → ") + out_path)
        return ok
    finally:
        if keep_workdir:
            print(f"(workdir conservado: {workdir})")
        else:
            shutil.rmtree(workdir, ignore_errors=True)


def _timeline_words(edl: Edl, head_cuts: list[float],
                    durs: list[float]) -> list[dict]:
    """Mapea los word_timings de cada shot al timeline FINAL.

    El invariante de producción es alinear contra el AUDIO FINAL (la
    deriva de recortes+transiciones es acumulativa y ningún offset fijo
    la compensa). Aquí lo aproximamos con aritmética exacta: mismo número
    que usa el montaje, así la deriva teórica es ~0. Para clips reales
    con recorte por keyframe, el reto 1 (whisper sobre el video final)
    es la versión pro.
    """
    words = []
    for i, shot in enumerate(edl.shots):
        # inicio del shot i en el timeline final:
        offset = sum(durs[:i]) - sum(b.duration_s for b in edl.boundaries[:i])
        for w in shot.word_timings:
            t0 = w["t0"] - head_cuts[i]
            t1 = w["t1"] - head_cuts[i]
            if t1 <= 0 or t0 >= durs[i]:
                continue                      # la palabra cayó en el recorte
            words.append({"w": w["w"],
                          "t0": max(0.0, t0) + offset,
                          "t1": min(durs[i], t1) + offset})
    words.sort(key=lambda x: x["t0"])
    return words


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("edl", help="ruta al EDL (JSON)")
    ap.add_argument("-o", "--out", default="final.mp4")
    ap.add_argument("--keep-workdir", action="store_true")
    ap.add_argument("--auto-subs", action="store_true",
                    help="transcribir con whisper si el EDL no trae word_timings")
    args = ap.parse_args()
    sys.exit(0 if render(args.edl, args.out, args.keep_workdir, args.auto_subs) else 1)
