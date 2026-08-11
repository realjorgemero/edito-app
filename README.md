# mini-editor

Motor didáctico de edición de video publicitario vertical (9:16), destilado de un motor de
producción real. Python + ffmpeg, y una **interfaz web en localhost** donde subís tus
tomas y descargás el MP4 editado: silencios recortados, tomas unidas con transición,
loudness de plataforma y subtítulos karaoke automáticos – verificado por gates.

> **¿Primera vez?** Seguí [`PASO-A-PASO.md`](./PASO-A-PASO.md): instalación desde cero,
> primer render y la interfaz, en ese orden.
>
> Este repo está pensado para trabajarse **con tu agente de IA** (Claude Code o similar).
> Abrí el proyecto, dejá que lea `AGENT.md`, y pedile cosas.

## Instalación (una sola vez)

1. Python 3.10+ y ffmpeg en el PATH
   (macOS: `brew install ffmpeg` · Ubuntu/Debian: `sudo apt install ffmpeg` · Windows: `winget install ffmpeg`)
2. `pip install -r requirements.txt` (flask para la interfaz + faster-whisper para los
   subtítulos; el modelo de whisper se descarga solo la primera vez, ~465 MB)

## Usarlo

**Con interfaz** (el producto):

```bash
python app.py          # → http://localhost:5000
```

Subí 2+ tomas en orden, elegí features (cortar silencios, transición, subtítulos, color)
y descargá el MP4 con su reporte de gates.

**Por línea de comandos** (el motor completo, con pista de SFX incluida):

```bash
python scripts/make_demo.py            # genera clips y SFX sintéticos de prueba
python pipeline.py examples/edl.json -o demo.mp4
```

Con clips propios: armá tu `edl.json` (ver abajo) y agregá `--auto-subs` para que los
subtítulos se transcriban solos con whisper.

## El flujo

```
EDL (JSON)
   │
   ▼
1. trim       recorte del aire muerto (silencedetect, deja 0.80 s de aire)
2. normalize  canvas 1080×1920 @30 fps (contain, nunca cover) + cadena de voz
3. concat     montaje con xfade — UNA sola pasada, una duración por frontera
4. sfx        stings + golpes alineados por pico (el pico cae 40 ms ANTES del corte)
5. loudnorm   -14 LUFS / -1 dBTP, dos pasadas — ÚLTIMO paso de audio
6. captions   subtítulos karaoke de bloque estable — lo último de todo
7. gates      verificación sobre el ENTREGABLE (loudness, sync, portada, energía)
   │
   ▼
final.mp4
```

Cada módulo de `minieditor/` documenta en su docstring **por qué** ese paso es como es y
qué se rompe si lo cambiás. Leer los docstrings ES el curso.

## El EDL

```jsonc
{
  "canvas_w": 1080, "canvas_h": 1920, "fps": 30,
  "captions": true,
  "caption_accent": "#F4DC1A",
  "opening_sting": "examples/sfx/impact.wav",
  "closing_sting": "examples/sfx/impact.wav",
  "shots": [
    {
      "source": "examples/clips/toma_0.mp4",
      "script": "Nadie te contó esto sobre editar videos",
      "trim": true,
      "word_timings": [ {"w": "Nadie", "t0": 1.35, "t1": 1.76}, ... ]
    }
  ],
  "boundaries": [
    {
      "transition": "fadewhite",       // fade | fadegrays | fadewhite | zoomin | circleopen | distance
      "duration_s": 0.35,              // la MISMA para video y audio (invariante)
      "sfx": "examples/sfx/impact.wav",
      "sfx_peak_offset_s": 0.013,      // dónde está el golpe DENTRO del archivo
      "sfx_gain": 0.82
    }
  ]
}
```

Notas:

- `word_timings` son relativos al **clip fuente**; el pipeline los mapea al timeline final
  (descontando recortes y transiciones). Si faltan, usá `--auto-subs` y whisper transcribe
  el **video final** (alineación subs-last, deriva cero – la interfaz siempre hace esto).
- `sfx_peak_offset_s` se mide **una vez por asset** con `python -m minieditor.peaks archivo.wav`
  (deja un sidecar `.peak`). El render nunca analiza audio: solo resta dos números.
- La duración de cada transición está capada a 0.60 s: con 0.80 s de aire de recorte, una
  transición más larga pisaría la última palabra de la toma (la "fórmula del aire", ver
  `minieditor/edl.py`).

## Los invariantes que este código protege

Si tocás el código (y la idea es que lo hagas), estas seis cosas no se negocian:

1. **Loudness -14 LUFS / -1 dBTP, en dos pasadas, al FINAL del audio.** Un anuncio a -20
   LUFS suena apagado al lado del siguiente video del feed.
2. **Una sola duración por frontera, compartida por xfade y acrossfade.** 50 ms de
   diferencia × 6 fronteras = 300 ms de desincronía acumulada.
3. **El pico del SFX cae 40 ms antes del corte** (y "corte" = punto MEDIO del crossfade).
   Encima del corte se lee como ruido; antes, como acento.
4. **Aire de recorte ≥ duración de la transición más larga + margen.** Si no, el crossfade
   se come palabras.
5. **Cadena de voz limpia (sin ganancia) en TODAS las tomas.** Si solo algunas se tratan,
   el timbre salta en cada corte.
6. **Los gates miden el ENTREGABLE, no un intermedio.** Una métrica que no abre el MP4
   final es una métrica que miente.

## Estructura

```
app.py                     la interfaz web (un solo archivo, para hacerla tuya)
pipeline.py                orquestador CLI (leelo primero: son ~100 líneas)
minieditor/
  edl.py                   el contrato de entrada + validación
  trim.py                  paso 1 · recorte de silencios
  normalize.py             paso 2 · canvas + voz
  concat.py                paso 3 · montaje
  sfx.py                   paso 4 · pista de sonido (solo CLI; en la interfaz es reto)
  loudnorm.py              paso 5 · loudness
  asr.py                   transcripción whisper (subs-last)
  captions.py              paso 6 · subtítulos
  gates.py                 paso 7 · verificación
  peaks.py                 análisis de pico de SFX (offline)
  ff.py                    helpers ffmpeg/ffprobe
scripts/
  make_demo.py             genera el proyecto de ejemplo
  transcribe.py            transcripción suelta por CLI
assets/sfx/README.md       cómo armar tu librería de SFX (CC0) y qué comprar
PASO-A-PASO.md             instalación desde cero + primer uso
AGENT.md                   contexto para tu agente de IA
```

## Licencia

MIT. Usalo, rompelo, mejoralo, compartilo.
