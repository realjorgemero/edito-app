"""PASO 5 (opcional) – MÚSICA DE FONDO.

Una pista continua debajo de todo el audio, a volumen bajo fijo por
default. Se ajusta a la duración EXACTA del video final: se recorta si
sobra, hace loop si falta. Fade in/out corto para que no entre ni salga
en seco.

DUCKING MANUAL (a diferencia del automático por detección de voz, que
sigue pendiente en RETOS.md): el usuario marca "momentos" [inicio, fin,
nivel] donde la música baja a ese nivel y vuelve sola. Cada zona se
compone como un `volume` con `eval=frame` y una expresión anidada: fuera
de sus zonas siempre vale 1 (no toca nada), así que agregar más zonas
nunca pisa a las demás – ver _duck_expr.

Va DESPUÉS de la pista de SFX y ANTES de loudnorm – mismo invariante que
minieditor/sfx.py: todo lo que suma buses de audio corre antes del
limitador final, nunca después. Si este paso fuera último, el loudnorm ya
habría fijado el nivel y la música lo desbalancearía sin que nada lo
vuelva a corregir.
"""

from __future__ import annotations

import os

from . import ff

FADE_S = 1.2           # entrada/salida de la música
DEFAULT_GAIN = 0.22    # ~ -13 dB: bajo a propósito, es el nivel "normal"
                       # fuera de cualquier zona de ducking
DUCK_RAMP_S = 0.4      # cuánto tarda en bajar/subir al entrar/salir de una zona


def _duck_expr(zones: list[tuple[float, float, float]], ramp: float = DUCK_RAMP_S) -> str:
    """Expresión ffmpeg (variable `t`) para el filtro volume: 1 fuera de
    toda zona, `nivel` sostenido adentro, con una rampa lineal de `ramp`
    segundos en cada borde. zones = [(inicio, fin, nivel_0_a_1), ...],
    se asume ya ordenada por inicio (ver caller)."""
    expr = "1"
    for start, end, level in zones:
        expr = (
            f"if(lt(t,{start:.3f}),{expr},"
            f"if(lt(t,{start + ramp:.3f}),{expr}-({expr}-{level:.3f})*(t-{start:.3f})/{ramp:.3f},"
            f"if(lt(t,{end:.3f}),{level:.3f},"
            f"if(lt(t,{end + ramp:.3f}),{level:.3f}+({expr}-{level:.3f})*(t-{end:.3f})/{ramp:.3f},"
            f"{expr}))))"
        )
    return expr


def apply_music(video: str, music_path: str, workdir: str,
                gain: float = DEFAULT_GAIN, start_s: float = 0.0,
                end_s: float | None = None,
                duck_zones: list[tuple[float, float, float]] | None = None) -> str:
    """Mezcla music_path bajo el audio de `video`, ajustada a la ventana
    [start_s, end_s) (loop si la pista es más corta que la ventana,
    recorte si es más larga). end_s=None = hasta el final del video.

    La ventana se arma en tres pasos sobre la pista YA loopeada:
    recortarla a la duración de la ventana (atrim), resetear sus
    timestamps a 0 (asetpts – si no, atrim deja el offset del loop
    original y el fade/adelay de después queda mal alineado), y recién
    ahí retrasarla hasta start_s (adelay). El ducking (duck_zones) se
    aplica DESPUÉS de adelay a propósito: sus tiempos son los del video
    FINAL (lo mismo que ve el usuario en el panel), no los de la pista
    de música sin posicionar.

    Devuelve la ruta del nuevo video – mismo contenido de imagen
    (`-c:v copy`), audio con la música sumada.
    """
    out = os.path.join(workdir, "music.mp4")
    dur = ff.stream_duration(video, "video")
    end_s = dur if end_s is None else min(end_s, dur)
    start_s = max(0.0, min(start_s, end_s))
    window = max(0.1, end_s - start_s)
    fade = min(FADE_S, window / 2)
    fade_out_at = max(0.0, window - fade)
    delay_ms = round(start_s * 1000)

    music_filter = (
        f"[1:a]volume={gain},atrim=0:{window:.3f},asetpts=PTS-STARTPTS,"
        f"afade=t=in:st=0:d={fade:.3f},"
        f"afade=t=out:st={fade_out_at:.3f}:d={fade:.3f},"
        f"adelay={delay_ms}|{delay_ms}"
    )
    if duck_zones:
        zones = sorted(duck_zones, key=lambda z: z[0])
        music_filter += f",volume=eval=frame:volume='{_duck_expr(zones)}'"
    music_filter += "[music]"

    ff.run([
        "-i", video, "-stream_loop", "-1", "-i", music_path,
        "-filter_complex",
        f"{music_filter};[0:a][music]amix=inputs=2:duration=first:normalize=0[aout]",
        "-map", "0:v", "-map", "[aout]",
        "-t", f"{dur:.3f}",
        "-c:v", "copy", "-c:a", "aac", "-ar", "44100", "-ac", "2",
        out,
    ])
    return out
