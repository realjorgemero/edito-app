"""Helpers para invocar ffmpeg/ffprobe.

Todo el mini-editor pasa por estas dos funciones. Ventaja: un solo lugar
para logging, timeouts y manejo de errores.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys


def _tool_path(name: str) -> str:
    """Ruta al binario, embebido si la app está empaquetada (PyInstaller),
    o el nombre pelado si no – en ese caso lo resuelve el PATH, que es lo
    que ya tiene cualquiera que siguió PASO-A-PASO.md (winget/brew/apt).

    Empaquetado: PyInstaller extrae los binarios embebidos (ver
    build_installer.py, --add-binary) a sys._MEIPASS en modo --onefile,
    o junto al .exe en modo --onedir. Sin esto, el .exe no encontraría
    ffmpeg en una máquina que nunca lo instaló – todo el sentido de
    empaquetar es no depender de eso.
    """
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        bundled = os.path.join(base, f"{name}.exe")
        if os.path.isfile(bundled):
            return bundled
    return name


FFMPEG = _tool_path("ffmpeg")
FFPROBE = _tool_path("ffprobe")
DEFAULT_TIMEOUT = 600  # segundos; ningún paso de este mini-editor debería tardar más


class FfmpegError(RuntimeError):
    pass


def run(args: list[str], timeout: int = DEFAULT_TIMEOUT, quiet: bool = True,
        cwd: str | None = None) -> str:
    """Ejecuta ffmpeg y devuelve stderr (ahí escribe ffmpeg su info).

    cwd: opcional. Usalo cuando alguno de los `args` sea la ruta a un
    archivo que ffmpeg parsea como VALOR DE FILTRO (p. ej. `-vf ass=...`),
    no como argumento de archivo (`-i`, la salida). Esos valores pasan por
    el parser de filtergraph de ffmpeg, que usa ':' como separador de
    opciones – y en Windows toda ruta absoluta tiene un ':' después de la
    letra de unidad ("C:\\..."). El escape con backslash (\\:) documentado
    para ese parser no es fiable en todas las versiones de ffmpeg (se
    reprodujo el fallo incluso en Linux forzando una ruta con ':'). La
    solución robusta es evitar el problema: correr ffmpeg con cwd en la
    carpeta del archivo y pasarle solo el nombre relativo, que nunca
    contiene ':'.
    """
    cmd = [FFMPEG, "-y", "-hide_banner"] + (["-nostats"] if quiet else []) + args
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    if p.returncode != 0:
        # La cola de stderr, no la cabeza: ffmpeg imprime banner primero
        # y el error real queda al final. (Lección aprendida en producción.)
        raise FfmpegError(f"ffmpeg falló ({p.returncode}):\n{p.stderr[-1500:]}")
    return p.stderr


def probe(path: str, timeout: int = 30) -> dict:
    """ffprobe → dict con streams y format."""
    cmd = [FFPROBE, "-v", "quiet", "-print_format", "json",
           "-show_streams", "-show_format", path]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise FfmpegError(f"ffprobe falló sobre {path}:\n{p.stderr[-500:]}")
    return json.loads(p.stdout)


def stream_duration(path: str, kind: str = "video") -> float:
    """Duración del STREAM (no del contenedor).

    ⚠ Invariante de oficio: nunca uses format.duration para medir sincronía
    A/V – es la duración de la pista MÁS LARGA, y un guard que mide eso se
    auto-anula justo cuando el audio quedó más largo que el video.
    """
    info = probe(path)
    want = "video" if kind == "video" else "audio"
    for s in info.get("streams", []):
        if s.get("codec_type") == want and s.get("duration"):
            return float(s["duration"])
    # Fallback honesto: duración del contenedor, marcado como aproximación.
    return float(info.get("format", {}).get("duration", 0.0))
