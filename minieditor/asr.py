"""ASR – subtítulos automáticos con whisper (alineación subs-last).

La decisión de arquitectura más valiosa del motor de producción:

    Los subtítulos se alinean contra el AUDIO FINAL, no contra los
    clips fuente.

Entre el clip fuente y el video terminado hubo recortes y crossfades que
comieron tiempo. Si transcribís los clips originales, la deriva se
ACUMULA hacia el final – y ningún offset fijo la compensa (en producción
se probó con 250 ms: no funcionó, porque el error no es constante, es
acumulativo). Transcribir el video FINAL la elimina de raíz: los
timestamps que devuelve whisper YA son absolutos en el timeline final.

Por eso este módulo se llama sobre el video montado (después de trim,
concat y loudnorm) y justo antes de quemar los subtítulos.

Dependencia: pip install faster-whisper  (la única de todo el proyecto,
junto a flask para la interfaz). El modelo se descarga solo la primera
vez (~465 MB para "small"). Con conexión lenta: WHISPER_MODEL=base.

⚠ Forzamos device="cpu" a propósito. El default de faster-whisper es
device="auto": en una máquina con GPU NVIDIA intenta cargar CUDA/cuBLAS,
y si el sistema no tiene esas librerías completas (el caso normal en la
mayoría de laptops de estudiantes, sobre todo Windows) revienta con
"Library cublasXX.dll is not found" – un error que no tiene nada que ver
con la instalación del proyecto. CPU es más lento pero funciona en
cualquier máquina sin configuración extra. Quien sí tenga CUDA bien
instalado puede forzar GPU con WHISPER_DEVICE=cuda.
"""

from __future__ import annotations

import os

_model = None  # se carga una vez por proceso (tarda unos segundos)


def transcribe_words(video_path: str, language: str = "es") -> list[dict]:
    """Transcribe y devuelve [{"w", "t0", "t1"}] con tiempos ABSOLUTOS.

    Los tokens que empiezan con "[" se descartan: son alucinaciones del
    ASR tipo "[Música]" en tramos sin habla.
    """
    global _model

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise RuntimeError(
            "Falta faster-whisper. Instalalo con:\n"
            "  pip install faster-whisper"
        )

    if _model is None:
        name = os.environ.get("WHISPER_MODEL", "small")
        device = os.environ.get("WHISPER_DEVICE", "cpu")
        try:
            _model = WhisperModel(name, device=device, compute_type="int8")
        except Exception as e:
            if device != "cpu":
                raise
            raise RuntimeError(
                f"No se pudo cargar el modelo whisper en CPU: {e}\n"
                "Si el error menciona una .dll de CUDA, tu instalación de "
                "faster-whisper quedó apuntando a GPU por accidente – "
                "reinstalá con: pip install --force-reinstall faster-whisper"
            ) from e

    segments, _info = _model.transcribe(
        video_path, language=language, word_timestamps=True,
        vad_filter=True,          # ignora tramos sin voz (menos alucinación)
    )

    words = []
    for seg in segments:
        for w in seg.words or []:
            token = w.word.strip()
            if not token or token.startswith("["):
                continue
            t0, t1 = float(w.start), float(w.end)
            if t1 <= t0:              # whisper emite palabras de ancho cero
                t1 = t0 + 0.05        # y un span 0 rompe cualquier renderer
            words.append({"w": token, "t0": round(t0, 3), "t1": round(t1, 3)})
    return words


# Nota para el reto avanzado: el texto que se MUESTRA idealmente es el
# guion verbatim del creador, no la transcripción (el ASR comete errores
# y alucina). El flujo pro: transcribir para obtener NÚMEROS y mapear
# esos números contra el guion con difflib.SequenceMatcher – whisper solo
# aporta timestamps. Buen próximo paso para pedirle a tu agente.
