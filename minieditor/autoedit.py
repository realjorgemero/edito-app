"""EDICIÓN AUTOMÁTICA estilo anuncio (8 oct 2026).

El equipo agrega los clips en orden; Edito hace el resto. Nace de comparar
9 anuncios del Mentor contra 6 de Influwa: el Mentor corta poco entre tomas
pero cada plano está vivo, sin pausas, con volumen alto y subtítulos que
acompañan cada palabra. Su editor lo hace solo; este también.

Por cada clip:
  1. whisper da los tiempos de cada palabra del CLIP (no del video final:
     acá necesitamos saber dónde están las pausas dentro de cada toma);
  2. se cortan las pausas internas mayores a PAUSA_MAX, dejando MARGEN de
     aire a cada lado para no comerse sílabas;
  3. cada tramo alterna encuadre normal / acercamiento PUNCH: el clásico
     jump cut de los anuncios, que además da el movimiento que faltaba.
     Determinista (par/impar), nada de random (regla 6 de AGENT.md).

Después el montaje es el de siempre (normalize → concat con cortes secos →
loudnorm → subtítulos subs-last → gates) y al final los controles de la
revisión final con las MISMAS palabras de los subtítulos (un solo whisper
sobre el video terminado).

Subtítulos: si INFLUWA mandó el guion (paquete), el texto que se ve es el
guion verbatim y whisper solo aporta los tiempos (ver captions.py). Sin
guion, se muestra lo que oyó whisper.

Hooks alternativos: el mismo cuerpo con distintos primeros clips da un video
por hook. Los clips del cuerpo se procesan UNA vez (caché por ruta).
"""

from __future__ import annotations

import difflib
import os
import shutil
import unicodedata

from . import asr, captions, concat, ff, gates, loudnorm, normalize, revision
from .edl import Boundary

PAUSA_MAX = 0.30      # una pausa más larga que esto se corta
MARGEN = 0.08         # aire que queda a cada lado del corte
COLA = 0.30           # aire después de la última palabra del clip
PUNCH = 1.08          # acercamiento de los tramos impares (8 %)
CORTE_S = 0.04        # "corte seco": un fundido de un frame entre tomas
MIN_TRAMO = 0.25      # tramos más cortos se pegan al anterior


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFD", text or "")
    return "".join(c for c in text.lower() if c.isalnum() and unicodedata.category(c) != "Mn")


def tramos_de_habla(words: list[dict], dur: float) -> list[tuple[float, float]]:
    """Los tramos del clip que se conservan: habla + MARGEN, sin pausas largas."""
    if not words:
        return [(0.0, dur)]
    tramos = []
    ini = max(0.0, words[0]["t0"] - MARGEN)
    for a, b in zip(words, words[1:]):
        if b["t0"] - a["t1"] > PAUSA_MAX:
            tramos.append((ini, a["t1"] + MARGEN))
            ini = max(0.0, b["t0"] - MARGEN)
    tramos.append((ini, min(dur, words[-1]["t1"] + COLA)))
    # Un tramo diminuto se ve como un parpadeo: se une al anterior.
    unidos: list[tuple[float, float]] = []
    for t in tramos:
        if unidos and (t[1] - t[0] < MIN_TRAMO):
            unidos[-1] = (unidos[-1][0], t[1])
        else:
            unidos.append(t)
    return unidos


def cortar_pausas(src: str, workdir: str, idx: int, punch: bool = True) -> tuple[str, dict]:
    """Corta las pausas internas del clip y alterna el acercamiento."""
    dur = ff.stream_duration(src, "video")
    words = asr.transcribe_words(src)
    tramos = tramos_de_habla(words, dur)
    quitado = dur - sum(b - a for a, b in tramos)
    if len(tramos) == 1 and quitado < 0.15 and not punch:
        return src, {"tramos": 1, "recortado_s": round(quitado, 2), "palabras": len(words)}

    v = next(s for s in ff.probe(src)["streams"] if s["codec_type"] == "video")
    w, h = int(v["width"]), int(v["height"])
    # El tramo con acercamiento vuelve EXACTO al tamaño del clip: concat no
    # acepta tramos que difieran aunque sea en 2 píxeles.
    zw, zh = (round(w * PUNCH / 2) * 2, round(h * PUNCH / 2) * 2)
    parts, labels = [], []
    for k, (a, b) in enumerate(tramos):
        zoom = f",scale={zw}:{zh},crop={w}:{h}" if (punch and k % 2 == 1) else ""
        parts.append(f"[0:v]trim={a:.3f}:{b:.3f},setpts=PTS-STARTPTS{zoom},setsar=1[v{k}]")
        fade_out = max(0.0, (b - a) - 0.012)
        parts.append(f"[0:a]atrim={a:.3f}:{b:.3f},asetpts=PTS-STARTPTS,"
                     f"afade=t=in:st=0:d=0.012,afade=t=out:st={fade_out:.3f}:d=0.012[a{k}]")
        labels.append(f"[v{k}][a{k}]")
    parts.append("".join(labels) + f"concat=n={len(tramos)}:v=1:a=1[v][a]")

    out = os.path.join(workdir, f"auto_{idx:03d}.mp4")
    ff.run(["-i", src, "-filter_complex", ";".join(parts), "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "256k", "-ar", "44100", "-ac", "2",
            "-pix_fmt", "yuv420p", out])
    return out, {"tramos": len(tramos), "recortado_s": round(quitado, 2), "palabras": len(words)}


def alinear_guion(oido: list[dict], guion: str) -> list[dict]:
    """El texto del guion con los tiempos de whisper.

    Se alinea palabra por palabra (difflib sobre la forma normalizada). Las
    palabras del guion que whisper no oyó toman tiempos repartidos entre sus
    vecinas alineadas. Si casi nada coincide (guion de otro video), se
    devuelve lo que oyó whisper: mejor eso que subtítulos desfasados."""
    escritas = [w for w in (guion or "").split() if w.strip()]
    if not oido or not escritas:
        return oido
    a = [_plain(w["w"]) for w in oido]
    b = [_plain(w) for w in escritas]
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    tiempos: list[tuple[float, float] | None] = [None] * len(escritas)
    iguales = 0
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                tiempos[j1 + k] = (oido[i1 + k]["t0"], oido[i1 + k]["t1"])
            iguales += i2 - i1
        elif op == "replace":
            # mismo tramo dicho con otras palabras: se reparte el tiempo
            t0, t1 = oido[i1]["t0"], oido[i2 - 1]["t1"]
            n = j2 - j1
            for k in range(n):
                tiempos[j1 + k] = (t0 + (t1 - t0) * k / n, t0 + (t1 - t0) * (k + 1) / n)
    if iguales < 0.5 * len(escritas):
        return oido
    # huecos (palabras que whisper no oyó): entre las vecinas con tiempo
    for j in range(len(escritas)):
        if tiempos[j] is None:
            prev = next((tiempos[k] for k in range(j - 1, -1, -1) if tiempos[k]), None)
            nxt = next((tiempos[k] for k in range(j + 1, len(escritas)) if tiempos[k]), None)
            t0 = prev[1] if prev else (nxt[0] - 0.2 if nxt else 0.0)
            t1 = nxt[0] if nxt else t0 + 0.2
            tiempos[j] = (t0, max(t1, t0 + 0.05))
    return [{"w": w, "t0": round(t[0], 3), "t1": round(max(t[1], t[0] + 0.05), 3)}
            for w, t in zip(escritas, tiempos)]


class Cache:
    """Clips ya procesados (cortar_pausas) por ruta: el cuerpo compartido por
    varios hooks se procesa una sola vez."""

    def __init__(self):
        self._d: dict[str, tuple[str, dict]] = {}

    def get(self, src: str, workdir: str, punch: bool) -> tuple[str, dict]:
        if src not in self._d:
            self._d[src] = cortar_pausas(src, workdir, len(self._d), punch=punch)
        return self._d[src]


def render(paths: list[str], out_path: str, workdir: str, cache: Cache,
           guiones: list[str] | None = None, producto: str = "",
           font: str = captions.FONT, accent: str = "#F4DC1A", position: str = "abajo",
           punch: bool = True, on_step=lambda msg: None) -> dict:
    """Un video completo a partir de clips en orden. Devuelve gates + revisión."""
    sub = os.path.join(workdir, os.path.splitext(os.path.basename(out_path))[0])
    os.makedirs(sub, exist_ok=True)

    on_step("Cortando pausas y armando los jump cuts…")
    procesados, info = [], []
    for p in paths:
        q, i = cache.get(p, workdir, punch)
        procesados.append(q)
        info.append(i)

    on_step("Normalizando canvas y voz…")
    normal = [normalize.normalize_shot(p, sub, i, fade_in=(i == 0), hook_punch=(i == 0))
              for i, p in enumerate(procesados)]

    on_step("Montando con cortes secos…")
    bounds = [Boundary(transition="fade", duration_s=CORTE_S) for _ in range(len(normal) - 1)]
    video, _cuts = concat.concat_shots(normal, bounds, sub)

    on_step("Volumen a -14 LUFS…")
    antes = loudnorm._measure(video)
    video = loudnorm.apply_loudnorm(video, sub)

    on_step("Subtítulos…")
    oido = asr.transcribe_words(video)
    guion = " ".join(g for g in (guiones or []) if g)
    words = alinear_guion(oido, guion) if guion else oido
    if words:
        ass = captions.build_ass(words, os.path.join(sub, "subs.ass"), accent=accent,
                                 font=font, position=position, pop=True)
        video = captions.burn(video, ass, sub)

    on_step("Verificando…")
    shutil.copyfile(video, out_path)
    dur = ff.stream_duration(out_path, "video")
    resultado_gates = gates.run_gates(out_path, check_hook_punch=True)
    despues = loudnorm._measure(out_path)
    rev = revision.controles_de_voz(
        out_path, dur, oido, producto,
        lufs_antes=float(antes["input_i"]) if antes else None,
        lufs_despues=float(despues["input_i"]) if despues else None)
    return {
        "gates": resultado_gates,
        "revision": rev,
        "duracion": round(dur, 2),
        "recortado_s": round(sum(i["recortado_s"] for i in info), 2),
        "cortes_internos": sum(max(0, i["tramos"] - 1) for i in info),
    }
