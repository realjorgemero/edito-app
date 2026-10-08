"""REVISIÓN FINAL – el último control antes de publicar un video que se
editó AFUERA de Edito (CapCut, por ejemplo).

Nace de comparar 6 videos de Influwa contra 9 anuncios del Mentor
(8 oct 2026). Lo que se veía distinto y se puede medir sin abrir el video:

- VOLUMEN: los del Mentor estaban entre -10 y -14 LUFS; los nuestros entre
  -17 y -21. En el feed eso suena "apagado" (ver loudnorm.py). Acá NO solo
  se avisa: se corrige, y el MP4 que sale ya está a -14 LUFS.
- CIERRE SIN PEDIDO: uno de los videos terminaba con "espero que tenga un día
  tranquilo". Si en los últimos segundos no se pide la compra, se marca.
- NOMBRE DEL PRODUCTO: la voz de la IA lo pronunciaba de cuatro formas. Si
  whisper no lo reconoce, probablemente la gente tampoco.
- SILENCIO AL ARRANQUE y PAUSAS LARGAS: cada pausa es una salida del video.

Lo que NO se puede medir acá (subtítulos que arrancan tarde, rótulos de
escena quemados en la imagen) queda como lista para revisar a ojo en la
interfaz. Mismo principio que gates.py: todo se mide sobre el MP4 FINAL.
"""

from __future__ import annotations

import difflib
import os
import re
import shutil
import unicodedata

from . import asr, ff
from .gates import _luma
from .loudnorm import TARGET_I, _measure, apply_loudnorm

# Ventana final donde tiene que aparecer el pedido de compra.
VENTANA_CIERRE_S = 8.0
# Formas de pedir la compra o quitar el riesgo (tú, vos y usted).
CTA = re.compile(
    r"\b(pid[ae]|pidal[oa]|pedil[oa]|pedi|pedir|ped[ií]|orden[aáe]|compr[aáe]\w*|"
    r"enlace|link|abajo|contra ?entrega|paga\w* (al|cuando)|pague (al|cuando)|"
    r"llega a (su|tu) casa|entr[aáe] (al|aqu[ií])|escrib\w*)\b"
)


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFD", text or "")
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9 ]", "", text.lower())


def _mejor_parecido(producto: str, palabras: list[str]) -> tuple[float, str]:
    """Qué tan parecido suena el nombre del producto a lo que oyó whisper.
    Compara contra grupos de 1 a 3 palabras seguidas, sin espacios: así
    "Max Calm" y "MaxCalm" cuentan igual."""
    meta = _plain(producto).replace(" ", "")
    if not meta:
        return 1.0, ""
    limpias = [_plain(p) for p in palabras]
    mejor, oido = 0.0, ""
    for n in (1, 2, 3):
        for i in range(len(limpias) - n + 1):
            junto = "".join(limpias[i:i + n])
            r = difflib.SequenceMatcher(None, meta, junto).ratio()
            if r > mejor:
                mejor, oido = r, " ".join(palabras[i:i + n])
    return mejor, oido


def revisar(video: str, out_dir: str, workdir: str, producto: str = "",
            on_step=lambda msg: None) -> dict:
    """Corrige el volumen, transcribe y revisa. Deja `final.mp4` en out_dir."""
    checks: list[dict] = []

    def check(nombre: str, ok: bool, detalle: str, nivel: str = "aviso"):
        checks.append({"nombre": nombre, "ok": bool(ok), "nivel": nivel, "detalle": detalle})

    on_step("Midiendo el volumen…")
    antes = _measure(video)
    lufs_antes = float(antes["input_i"]) if antes else None

    on_step("Dejando el volumen en -14 LUFS…")
    normalizado = apply_loudnorm(video, workdir)
    final = os.path.join(out_dir, "final.mp4")
    shutil.copyfile(normalizado, final)
    despues = _measure(final)
    lufs_despues = float(despues["input_i"]) if despues else None
    if lufs_antes is None:
        check("Volumen", False, "No se pudo medir el volumen (¿el video tiene audio?).", "error")
    else:
        corregido = lufs_despues is not None and abs(lufs_despues - TARGET_I) <= 1.0
        check("Volumen", corregido,
              f"Venía en {lufs_antes:.1f} LUFS; el MP4 que descargás queda en "
              f"{lufs_despues:.1f} LUFS (objetivo {TARGET_I:.0f})." if corregido
              else f"Venía en {lufs_antes:.1f} LUFS y no se pudo llevar a {TARGET_I:.0f}.",
              "error")

    info = ff.probe(final)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), {})
    ancho = int(v.get("width") or 0)
    check("Resolución", ancho >= 720,
          f"{v.get('width')}x{v.get('height')}" + ("" if ancho >= 720 else
          " – se ve borroso en el celular; exportá a 1080x1920 desde clips de 720p o más."))

    dur = ff.stream_duration(final, "video")
    check("Sincronía", abs(dur - ff.stream_duration(final, "audio")) <= 0.12,
          "audio y video alineados" if abs(dur - ff.stream_duration(final, "audio")) <= 0.12
          else "el audio y el video no duran lo mismo", "error")
    check("Portada", _luma(final, 1.0) > 22, "el fotograma del segundo 1 no es negro"
          if _luma(final, 1.0) > 22 else "el fotograma del segundo 1 está negro: TikTok lo usa de portada")

    on_step("Escuchando el video…")
    words = asr.transcribe_words(final)
    return controles_de_voz(final, dur, words, producto, checks,
                            lufs_antes=lufs_antes, lufs_despues=lufs_despues)


def controles_de_voz(final: str, dur: float, words: list[dict], producto: str = "",
                     checks: list[dict] | None = None,
                     lufs_antes: float | None = None, lufs_despues: float | None = None) -> dict:
    """Los controles que necesitan la transcripción. Separados para que la
    edición automática (autoedit.py) los use con las palabras que ya
    transcribió para los subtítulos, sin pasar whisper dos veces."""
    checks = checks if checks is not None else []

    def check(nombre: str, ok: bool, detalle: str, nivel: str = "aviso"):
        checks.append({"nombre": nombre, "ok": bool(ok), "nivel": nivel, "detalle": detalle})

    texto = " ".join(w["w"] for w in words)
    if not words:
        check("Voz", False, "No se detectó voz en el video.", "error")
        return {"checks": checks, "lufs_antes": lufs_antes, "lufs_despues": lufs_despues,
                "duracion": round(dur, 2), "transcripcion": "", "palabras": 0}

    primera = words[0]["t0"]
    check("Arranque", primera <= 0.5,
          f"la primera palabra entra a los {primera:.2f}s" + ("" if primera <= 0.5 else
          " – recortá ese silencio: el hook tiene que sonar desde el inicio."))

    cola = " ".join(w["w"] for w in words if w["t0"] >= dur - VENTANA_CIERRE_S)
    pide = bool(CTA.search(_plain(cola)))
    check("Cierre con pedido", pide,
          f"últimos {VENTANA_CIERRE_S:.0f}s: «{cola.strip()[:140]}»" + ("" if pide else
          " – no se pide la compra. Cerrá con el pedido y el pago contra entrega."), "error")

    if producto.strip():
        parecido, oido = _mejor_parecido(producto, [w["w"] for w in words])
        check("Nombre del producto", parecido >= 0.8,
              f"se entiende «{oido}»" if parecido >= 0.8 else
              f"no se reconoce «{producto}» (lo más parecido: «{oido or 'nada'}»). "
              "Escuchá si la voz lo pronuncia bien.")

    pausas = [(round(a["t1"], 1), round(b["t0"] - a["t1"], 1))
              for a, b in zip(words, words[1:]) if b["t0"] - a["t1"] >= 0.7]
    check("Pausas largas", not pausas,
          "sin pausas de 0.7s o más" if not pausas else
          "pausas en " + ", ".join(f"{t}s ({d}s)" for t, d in pausas[:6]) + " – cada una es una salida del video.")

    habla = words[-1]["t1"] - primera
    ppm = round(len(words) / (habla / 60)) if habla > 0 else 0
    check("Ritmo", ppm >= 150, f"{ppm} palabras por minuto" + ("" if ppm >= 150 else
          " – lento; los anuncios que ganan van a 175-200."))

    return {"checks": checks, "lufs_antes": lufs_antes, "lufs_despues": lufs_despues,
            "duracion": round(dur, 2), "transcripcion": texto, "palabras": len(words), "ppm": ppm}
