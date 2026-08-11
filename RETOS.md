# RETOS

Backlog de mejoras para mini-editor. No es una lista fija de tareas del curso (esa
filosofía vive en `AGENT.md`) — es el lugar donde anotamos, a medida que surgen en la
conversación con el agente, las mejoras concretas que vale la pena encarar después.

Formato sugerido por ítem: qué, por qué vale la pena, y en qué archivo empieza.

## Semillas (próximos pasos razonables, no obligación)

- **Guion verbatim vs. transcripción ASR.** El texto que se muestra en los subtítulos debe
  ser el guion del creador, no lo que transcribe whisper (que alucina). El flujo pro:
  transcribir solo para obtener timestamps y mapear esos números contra el guion con
  `difflib.SequenceMatcher`. Ver la nota al final de `minieditor/asr.py` y
  `scripts/transcribe.py`. (La revisión humana en el navegador ya cubre el caso de
  corregir errores puntuales del ASR — esto es para cuando ya existe un guion escrito de
  antemano y se quiere saltar la corrección manual por completo.)
- **Capa emocional con LLM.** Clasificar el tono de cada frontera para elegir
  transición/SFX por corte en vez de una sola elección global (`Boundary` en
  `minieditor/edl.py`, loop de `bounds` en `app.py`). Un LLM externo pide API + rompe
  determinismo — `captions.py` ya abandonó un clasificador LLM por "flaky" en favor de una
  heurística. Recomendación: arrancar con heurística local (pace de habla desde los
  timestamps del ASR + picos de audio que ya mide `sfx.py`), LLM real como upgrade
  opcional, no default.
- **Notificación de escritorio al terminar el render.** Un toast de Windows cuando termina
  – útil ahora que es una app de verdad y no una pestaña de navegador.
- **ffmpeg más liviano en el instalador.** `vendor/ffmpeg/*.exe` es el build "full_build"
  de Gyan (más códecs de los que Edito usa – de ahí buena parte de los 685MB del `.exe`).
  Si algún día se distribuye fuera de la organización, conviene cambiar al build
  "essentials" (más chico) y revisar la licencia GPL para uso público.
- **Timeline visual (tipo CapCut).** Se intentó una franja horizontal con bloques
  proporcionales a la duración + arrastre de bordes para recortar – el drag no anduvo bien
  en la práctica y se revirtió (ver commit-equivalente en esta conversación). Si se
  retoma, probablemente valga la pena una librería de timeline en vez de reimplementar el
  drag a mano.

## Hecho (de conversaciones con el agente)

- **Interfaz completa**: layout de 2 columnas, branding "Edito by Influwa" (isotipo propio
  en `app.py`/`icongen.py`), animaciones auditadas con la skill de Emil Kowalski, historial
  de proyectos con reedición no destructiva (`/history`, `/project/<id>`, meta.json
  persistente), presets de marca guardables (`/presets`).
- **Recorte manual por toma**, estilo CapCut: barra arrastrable con preview en vivo del
  video, más "Dividir acá" para partir una toma en dos (`minieditor/trim.py` –
  `manual_trim`).
- **Música de fondo** (`minieditor/music.py`): pista en loop ajustada a una ventana
  [inicio, fin] del video final, con ducking manual por zonas (`_duck_expr`, rampa suave
  vía `volume=eval=frame`).
- **SFX por corte**: cada frontera entre tomas puede prender/apagar el golpe
  individualmente (antes era todo-o-nada) – `sfx_gaps` en `app.py`.
- **Hook punch (zoom de apertura).** Feature 5: la primera toma arranca con 10% de zoom y
  se asienta en 0.35s (`_hook_punch_vf` en `minieditor/normalize.py`). Gate `G9_hook_punch`.
- **Transcripción editable antes de quemar.** El job web se pausa después de transcribir y
  muestra las palabras en el navegador para corregir texto o borrar alguna que el ASR
  alucinó (`_await_subs_review`/`/confirm_subs` en `app.py`).
- **Portada inteligente.** `minieditor/thumbnail.py` (`pick_cover`): mejor frame entre los
  primeros 2.5s por heurística determinista (luminancia + varianza), exportado aparte.
- **App de escritorio + empaquetado.** `pywebview` para la ventana nativa (ícono propio
  rasterizado a mano en `icongen.py`, sin Pillow) + `PyInstaller` para `dist/Edito/Edito.exe`
  con ffmpeg embebido (`minieditor/ff.py` resuelve el binario embebido vs. PATH del
  sistema) y whisper descargándose la primera vez. Validado corriendo el .exe con el PATH
  del sistema sin ffmpeg – gates en verde.

## En curso / propuestas nuevas

_(vacío por ahora — acá van las que surjan de las conversaciones con el agente)_
