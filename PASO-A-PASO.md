# PASO A PASO – de cero a tu editor con interfaz

Este es el recorrido de la clase práctica. Una sola instalación al principio; después
todo es usar y extender. Alcance de hoy: **unir tomas + cortar silencios + subtítulos
automáticos**, primero por línea de comandos y luego con una interfaz web en tu máquina.

---

## Paso 0 · Requisitos (5 min, una sola vez)

Necesitás dos cosas en tu máquina:

**Python 3.10 o superior**

```bash
python3 --version     # (en Windows: python --version)
```

Si no lo tenés: [python.org/downloads](https://www.python.org/downloads) (en Windows,
marcá "Add Python to PATH" al instalar).

**ffmpeg** – el motor de video que usa todo el proyecto:

```bash
# macOS (con Homebrew)
brew install ffmpeg

# Ubuntu / Debian / WSL
sudo apt install ffmpeg

# Windows
winget install ffmpeg
```

Verificá: `ffmpeg -version` debe responder. Si no responde en Windows, cerrá y reabrí
la terminal.

---

## Paso 1 · La instalación (2 min, una sola vez)

Descomprimí `mini-editor.zip` donde quieras, entrá a la carpeta e instalá las dos
dependencias (la interfaz web y el transcriptor de subtítulos):

```bash
cd mini-editor
pip install -r requirements.txt
```

Eso es todo. No hay paso 2 de instalación.

> **Nota sobre whisper**: la primera vez que pidas subtítulos, se descarga el modelo de
> transcripción (~465 MB) automáticamente. Es una sola vez. Si tu conexión es lenta,
> antes de arrancar poné `WHISPER_MODEL=base` en tu entorno (modelo más chico y rápido,
> algo menos preciso).

---

## Paso 2 · Ver el motor funcionar (2 min)

Antes de la interfaz, un render por línea de comandos para ver los pasos y los gates:

```bash
python scripts/make_demo.py                     # genera 3 tomas de prueba
python pipeline.py examples/edl.json -o demo.mp4
```

Vas a ver pasar los pasos (recorte → normalización → montaje → sonido → loudness →
subtítulos) y al final los **gates**: los chequeos automáticos sobre el archivo
entregado. Si salen ✅, abrí `demo.mp4`.

Este es el motor "completo" (incluye la pista de efectos de sonido, que en la interfaz
queda como reto). Curiosear `pipeline.py` y los módulos de `minieditor/` – cada uno
explica el porqué en su docstring – ES el material de estudio.

---

## Paso 3 · La interfaz (arranca mínima, a propósito)

```bash
python app.py
```

Abrí **http://localhost:5000**. La primera versión hace UNA cosa: subís tus tomas en
orden (el botón "➕ Agregar toma(s)" SUMA en cada click – podés agregar de a una) y te
devuelve el video con las tomas pegadas, ya con volumen profesional.

Probalo ya mismo: grabate 2-3 clips verticales con el teléfono, subilos y descargá tu
primer resultado.

## Paso 3b · Activá las features, de a una

Las features ya están construidas (viven en `minieditor/`) pero llegan apagadas: el
dict `FEATURES` al inicio de `app.py` las conecta. Activalas DE A UNA – re-editando
las mismas tomas entre cada una para ver (y oír) la diferencia:

1. `"cut_silence": True` – recorte del aire muerto. Re-editá: el video respira mejor.
2. `"transitions": True` – aparece el selector de transición. Probá el destello blanco.
3. `"subtitles": True` – subtítulos karaoke automáticos (la primera vez descarga el
   modelo whisper, ~465 MB).

Cada activación es: cambiar `False → True`, reiniciar `python app.py`, refrescar el
navegador. Pedíselo a tu agente y de paso que te explique el módulo que acabás de
enchufar.

### Qué hace por dentro (el mismo pipeline, siempre)

```
tus tomas → recorte de silencios → canvas 1080×1920 + limpieza de voz
          → montaje con transición → loudness -14 LUFS
          → whisper sobre el video FINAL → subtítulos karaoke → gates
```

El detalle que importa: los subtítulos se transcriben sobre el **video ya montado**
(no sobre tus clips originales). Así los tiempos son exactos aunque el recorte y las
transiciones hayan movido todo. Eso se llama alineación *subs-last* y es la decisión
de arquitectura más valiosa que se llevan hoy.

---

## Paso 4 · Hacelo tuyo (aquí se abre el juego)

Ya tenés un producto corriendo. A partir de acá, cada quien elige su camino:

**Camino A – usarlo con tu agente.** Abrí la carpeta en tu agente (Claude Code, Cursor…).
Ya trae `AGENT.md` con el contexto y las reglas. Pedile lo que se te ocurra: "agregale un
selector de velocidad a la interfaz", "quiero que la transición sea distinta en cada
frontera", "mostrame cómo suena con música de fondo". El agente conoce los invariantes,
te va a avisar cuando algo los rompa, y puede proponerte ideas según lo que le preguntes –
no hace falta seguir una lista: la curiosidad la vas armando en la conversación.

**Camino B – mejorar la interfaz.** Todo el frontend está en un solo archivo (`app.py`),
a propósito. Ideas: barra de progreso real, cola de trabajos, previsualización del video,
reordenar tomas arrastrando, elegir posición de subtítulos.

**Camino C – sacarlo de tu máquina.** Tal cual está, corre en localhost. Para mostrarlo
al mundo: un túnel temporal (`ngrok http 5000` o `cloudflared tunnel --url localhost:5000`)
te da una URL pública en 1 minuto. Para producción de verdad: un VPS con dominio – y ahí
van a aparecer los problemas interesantes (colas, límites, costos).

**Camino D – subir la calidad del video.** El motor ya trae, en la línea de comandos, una
pista de efectos de sonido alineados al corte (`minieditor/sfx.py`) que la interfaz
todavía no expone – llevarla ahí es un buen primer desafío. Más allá de eso: zoom de
apertura, ducking, capa emocional con LLM, música de fondo, portada inteligente – pedíselo
a tu agente y que te explique cómo encararlo con lo que ya tenés construido.

---

## Problemas frecuentes

| Síntoma | Causa y arreglo |
|---|---|
| `ffmpeg: command not found` | No está instalado o falta reabrir la terminal. Paso 0. |
| `pip: command not found` | Usá `pip3`, o `python3 -m pip install -r requirements.txt` |
| La primera edición con subtítulos tarda mucho | Está bajando el modelo whisper (una sola vez). Con conexión lenta: `WHISPER_MODEL=base` |
| Subtítulos con palabras mal transcritas | El ASR no es perfecto. En producción se resuelve mostrando el guion verbatim y mapeándolo contra la transcripción – pedile a tu agente que te explique cómo implementarlo |
| El video sale con barras negras | Tus clips no son 9:16. Es a propósito: nunca se recorta la cara para llenar cuadro |
| `Address already in use` al correr `app.py` | Ya hay una instancia corriendo. Cerrala o cambiá el puerto en la última línea de `app.py` |
| `RuntimeError: Library cublas...dll is not found` (Windows, subtítulos) | faster-whisper intentó usar tu GPU y faltan las librerías CUDA. Ya está resuelto por defecto (forzamos CPU en `minieditor/asr.py`); si lo ves igual, reinstalá con `pip install --force-reinstall faster-whisper` |
| `Unable to parse "original_size" option value ... as image size` (Windows, al quemar subtítulos) | Bug de escape de rutas con ffmpeg en Windows (la letra de unidad `C:` confundía al parser de filtros). Ya está resuelto en `minieditor/captions.py` – si tu proyecto es de antes de este fix, pedile al agente que reescriba ese archivo desde `instalar-mini-editor.md` |
