"""EDL – Edit Decision List.

El contrato de entrada del mini-editor: un JSON que describe QUÉ montar.
El pipeline solo compone lo que el EDL declara. Todo lo que requiera
análisis de señal (timings de palabras, picos de SFX) viaja resuelto
dentro del EDL o se calcula en pasos previos – nunca dentro del render.

Esa separación (análisis → EDL → composición) es la arquitectura que hace
portable la receta a cualquier stack: Remotion, MoviePy, un SaaS de video…
el compositor solo necesita leer este JSON.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class Shot:
    source: str                       # ruta al MP4 (con audio embebido)
    script: str = ""                  # guion VERBATIM de lo que se dice
    trim: bool = True                 # recortar aire muerto de cabeza/cola
    word_timings: list[dict] = field(default_factory=list)
    # word_timings: [{"w": "Hola", "t0": 0.18, "t1": 0.31}, ...]
    # relativos al CLIP FUENTE. Opcionales: si faltan y hay whisper
    # instalado, transcribe.py puede generarlos sobre el video final.


@dataclass
class Boundary:
    """Frontera entre shot i y shot i+1."""
    transition: str = "fade"          # fade | fadewhite | fadegrays | zoomin | circleopen
    duration_s: float = 0.40          # UNA sola duración para video Y audio (invariante)
    sfx: str | None = None            # ruta a un wav/mp3, o None
    sfx_gain: float = 0.82            # ganancia lineal (0.82 → -1.7 dB)
    sfx_peak_offset_s: float = 0.0    # dónde está el golpe DENTRO del archivo


@dataclass
class Edl:
    shots: list[Shot]
    boundaries: list[Boundary]
    canvas_w: int = 1080
    canvas_h: int = 1920
    fps: int = 30
    target_lufs: float = -14.0
    true_peak_db: float = -1.0
    captions: bool = True
    caption_accent: str = "#F4DC1A"   # amarillo para palabras-contenido
    hook_punch: bool = True           # zoom de apertura en el shot 0 (ver normalize.py)
    opening_sting: str | None = None  # SFX de apertura (opcional)
    closing_sting: str | None = None  # SFX de cierre (opcional)

    @classmethod
    def load(cls, path: str) -> "Edl":
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        shots = [Shot(**s) for s in raw["shots"]]
        boundaries = [Boundary(**b) for b in raw.get("boundaries", [])]
        # Si el EDL no declara fronteras, generamos una por defecto entre
        # cada par de shots.
        while len(boundaries) < max(0, len(shots) - 1):
            boundaries.append(Boundary())
        top = {k: v for k, v in raw.items() if k not in ("shots", "boundaries")}
        edl = cls(shots=shots, boundaries=boundaries, **top)
        edl.validate()
        return edl

    def validate(self) -> None:
        if not (1 <= len(self.shots) <= 25):
            raise ValueError(f"Se esperan 1..25 shots, hay {len(self.shots)}")
        if len(self.boundaries) != max(0, len(self.shots) - 1):
            raise ValueError("Debe haber exactamente len(shots)-1 fronteras")
        for b in self.boundaries:
            # ⚠ FÓRMULA DEL AIRE: el recorte deja 0.80 s de aire y las
            # transiciones solapan la frontera. Si una transición dura más
            # que el aire disponible, el crossfade SE COME palabras.
            if b.duration_s > 0.60:
                raise ValueError(
                    f"Transición de {b.duration_s}s > 0.60s: con 0.80s de aire "
                    "de recorte, pisaría la última/primera palabra. "
                    "(aire ≥ max(duración de transición) + 0.20s)"
                )
