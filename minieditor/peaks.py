"""Análisis de pico RMS de un asset de sonido.

Se corre UNA vez por asset (offline, al ingresar el asset al catálogo),
nunca durante el render. El resultado se guarda en un sidecar `.peak`.

Algoritmo (el mismo del motor de producción):

  decode a mono 16 kHz PCM → normalizar por max(abs) → envolvente RMS con
  ventana de 20 ms → argmax.

Criterio de compra de un SFX (esto NO es obvio): el asset necesita UN
único pico RMS con ≥3 dB de ventaja sobre el segundo máximo local. Si
tiene meseta (dos máximos casi iguales), el argmax elige uno arbitrario y
el golpe se desplaza entre renders sin que nada falle.
"""

from __future__ import annotations

import struct
import subprocess

from . import ff

SR = 16000
WIN_S = 0.020


def measure_peak(path: str) -> float:
    """Devuelve el offset (s) del pico RMS dentro del archivo."""
    p = subprocess.run(
        [ff.FFMPEG, "-v", "quiet", "-i", path, "-ac", "1", "-ar", str(SR),
         "-f", "s16le", "-"],
        capture_output=True, timeout=60, creationflags=ff.NO_WINDOW_FLAGS)
    raw = p.stdout
    n = len(raw) // 2
    if n == 0:
        return 0.0
    samples = struct.unpack(f"<{n}h", raw[: n * 2])

    win = max(1, int(SR * WIN_S))
    best_i, best_e, acc = 0, -1.0, 0.0
    # Suma deslizante de energía (equivalente a envolvente RMS, sin numpy)
    sq = [s * s for s in samples]
    acc = sum(sq[:win])
    best_e, best_i = acc, 0
    for i in range(1, n - win):
        acc += sq[i + win - 1] - sq[i - 1]
        if acc > best_e:
            best_e, best_i = acc, i
    return (best_i + win / 2) / SR


if __name__ == "__main__":
    import sys
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    for f in sys.argv[1:]:
        peak = measure_peak(f)
        with open(f + ".peak", "w") as fh:
            fh.write(f"{peak:.3f}")
        print(f"{f}: pico en {peak:.3f}s → sidecar .peak escrito")
