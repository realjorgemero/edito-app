"""PASO 2 – NORMALIZAR canvas + fps, y limpiar la voz.

Dos invariantes viven acá:

1. **Todas las tomas aterrizan en el mismo canvas y fps ANTES de tocarse.**
   xfade (y cualquier compositor) exige dimensiones y timebase iguales.
   Usamos `contain` con barras negras – NUNCA `cover`: no se recorta la
   cara del avatar para llenar cuadro. Es decisión editorial, no técnica.

2. **La cadena de voz limpia, sin ganancia, en TODAS las tomas.**
   highpass 80 Hz + de-esser + compresor suave con makeup 0. La cadena
   LIMPIA; la loudness la entrega el normalizador final (paso loudnorm).
   Si el compresor también levantara volumen, pelearía con loudnorm y el
   resultado sería impredecible. Y tiene que aplicarse a TODAS las tomas:
   si solo algunas se tratan, el timbre de la voz salta en cada corte y
   se oye como error de mezcla.

⚠ El de-esser de ffmpeg usa frecuencia NORMALIZADA [0,1], no Hz:
  f=0.317 × 22050 ≈ 7 kHz. No copies 0.317 a un stack que pida Hz.

HOOK PUNCH (zoom de apertura, solo shot 0): el motor de producción real
abre con un zoom-out rápido – arranca un poco encendido y se asienta en
framing normal – para dar sensación de "enganche" en el primer instante,
el más caro del scroll. Se implementa DESPUÉS de pad/setsar, sobre el
canvas ya compuesto (barras incluidas): `scale` agranda el frame con un
factor que decae linealmente de HOOK_PUNCH_ZOOM a 1.0 en HOOK_PUNCH_S, y
`crop` recorta de vuelta al canvas exacto. Como el crop final SIEMPRE
sale en w×h fijo, un redondeo de píxel en el `scale` intermedio (`eval`
por frame, no entero) no puede romper el contenedor – lo verifica G1.
"""

from __future__ import annotations

import os

from . import ff

# clean only – NO gain, NO presence-EQ, NO coloring
VOICE_CHAIN = (
    "highpass=f=80:poles=2,"
    "deesser=i=0.3:m=0.5:f=0.317,"
    "acompressor=threshold=0.063:ratio=1.8:attack=20:release=250:makeup=1"
)

HOOK_PUNCH_ZOOM = 1.10     # 10 % de zoom al primer frame
HOOK_PUNCH_S = 0.35        # mismo ritmo que el xfade entre tomas: decae a
                           # framing normal en 0.35 s


def _hook_punch_vf(w: int, h: int) -> str:
    extra = HOOK_PUNCH_ZOOM - 1.0
    factor = f"if(lt(t,{HOOK_PUNCH_S}),{HOOK_PUNCH_ZOOM}-{extra}*t/{HOOK_PUNCH_S},1)"
    # crop evalúa x/y por-frame por defecto (a diferencia de scale, que
    # necesita el eval=frame explícito): no hace falta pedirlo acá.
    return (f"scale=w='iw*({factor})':h='ih*({factor})':eval=frame,"
            f"crop={w}:{h}:x='(iw-{w})/2':y='(ih-{h})/2'")


def normalize_shot(src: str, workdir: str, index: int,
                   w: int = 1080, h: int = 1920, fps: int = 30,
                   fade_in: bool = False, hook_punch: bool = False) -> str:
    """Lleva un clip al canvas común y aplica la cadena de voz."""
    out = os.path.join(workdir, f"norm_{index:03d}.mp4")
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
          f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}")
    af = VOICE_CHAIN
    if fade_in:
        # Fade corto de apertura. En el motor de producción son 0.30 s,
        # pero 9 frames de negro en un formato donde el scroll se decide
        # en el primer segundo son carísimos – 0.15 s.
        vf += ",fade=t=in:st=0:d=0.15"
        af += ",afade=t=in:st=0:d=0.15"
    if hook_punch:
        vf += "," + _hook_punch_vf(w, h)

    ff.run(["-i", src, "-vf", vf, "-af", af,
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-c:a", "aac", "-b:a", "256k", "-ar", "44100", "-ac", "2",
            "-pix_fmt", "yuv420p", out])
    return out
