#!/usr/bin/env python3
"""Genera word_timings transcribiendo un video con whisper (OPCIONAL).

Este es el "reto 1" del mini-editor: alineación subs-last, como en
producción – transcribir el VIDEO FINAL (después de recortes, transiciones
y mezcla) para que los subtítulos no acumulen deriva.

Requiere:  pip install faster-whisper

Uso:
    python scripts/transcribe.py final_sin_subs.mp4 > words.json

Luego pegá ese JSON como word_timings (con los tiempos ya absolutos) o
usalo desde tu propia variante del pipeline.

Nota de producción: el texto que se MUESTRA debe ser el guion verbatim,
no la transcripción (el ASR alucina). El flujo pro es: transcribir para
obtener NÚMEROS, y mapear esos números contra el guion con difflib.
Ese mapeo es parte del reto.
"""

import json
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def main(path: str) -> None:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        sys.exit("Falta faster-whisper:  pip install faster-whisper")

    model = WhisperModel("small", compute_type="int8")
    segments, _info = model.transcribe(path, language="es", word_timestamps=True)

    words = []
    for seg in segments:
        for w in seg.words or []:
            token = w.word.strip()
            if not token or token.startswith("["):   # anti-alucinación [Música]
                continue
            words.append({"w": token, "t0": round(w.start, 3),
                          "t1": round(w.end, 3)})

    json.dump(words, sys.stdout, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
