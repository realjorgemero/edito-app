#!/usr/bin/env python3
"""mini-editor · interfaz web en localhost.

    python app.py
    → abrí http://localhost:5000

Subís tus tomas en orden y descargás el MP4 con las tomas pegadas.

FEATURES – la interfaz arranca MÍNIMA a propósito (solo pegar tomas).
Cada feature se activa cambiando False → True acá abajo y reiniciando
la app. La idea: activalas de a una, re-editá las mismas tomas, y mirá
la diferencia que agrega cada una. El motor de cada feature ya está
construido y probado (vive en minieditor/); el flag solo lo conecta.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid

# En el .exe empaquetado (console=False) no hay consola adjunta, así que
# sys.stdout/stderr son None – cualquier print() los revienta con
# AttributeError. Los mandamos a devnull para que print() siga andando.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

# La consola de Windows usa por defecto un codepage (cp1252) que no sabe
# imprimir los símbolos que usamos en los mensajes (→, ✅). Sin esto, el
# primer print() con un símbolo así revienta con UnicodeEncodeError.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# BASE_DIR: junto al script en modo desarrollo. DATA_DIR: dónde viven
# jobs/, presets.json y el ícono cacheado – EMPAQUETADO, va a
# %LOCALAPPDATA%\Edito, no junto al .exe. Es a propósito: el plan de
# actualización es "descargá el .exe nuevo y corré ese" (un solo archivo
# portable, puede vivir en Descargas, el Escritorio, donde sea, y el
# archivo VIEJO se descarta). Si los datos vivieran junto al .exe, cada
# actualización te dejaría con el historial "perdido" en la versión
# anterior. LOCALAPPDATA es estable pase lo que pase con el .exe.
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
    DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", BASE_DIR), "Edito")
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = BASE_DIR
os.makedirs(DATA_DIR, exist_ok=True)

from flask import Flask, render_template_string, request, send_file, send_from_directory

import icongen
from minieditor import asr, captions, concat, gates, loudnorm, music, normalize, sfx, thumbnail, trim
from minieditor.edl import Boundary, Edl
from minieditor.ff import stream_duration
from minieditor.peaks import measure_peak

# ─── Versión y chequeo de actualizaciones ──────────────────────────────
# Subí cada nueva versión del .exe a un Release de GitHub con tag "vX.Y.Z"
# (ver build_installer.ps1). UPDATE_REPO queda vacío hasta que crees ese
# repo – con eso, _check_for_update() se auto-desactiva sola (fail-open,
# nunca bloquea el arranque de la app). Se resuelve DENTRO de index(),
# no vía un fetch de JS aparte – ver el comentario en el <template> de
# PAGE, cerca de "update-banner", para el porqué.
EDITO_VERSION = "1.1.0"
UPDATE_REPO = "realjorgemero/edito-app"
# ─────────────────────────────────────────────────────────────────────

# ─── FEATURES ─────────────────────────────────────────────────────────
FEATURES = {
    "cut_silence": True,    # Feature 1 · recorta el aire muerto de cada toma
    "transitions": True,    # Feature 2 · selector de transición entre tomas
    "subtitles":   True,    # Feature 3 · subtítulos karaoke automáticos (whisper)
    "sfx":         True,    # Feature 4 · sonido en los cortes (stings alineados por pico)
    "hook_punch":  True,    # Feature 5 · zoom de apertura en la primera toma
    "music":       True,    # Feature 6 · música de fondo (volumen fijo, sin ducking)
}
# ─────────────────────────────────────────────────────────────────────

# Fuentes ofrecidas para los subtítulos: nombres de familia tal como
# libass las resuelve. Esta build de ffmpeg usa el proveedor DirectWrite
# (confirmado con `ffmpeg -v verbose`), que solo ve fuentes REALMENTE
# instaladas en Windows – por eso las de sistema (Arial, Impact, etc.) no
# hace falta embeberlas. Las que no vienen con Windows viven como archivo
# en assets/fonts/ (con su licencia) y viajan a libass vía `fontsdir` en
# minieditor/captions.py (burn) – están marcadas abajo.
FONT_CHOICES = [
    ("Arial", "Arial"),
    ("Segoe UI", "Segoe UI (moderna)"),
    ("Impact", "Impact (bold, tipo meme)"),
    ("Georgia", "Georgia (serif)"),
    ("Verdana", "Verdana"),
    ("Comic Sans MS", "Comic Sans"),
    ("Trebuchet MS", "Trebuchet"),
    ("Bowlby One SC", "Bowlby One SC (display, mayúsculas)"),  # assets/fonts/
    ("Anton", "Anton (bold condensada, captions)"),  # assets/fonts/
    ("Bebas Neue", "Bebas Neue (condensada, títulos)"),  # assets/fonts/
    ("Poppins ExtraBold", "Poppins ExtraBold (moderna, legible)"),  # assets/fonts/
]

# Nombre de familia -> archivo, para las fuentes de FONT_CHOICES que NO
# vienen con Windows (viven en assets/fonts/, ver captions.fonts_dir()).
# Sirve para dos cosas: la ruta /fonts/<archivo> de abajo, y el
# @font-face que le mete el navegador para que el preview de la UI
# coincida con lo que se quema en el video (si no, el preview cae al
# font-family por defecto del navegador para cualquier fuente que no
# esté instalada en el sistema).
EMBEDDED_FONT_FILES = {
    "Bowlby One SC": "BowlbyOneSC-Regular.ttf",
    "Anton": "Anton-Regular.ttf",
    "Bebas Neue": "BebasNeue-Regular.ttf",
    "Poppins ExtraBold": "Poppins-ExtraBold.ttf",
}
FONTS_DIR = captions.fonts_dir()

# Favicon: el mismo isotipo de "E" que usa .brand en el <head>, pre-codificado
# como data URI (self-contained, sin servir un archivo aparte). Fondo sólido
# (no transparente) para que se lea igual en pestañas claras y oscuras.
_EDITO_MARK_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
<rect width="24" height="24" rx="6" fill="#0b1020"/>
<rect x="6" y="5" width="3" height="14" rx="1.5" fill="url(#g)"/>
<rect x="6" y="5" width="12" height="3" rx="1.5" fill="url(#g)"/>
<rect x="6" y="10.5" width="8" height="3" rx="1.5" fill="url(#g)"/>
<rect x="6" y="16" width="12" height="3" rx="1.5" fill="url(#g)"/>
<defs><linearGradient id="g" x1="4" y1="4" x2="20" y2="20" gradientUnits="userSpaceOnUse">
<stop stop-color="#bcd2ff"/><stop offset="1" stop-color="#2148ff"/>
</linearGradient></defs>
</svg>"""
FAVICON_HREF = "data:image/svg+xml;base64," + base64.b64encode(
    _EDITO_MARK_SVG.encode("utf-8")).decode("ascii")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500 MB por request

JOBS_DIR = os.path.join(DATA_DIR, "jobs")
os.makedirs(JOBS_DIR, exist_ok=True)

# ─── JOBS en memoria: render corre en un hilo de background, el navegador
# hace polling a /status/<job_id> en vez de bloquear la request de /render.
# Alcanza para uso local/single-instance; si esto se despliega detrás de
# más de un worker process, este dict deja de ser compartido y hay que
# pasarlo a algo externo (Redis, un archivo por job, etc).
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
JOB_TTL_HOURS = 24 * 30  # 30 días: también es la ventana de retención del
                         # historial (ver /history) – pasado esto, un
                         # proyecto se borra solo (disco y memoria)

# ─── Descarga de actualización: mismo patrón que JOBS (hilo de background
# + polling), un solo estado global porque a lo sumo hay una descarga de
# actualización a la vez. `path` solo se llena cuando status=="ready" – es
# el que apply_update() (ver Api en __main__) copia sobre el .exe actual.
UPDATE_DL_STATE: dict = {"status": "idle"}
UPDATE_DL_LOCK = threading.Lock()

# ─── Presets de marca: combinación con nombre de fuente/color/posición/
# transición, para no reconfigurar todo cada vez. Un solo JSON chico junto
# al resto de la app (no son por-usuario ni por-proyecto, son de la
# instalación entera).
PRESETS_FILE = os.path.join(DATA_DIR, "presets.json")
PRESETS_LOCK = threading.Lock()

# ─── Revisión de subtítulos: el hilo del job se PAUSA después de
# transcribir (subs-last) y espera a que /confirm_subs le mande el texto
# corregido. Un Event por job liberado desde esa ruta; JOB_EDITS es donde
# esa ruta deja las palabras editadas para que el hilo las recoja al
# despertar. Igual que JOBS: alcanza para un solo proceso.
JOB_EVENTS: dict[str, threading.Event] = {}
JOB_EDITS: dict[str, list[dict]] = {}


def _cleanup_old_jobs() -> None:
    """Fail-open: esto es limpieza de casa, nunca debe tumbar un request."""
    cutoff = time.time() - JOB_TTL_HOURS * 3600
    try:
        with JOBS_LOCK:
            stale = [jid for jid, j in JOBS.items() if j.get("created", 0) < cutoff]
            for jid in stale:
                JOBS.pop(jid, None)
                JOB_EVENTS.pop(jid, None)
                JOB_EDITS.pop(jid, None)
        for name in os.listdir(JOBS_DIR):
            path = os.path.join(JOBS_DIR, name)
            if os.path.isdir(path) and os.path.getmtime(path) < cutoff:
                shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass


_cleanup_old_jobs()

PAGE = """
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Edito</title>
<link rel="icon" type="image/svg+xml" href="{{ favicon }}">
<style>
  {# Fuentes propias (no instaladas en Windows): sin esto el preview de
     abajo cae al font-family por defecto del navegador aunque el video
     final sí las queme bien (las resuelve fontconfig, no el navegador). #}
  {% for name, filename in embedded_fonts.items() %}
  @font-face { font-family: "{{ name }}"; src: url("/fonts/{{ filename }}"); }
  {% endfor %}
  :root { --bg:#05070d; --card-border:rgba(255,255,255,.08); --ink:#eef2ff;
          --dim:#8b96ab; --blue1:#bcd2ff; --blue2:#3b6fff; --blue3:#1638c9;
          --ok:#3ddc84; --bad:#ff5d5d; color-scheme:dark;
          /* Curvas propias: las easings nativas de CSS (ease, ease-in-out)
             son débiles y se sienten genéricas. ease-out fuerte para lo que
             entra/responde a un click; ease-in-out fuerte para lo que se
             mueve en pantalla. Nunca ease-in solo (arranca lento = se
             siente lag). */
          --ease-out:cubic-bezier(.23,1,.32,1);
          --ease-in-out:cubic-bezier(.77,0,.175,1); }
  /* color-scheme:dark le pide al motor (WebView2/Chromium acá) que dibuje
     los controles NATIVOS del sistema (el popup de <select>, scrollbars)
     en tema oscuro. Sin esto, ese popup usa el tema claro del SO aunque
     el resto de la página sea oscura – ilegible: texto claro sobre fondo
     claro. option{} de abajo es el respaldo para navegadores que sí
     dejan pintar las opciones a mano. */
  * { box-sizing:border-box; }
  body { margin:0; min-height:100vh; color:var(--ink);
         font:16px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif;
         background:
           radial-gradient(650px 500px at 50% -8%, rgba(60,100,255,.55), transparent 60%),
           radial-gradient(500px 420px at 96% 28%, rgba(40,70,220,.28), transparent 60%),
           var(--bg);
         background-attachment:fixed; }
  .shell { max-width:1240px; margin:0 auto; padding:40px 24px 90px;
           display:grid; grid-template-columns:1fr; gap:28px; }
  @media (min-width:860px) {
    .shell { grid-template-columns:minmax(380px,480px) 1fr; align-items:start; }
    .side { position:sticky; top:40px; }
  }
  .brand .name { font-size:16px; font-weight:800; letter-spacing:-.01em; line-height:1.2; }
  .brand .byline { font-size:11px; font-weight:600; color:var(--dim);
           letter-spacing:.04em; text-transform:uppercase; line-height:1.3; }
  h1 { font-size:34px; line-height:1.16; margin:0 0 10px; font-weight:800; letter-spacing:-.02em; }
  h1 .accent { background:linear-gradient(135deg,var(--blue1),var(--blue2));
               -webkit-background-clip:text; background-clip:text; color:transparent; }
  .sub { color:var(--dim); margin:0 0 30px; font-size:15px; }
  .card { background:linear-gradient(180deg, rgba(255,255,255,.05), rgba(255,255,255,.02));
          border:1px solid var(--card-border); border-radius:20px; padding:22px;
          margin-bottom:16px; }
  .cardhead { display:flex; align-items:center; gap:10px; margin-bottom:14px; }
  .steptag { display:inline-flex; align-items:center; justify-content:center; width:22px;
             height:22px; border-radius:50%; background:linear-gradient(135deg,var(--blue1),var(--blue2));
             color:#05070d; font-size:12px; font-weight:800; flex-shrink:0; }
  .card h2 { font-size:13px; margin:0; text-transform:uppercase;
             letter-spacing:.09em; color:var(--dim); font-weight:700; }
  .addbtn { display:inline-flex; align-items:center; gap:8px; background:rgba(255,255,255,.04);
            color:var(--ink); border:1px dashed rgba(255,255,255,.25); border-radius:12px;
            padding:12px 18px; cursor:pointer; font-size:15px; font-weight:600;
            transition:border-color 160ms var(--ease-out), transform 100ms var(--ease-out); }
  @media (hover:hover) and (pointer:fine) {
    .addbtn:hover { border-color:var(--blue2); }
  }
  .addbtn:active { transform:scale(.97); }
  #picker { display:none; }
  .filelist { margin:14px 0 0; padding:0; list-style:none; font-size:14px; }
  .filelist li { padding:10px 12px; background:rgba(255,255,255,.04);
                 border:1px solid rgba(255,255,255,.06); border-radius:12px;
                 margin-bottom:8px; display:flex; flex-direction:column; gap:10px;
                 cursor:grab; transition:opacity 160ms var(--ease-out),
                 border-color 160ms var(--ease-out); }
  .filelist li.dragging { opacity:.35; }
  .filelist li.dragover { border-color:var(--blue2); }
  .filelist-row { display:flex; align-items:center; gap:10px; }
  .filelist .grip { color:var(--dim); font-size:14px; user-select:none; }
  .filelist .thumb { width:26px; height:46px; border-radius:6px; flex-shrink:0;
                 background-color:rgba(255,255,255,.06); background-size:cover;
                 background-position:center; }
  .filelist .thumb-empty { display:flex; align-items:center; justify-content:center;
                 color:var(--dim); font-size:12px; }
  .filelist .n { color:var(--blue1); font-weight:700; min-width:18px; }
  .filelist .name { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .filelist .size { color:var(--dim); font-size:13px; }
  .filelist .reused { color:var(--blue1); font-size:13px; }
  .filelist button { background:none; border:none; color:var(--bad);
                     font-size:18px; cursor:pointer; padding:0 4px;
                     transition:transform 100ms var(--ease-out); }
  .filelist button:active { transform:scale(.85); }
  .filelist .trimbtn { color:var(--dim); font-size:15px; }
  .filelist .trimbtn.active { color:var(--blue1); }
  .trim-panel { display:flex; flex-direction:column; gap:8px; padding-top:8px;
           border-top:1px solid rgba(255,255,255,.08); cursor:default; }
  .trim-panel video { width:100%; max-height:200px; border-radius:10px; background:#000; display:block; }
  /* Barra estilo CapCut: dos manijas arrastrables sobre la duración del
     clip. Arrastrar mueve el playhead del video en vivo – ver el "seek"
     dentro del pointermove en buildTrimPanel(). */
  .trim-bar { position:relative; height:40px; margin:26px 4px 4px; }
  .trim-bar-track { position:absolute; inset:0; background:rgba(255,255,255,.06);
           border-radius:8px; }
  .trim-bar-selected { position:absolute; top:0; bottom:0;
           background:rgba(59,111,255,.22); border-top:2px solid var(--blue2);
           border-bottom:2px solid var(--blue2); pointer-events:none; }
  .trim-handle { position:absolute; top:0; bottom:0; width:14px; margin-left:-7px;
           background:linear-gradient(135deg,var(--blue1),var(--blue2));
           border-radius:6px; cursor:ew-resize; touch-action:none;
           display:flex; align-items:center; justify-content:center; }
  .trim-handle::after { content:''; width:2px; height:14px;
           background:rgba(0,0,0,.4); border-radius:2px; }
  .trim-handle:active { filter:brightness(1.15); }
  .trim-time { position:absolute; top:-22px; left:50%; transform:translateX(-50%);
           font-size:11px; color:var(--dim); white-space:nowrap; }
  .trim-controls { display:flex; flex-wrap:wrap; gap:6px 8px; align-items:center;
           font-size:12px; color:var(--dim); }
  .trim-controls button { background:rgba(255,255,255,.06); border:1px solid rgba(255,255,255,.1);
           color:var(--ink); border-radius:8px; padding:6px 10px; font-size:11px; cursor:pointer;
           transition:border-color 160ms var(--ease-out), transform 100ms var(--ease-out); }
  .trim-controls button:active { transform:scale(.94); }
  @media (hover:hover) and (pointer:fine) {
    .trim-controls button:hover { border-color:var(--blue2); }
  }
  /* Conector entre dos tomas: prender/apagar el golpe de SFX en ESE
     corte puntual, en vez de todo-o-nada para todo el video. */
  .connector { display:flex; justify-content:center; margin:-2px 0 6px; cursor:default !important; }
  .connector-toggle { display:flex; align-items:center; gap:6px; font-size:12px;
           color:var(--dim); cursor:pointer; padding:4px 10px; border-radius:999px;
           border:1px dashed rgba(255,255,255,.14);
           transition:border-color 160ms var(--ease-out), color 160ms var(--ease-out); }
  .connector-toggle input { accent-color:var(--blue2); cursor:pointer; }
  .connector-toggle.on { color:var(--blue1); border-color:rgba(120,150,255,.35); }
  .hint { font-size:13px; color:var(--dim); margin-top:12px; line-height:1.5; }
  /* cursor:pointer solo en las filas que SON <label> (togglean el checkbox
     con un click en cualquier parte) – las que envuelven un select/color/
     file son <div>: ponerle cursor:pointer ahí prometía un click que la
     fila no responde, solo el control de adentro. */
  .frow { display:flex; align-items:center; justify-content:space-between; gap:16px;
          padding:14px 0; border-bottom:1px solid rgba(255,255,255,.06); }
  label.frow { cursor:pointer; }
  .frow:last-child { border-bottom:none; padding-bottom:2px; }
  .frow:first-of-type { padding-top:2px; }
  .ftext { flex:1; }
  .ftitle { font-size:15px; font-weight:700; margin-bottom:2px; }
  .fdesc { font-size:13px; color:var(--dim); line-height:1.4; }
  input[type=checkbox] { appearance:none; -webkit-appearance:none; width:26px; height:26px;
           border-radius:50%; border:2px solid rgba(255,255,255,.25); background:transparent;
           cursor:pointer; flex-shrink:0; display:grid; place-content:center;
           transition:background 160ms var(--ease-out), border-color 160ms var(--ease-out),
           transform 100ms var(--ease-out); }
  input[type=checkbox]:active { transform:scale(.9); }
  input[type=checkbox]:checked { background:linear-gradient(135deg,var(--blue1),var(--blue2));
           border-color:transparent; }
  input[type=checkbox]:checked::after { content:"✓"; color:#05070d; font-size:14px; font-weight:900; }
  select, input[type=color] { background:rgba(255,255,255,.06); color:var(--ink);
           border:1px solid rgba(255,255,255,.1); border-radius:10px; padding:9px 12px;
           font-size:14px; cursor:pointer;
           transition:border-color 160ms var(--ease-out), background 160ms var(--ease-out); }
  @media (hover:hover) and (pointer:fine) {
    select:hover, input[type=color]:hover { border-color:rgba(255,255,255,.22); }
  }
  select:focus-visible, input[type=color]:focus-visible {
           outline:none; border-color:var(--blue2); background:rgba(255,255,255,.09); }
  select option { background:#0b1020; color:var(--ink); }
  input[type=color] { padding:2px; width:44px; height:36px; cursor:pointer; }
  .fontpick { display:flex; align-items:center; gap:10px; }
  .fontpreview { font-size:19px; font-weight:800; color:var(--blue1);
           min-width:30px; text-align:center; }
  input[type=range] { width:110px; accent-color:var(--blue2); cursor:pointer; }
  input[type=number] { width:64px; background:rgba(255,255,255,.06); color:var(--ink);
           border:1px solid rgba(255,255,255,.1); border-radius:10px; padding:8px 8px;
           font-size:13px; transition:border-color 160ms var(--ease-out); }
  input[type=number]:focus-visible { outline:none; border-color:var(--blue2); }
  .duck-section { flex-direction:column; align-items:stretch; }
  .duck-add { margin-top:10px; width:100%; justify-content:center; }
  .duck-zone { display:flex; flex-wrap:wrap; align-items:center; gap:6px;
           font-size:12px; color:var(--dim); padding:8px 10px; margin-top:8px;
           background:rgba(255,255,255,.03); border:1px solid rgba(255,255,255,.07);
           border-radius:10px; animation:riseIn 200ms var(--ease-out) backwards; }
  .duck-zone input[type=number] { width:52px; }
  .duck-zone input[type=range] { width:70px; }
  .duck-zone .dzLevelLabel { color:var(--blue1); font-weight:700; min-width:32px; }
  .duck-zone .dzDel { background:none; border:none; color:var(--bad); font-size:16px;
           cursor:pointer; padding:0 2px; margin-left:auto;
           transition:transform 100ms var(--ease-out); }
  .duck-zone .dzDel:active { transform:scale(.85); }
  input[type=file]#sfxfile { background:none; border:none; color:var(--dim);
           font-size:12px; max-width:150px; cursor:pointer; }
  input[type=file]#sfxfile::file-selector-button { background:rgba(255,255,255,.08);
           color:var(--ink); border:none; border-radius:8px; padding:6px 10px;
           font-size:12px; cursor:pointer; margin-right:8px; }
  button.go { width:100%; background:linear-gradient(135deg,var(--blue1) 0%,var(--blue2) 55%,var(--blue3) 100%);
           color:#fff; font-size:17px; font-weight:800; border:none; border-radius:999px;
           padding:17px; cursor:pointer; box-shadow:0 10px 30px -10px rgba(45,90,255,.8);
           transition:transform 120ms var(--ease-out), box-shadow 160ms var(--ease-out); }
  button.go:active { transform:scale(.97); }
  button.go:disabled { opacity:.4; cursor:wait; box-shadow:none; }
  #status { text-align:center; padding:14px 10px; color:var(--dim); min-height:28px; font-size:14px; }
  #progwrap { display:none; align-items:center; gap:10px; margin-top:4px; }
  #progbar { flex:1; height:8px; border-radius:999px; background:rgba(255,255,255,.08);
             overflow:hidden; }
  #progfill { height:100%; width:100%; border-radius:999px;
              background:linear-gradient(90deg,var(--blue1),var(--blue2));
              transform:scaleX(0); transform-origin:left;
              transition:transform 350ms var(--ease-in-out); }
  #progpct { font-size:12px; color:var(--dim); min-width:34px; text-align:right; }
  #preview { width:100%; border-radius:16px; margin-bottom:12px; background:#000; display:block; }
  #result a, #result button.dl-btn { display:block; width:100%; text-align:center;
              background:linear-gradient(135deg,#5eeaa8,#1fae63); font:inherit; border:none;
              color:#04150a; font-weight:800; border-radius:999px; padding:16px; cursor:pointer;
              text-decoration:none; box-shadow:0 10px 30px -10px rgba(40,220,130,.6);
              transition:transform 120ms var(--ease-out); }
  #result a:active, #result button.dl-btn:active { transform:scale(.97); }
  #result a.secondary, #result button.dl-btn.secondary { background:rgba(255,255,255,.06); color:var(--ink);
              box-shadow:none; margin-top:10px; font-weight:600; }
  #result img.cover { width:100%; border-radius:16px; margin-bottom:12px; display:block; }
  .subword { display:flex; align-items:center; gap:8px; margin-bottom:8px; }
  .subword input { flex:1; background:rgba(255,255,255,.06); color:var(--ink);
           border:1px solid rgba(255,255,255,.1); border-radius:10px;
           padding:9px 12px; font-size:14px;
           transition:border-color 160ms var(--ease-out); }
  .subword input:focus-visible { outline:none; border-color:var(--blue2); }
  .subword button { background:none; border:none; color:var(--bad);
           font-size:18px; cursor:pointer; padding:0 4px;
           transition:transform 100ms var(--ease-out); }
  .subword button:active { transform:scale(.85); }
  .gates { font:13px/1.8 ui-monospace,monospace; white-space:pre-wrap;
           background:rgba(0,0,0,.35); border:1px solid rgba(255,255,255,.06);
           border-radius:14px; padding:16px; margin-top:14px; }
  .err { color:var(--bad); white-space:pre-wrap; font-size:13px; }
  .spin { display:inline-block; width:14px; height:14px; border:2px solid var(--dim);
          border-top-color:var(--blue2); border-radius:50%;
          animation:r 0.8s linear infinite; vertical-align:-2px; margin-right:8px; }
  @keyframes r { to { transform:rotate(360deg); } }
  /* Entrada: nunca desde scale(0)/opacity puro sin desplazamiento – nada en
     el mundo real aparece de la nada. 8px de recorrido + easing propio. */
  @keyframes riseIn { from { opacity:0; transform:translateY(8px); } to { opacity:1; transform:translateY(0); } }
  .filelist li { animation:riseIn 240ms var(--ease-out) backwards; }
  .fade-in { animation:riseIn 260ms var(--ease-out) backwards; }
  /* Delight, con cuentagotas: un solo pulso, solo cuando termina un
     render con TODOS los gates en verde – ocurre una vez por render
     exitoso, nunca en acciones frecuentes, así que se gana el "bounce". */
  @keyframes successPulse { 0% { transform:scale(1); } 45% { transform:scale(1.08); } 100% { transform:scale(1); } }
  .status-ok { display:inline-block; animation:successPulse 420ms var(--ease-out); }
  @media (prefers-reduced-motion:reduce) {
    .filelist li, .fade-in, .status-ok { animation:none; }
    * { transition-duration:1ms !important; }
  }
  .previewcard { min-height:420px; display:flex; flex-direction:column; }
  .empty-state { flex:1; display:flex; flex-direction:column; align-items:center;
           justify-content:center; gap:16px; padding:30px 10px; color:var(--dim);
           text-align:center; }
  .phone-frame { width:160px; aspect-ratio:9/16; border:2px dashed rgba(255,255,255,.14);
           border-radius:20px; display:flex; align-items:center; justify-content:center;
           position:relative; overflow:hidden; background:
           radial-gradient(120px 160px at 50% 20%, rgba(60,100,255,.18), transparent 70%); }
  .empty-state p { margin:0; font-size:14px; max-width:220px; line-height:1.5; }
  .cap-preview { position:absolute; left:50%; top:0;
           width:88%; text-align:center; font-weight:800; font-size:13px; line-height:1.35;
           color:#fff; text-shadow:0 0 3px #000,0 0 3px #000,1px 1px 1px #000,-1px -1px 1px #000;
           transition:transform 220ms var(--ease-in-out); pointer-events:none; }
  .cap-preview b { color:var(--capaccent,#F4DC1A); }

  /* Historial ─────────────────────────────────────────────────────── */
  .brand { display:flex; align-items:center; justify-content:space-between;
           margin-bottom:30px; }
  .brand-info { display:flex; align-items:center; gap:10px; }
  .historybtn { width:36px; height:36px; border-radius:10px; display:grid;
           place-content:center; background:rgba(255,255,255,.05);
           border:1px solid rgba(255,255,255,.1); color:var(--dim); cursor:pointer;
           transition:border-color 160ms var(--ease-out), color 160ms var(--ease-out),
           transform 100ms var(--ease-out); }
  .historybtn:active { transform:scale(.92); }
  @media (hover:hover) and (pointer:fine) {
    .historybtn:hover { border-color:var(--blue2); color:var(--ink); }
  }
  .update-banner { display:flex; flex-direction:column; gap:8px; padding:11px 14px;
           border-radius:12px; background:linear-gradient(135deg, rgba(61,220,132,.14), rgba(61,220,132,.06));
           border:1px solid rgba(61,220,132,.3); margin-bottom:18px; font-size:13px;
           animation:riseIn 240ms var(--ease-out) backwards; }
  .update-row { display:flex; align-items:center; gap:10px; }
  .update-banner span { flex:1; color:var(--ink); line-height:1.4; }
  .update-btn { background:linear-gradient(135deg,var(--blue1),var(--blue2)); color:#04070d;
           font-weight:800; border-radius:999px; padding:7px 14px; text-decoration:none;
           font-size:12px; flex-shrink:0; border:none; cursor:pointer; font:inherit; font-weight:800;
           transition:transform 100ms var(--ease-out); }
  .update-btn:active { transform:scale(.95); }
  .update-btn:disabled { opacity:.6; cursor:wait; }
  .update-dismiss { background:none; border:none; color:var(--dim); font-size:16px;
           cursor:pointer; padding:0 2px; flex-shrink:0; transition:transform 100ms var(--ease-out); }
  .update-dismiss:active { transform:scale(.85); }
  .update-progwrap { display:none; align-items:center; gap:10px; }
  .update-progbar { flex:1; height:6px; border-radius:999px; background:rgba(255,255,255,.08);
           overflow:hidden; }
  .update-progfill { height:100%; width:0%; border-radius:999px;
           background:linear-gradient(90deg,var(--blue1),var(--blue2));
           transition:width 200ms var(--ease-out); }
  .update-progwrap span { flex:none; font-size:11px; color:var(--dim); min-width:32px; text-align:right; }
  .preset-row { display:flex; gap:8px; align-items:center; padding-bottom:14px;
           margin-bottom:14px; border-bottom:1px solid rgba(255,255,255,.06); }
  .preset-row select { flex:1; min-width:0; }
  .preset-icon-btn { width:36px; height:36px; flex:0 0 36px; border-radius:10px;
           background:rgba(255,255,255,.05); border:1px solid rgba(255,255,255,.1);
           color:var(--ink); font-size:15px; cursor:pointer;
           transition:border-color 160ms var(--ease-out), transform 100ms var(--ease-out),
           opacity 160ms var(--ease-out); }
  .preset-icon-btn:active { transform:scale(.92); }
  .preset-icon-btn:disabled { opacity:.35; cursor:not-allowed; }
  @media (hover:hover) and (pointer:fine) {
    .preset-icon-btn:not(:disabled):hover { border-color:var(--blue2); }
  }
  .modal-overlay { position:fixed; inset:0; background:rgba(5,7,13,.65);
           backdrop-filter:blur(6px); display:flex; align-items:center;
           justify-content:center; padding:24px; z-index:50; opacity:0;
           transition:opacity 200ms var(--ease-out); }
  .modal-overlay[hidden] { display:none; }
  .modal-overlay.open { opacity:1; }
  .modal { background:linear-gradient(180deg, rgba(22,26,42,.98), rgba(9,12,20,.98));
           border:1px solid var(--card-border); border-radius:20px; padding:24px;
           width:100%; max-width:860px; max-height:80vh; overflow-y:auto;
           transform:scale(.96); opacity:0;
           transition:transform 220ms var(--ease-out), opacity 220ms var(--ease-out); }
  .modal-overlay.open .modal { transform:scale(1); opacity:1; }
  .modal-head { display:flex; align-items:center; justify-content:space-between;
           margin-bottom:18px; }
  .modal-head h2 { margin:0; font-size:18px; font-weight:800; }
  .modal-close { width:32px; height:32px; border-radius:50%; background:rgba(255,255,255,.06);
           border:none; color:var(--ink); font-size:18px; cursor:pointer;
           transition:transform 100ms var(--ease-out); }
  .modal-close:active { transform:scale(.9); }
  .history-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr));
           gap:16px; }
  .history-card { background:rgba(255,255,255,.03); border:1px solid rgba(255,255,255,.06);
           border-radius:16px; overflow:hidden; display:flex; flex-direction:column;
           animation:riseIn 240ms var(--ease-out) backwards; }
  .history-thumb { width:100%; aspect-ratio:9/16; background:#000 center/cover no-repeat;
           position:relative; cursor:pointer; }
  .history-thumb .badge { position:absolute; top:6px; right:6px; font-size:12px;
           padding:2px 6px; border-radius:999px; background:rgba(0,0,0,.55); }
  .history-thumb .playbtn { position:absolute; inset:0; display:flex; align-items:center;
           justify-content:center; background:linear-gradient(0deg, rgba(0,0,0,.35), transparent 40%);
           transition:background 160ms var(--ease-out); }
  .history-thumb .playbtn svg { width:38px; height:38px; filter:drop-shadow(0 2px 6px rgba(0,0,0,.5));
           transition:transform 120ms var(--ease-out); }
  .history-thumb .playbtn:active svg { transform:scale(.9); }
  @media (hover:hover) and (pointer:fine) {
    .history-thumb:hover .playbtn { background:linear-gradient(0deg, rgba(0,0,0,.5), transparent 55%); }
    .history-thumb:hover .playbtn svg { transform:scale(1.08); }
  }
  .history-thumb video { width:100%; height:100%; object-fit:cover; display:block; }
  .history-body { padding:10px; display:flex; flex-direction:column; gap:8px; flex:1; }
  .history-date { font-size:12px; color:var(--dim); line-height:1.4; }
  .history-actions { display:flex; gap:6px; margin-top:auto; }
  .history-actions button { border:none; border-radius:8px; font-size:12px; font-weight:700;
           padding:8px 6px; cursor:pointer; transition:transform 100ms var(--ease-out); }
  .history-actions button:active { transform:scale(.94); }
  .history-reedit { flex:1; background:linear-gradient(135deg,var(--blue1),var(--blue2)); color:#04070d; }
  .history-del { flex:0 0 32px; background:rgba(255,93,93,.14); color:var(--bad); }
  .history-empty { grid-column:1/-1; text-align:center; color:var(--dim); padding:40px 10px; }
</style>
</head>
<body>
<div class="shell">
<div class="main">
  <div class="brand">
    <div class="brand-info">
      <svg width="28" height="28" viewBox="0 0 24 24" fill="none">
        <rect x="1" y="1" width="22" height="22" rx="6.5" fill="rgba(59,111,255,.14)"
              stroke="rgba(120,150,255,.22)"/>
        <!-- Isotipo: "E" geométrica; el brazo del medio, más corto, es el
             mismo gesto de "recorte" que hace el motor (cortar silencios)
             traducido a forma. -->
        <rect x="6" y="5" width="3" height="14" rx="1.5" fill="url(#g1)"/>
        <rect x="6" y="5" width="12" height="3" rx="1.5" fill="url(#g1)"/>
        <rect x="6" y="10.5" width="8" height="3" rx="1.5" fill="url(#g1)"/>
        <rect x="6" y="16" width="12" height="3" rx="1.5" fill="url(#g1)"/>
        <defs><linearGradient id="g1" x1="4" y1="4" x2="20" y2="20" gradientUnits="userSpaceOnUse">
          <stop stop-color="#bcd2ff"/><stop offset="1" stop-color="#2148ff"/>
        </linearGradient></defs>
      </svg>
      <div>
        <div class="name">Edito</div>
        <div class="byline">by Influwa</div>
      </div>
    </div>
    <button class="historybtn" id="openHistory" title="Historial de proyectos">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="13" r="8"/>
        <path d="M12 9v4l3 2"/>
        <path d="M9 2h6M5 5l1.5 1.5"/>
      </svg>
    </button>
  </div>

  {% if update.update_available %}
  {# Chequeo de actualización resuelto DEL LADO DEL SERVIDOR, directo acá
     en index() (ver _check_for_update en app.py) – a propósito, NO vía
     fetch de JavaScript como antes. WebView2 (la ventana nativa) tiene su
     propio caché en disco que sobrevive a cerrar y reabrir la app entera,
     y insistía en servir una respuesta vieja de ese fetch aparte sin
     importar qué headers de no-cache le pusiéramos – tres rounds de
     parches distintos no lo arreglaron. Esta página ("/") sí se
     recarga entera y sin caché en cada apertura (confirmado repetidas
     veces), así que resolver el chequeo ACÁ, en la misma respuesta,
     elimina el recurso aparte que WebView2 podía cachear mal – no hay
     nada que cachear si nunca se vuelve a pedir. #}
  <div class="update-banner" id="updateBanner"
       data-url="{{ update.download_url or update.notes_url }}"
       data-version="{{ update.latest_version }}">
    <div class="update-row">
      <span id="updateMsg">Hay una versión nueva de Edito (v{{ update.latest_version }}) – la tuya es v{{ update.current_version }}.</span>
      <button type="button" class="update-btn" id="updateActionBtn">Actualizar</button>
      <button type="button" class="update-dismiss" id="updateDismiss" title="Cerrar">×</button>
    </div>
    <div class="update-progwrap" id="updateProgwrap">
      <div class="update-progbar"><div class="update-progfill" id="updateProgfill"></div></div>
      <span id="updateProgpct">0%</span>
    </div>
  </div>
  {% endif %}

  <h1>Tus tomas,<br><span class="accent">un video editado.</span></h1>
  <p class="sub">Subí tus clips en orden – llevate el MP4 con volumen y montaje profesional.</p>

  <div class="card">
    <div class="cardhead"><span class="steptag">1</span><h2>Tus tomas (en orden)</h2></div>
    <label class="addbtn" for="picker">➕ Agregar toma(s)</label>
    <input type="file" id="picker" accept="video/mp4,video/quicktime,video/*" multiple>
    <ul class="filelist" id="list"></ul>
    <p class="hint">Podés agregar de a una o varias a la vez – cada click SUMA,
    no reemplaza. El orden de la lista es el orden del montaje. Verticales (9:16)
    idealmente; si no, van con barras.</p>
  </div>

  {% if features.cut_silence or features.transitions or features.subtitles or features.sfx or features.hook_punch %}
  <div class="card">
    <div class="cardhead"><span class="steptag">2</span><h2>Features</h2></div>
    {% if features.transitions or features.subtitles %}
    <div class="preset-row">
      <select id="presetSelect"><option value="">Preset de marca…</option></select>
      <button class="preset-icon-btn" id="savePreset" title="Guardar preset actual">💾</button>
      <button class="preset-icon-btn" id="deletePreset" title="Borrar preset elegido" disabled>×</button>
    </div>
    {% endif %}
    {% if features.cut_silence %}
    <label class="frow">
      <div class="ftext">
        <div class="ftitle">Cortar silencios</div>
        <div class="fdesc">Recorta el aire muerto al inicio y final de cada toma.</div>
      </div>
      <input type="checkbox" id="cut_silence" checked>
    </label>
    {% endif %}
    {% if features.hook_punch %}
    <label class="frow">
      <div class="ftext">
        <div class="ftitle">Zoom de apertura</div>
        <div class="fdesc">La primera toma arranca con un punch de zoom y se asienta en 0.35s.</div>
      </div>
      <input type="checkbox" id="hook_punch" checked>
    </label>
    {% endif %}
    {% if features.subtitles %}
    <label class="frow">
      <div class="ftext">
        <div class="ftitle">Subtítulos automáticos</div>
        <div class="fdesc">Karaoke palabra a palabra, transcripto con whisper.</div>
      </div>
      <input type="checkbox" id="subs" checked>
    </label>
    {% endif %}
    {% if features.transitions %}
    <div class="frow">
      <div class="ftext">
        <div class="ftitle">Transición entre tomas</div>
        <div class="fdesc">El gesto visual de cada corte.</div>
      </div>
      <select id="transition">
        <option value="fade">Fundido</option>
        <option value="fadewhite">Destello blanco</option>
        <option value="fadegrays">Desaturado</option>
        <option value="zoomin">Zoom</option>
        <option value="circleopen">Iris</option>
      </select>
    </div>
    {% endif %}
    {% if features.subtitles %}
    <div class="frow">
      <div class="ftext">
        <div class="ftitle">Color de énfasis</div>
        <div class="fdesc">El color de las palabras destacadas en los subtítulos.</div>
      </div>
      <input type="color" id="accent" value="#F4DC1A">
    </div>
    <div class="frow">
      <div class="ftext">
        <div class="ftitle">Tipografía</div>
        <div class="fdesc">La fuente del texto quemado en el video.</div>
      </div>
      <div class="fontpick">
        <span class="fontpreview" id="fontPreview">Aa</span>
        <select id="font">
          {% for value, label in font_choices %}
          <option value="{{ value }}">{{ label }}</option>
          {% endfor %}
        </select>
      </div>
    </div>
    <div class="frow">
      <div class="ftext">
        <div class="ftitle">Estilo</div>
        <div class="fdesc">Negrita y/o cursiva para todo el texto quemado.</div>
      </div>
      <div class="fontpick">
        <label class="connector-toggle"><input type="checkbox" id="subBold" checked> Negrita</label>
        <label class="connector-toggle"><input type="checkbox" id="subItalic"> Cursiva</label>
      </div>
    </div>
    <div class="frow">
      <div class="ftext">
        <div class="ftitle">Posición</div>
        <div class="fdesc">Dónde se ancla el bloque de subtítulos en el cuadro.</div>
      </div>
      <select id="subpos">
        <option value="arriba">Arriba</option>
        <option value="medio">Medio</option>
        <option value="abajo" selected>Abajo</option>
      </select>
    </div>
    <div class="frow">
      <div class="ftext">
        <div class="ftitle">Texto en pantalla</div>
        <div class="fdesc">Cuánto se ve a la vez. Menos texto = más impacto por palabra.</div>
      </div>
      <select id="subLines">
        <option value="1">Poco (3 palabras, 1 línea)</option>
        <option value="2" selected>Normal (6 palabras, 2 líneas)</option>
      </select>
    </div>
    {% endif %}
    {% if features.sfx %}
    <div class="frow">
      <div class="ftext">
        <div class="ftitle">Sonido en los cortes</div>
        <div class="fdesc">Opcional: un WAV/MP3 corto (golpe o whoosh). Se alinea solo,
        40ms antes de cada transición.</div>
      </div>
      <input type="file" id="sfxfile" accept="audio/*">
    </div>
    {% endif %}
    {% if features.music %}
    <div class="frow">
      <div class="ftext">
        <div class="ftitle">Música de fondo</div>
        <div class="fdesc">Opcional: una pista de audio, en loop si hace falta,
        a volumen bajo fijo (todavía sin ducking automático).</div>
      </div>
      <input type="file" id="musicfile" accept="audio/*">
    </div>
    <div class="frow hidden" id="musicGainRow">
      <div class="ftext">
        <div class="ftitle">Volumen de la música</div>
        <div class="fdesc">Qué tan de fondo queda respecto a la voz.</div>
      </div>
      <div class="fontpick">
        <span class="fontpreview" id="musicGainLabel">22%</span>
        <input type="range" id="musicGain" min="5" max="50" value="22">
      </div>
    </div>
    <div class="frow hidden" id="musicWindowRow">
      <div class="ftext">
        <div class="ftitle">Cuándo suena</div>
        <div class="fdesc">En segundos del video final. Dejá "hasta" vacío para que llegue al final.</div>
      </div>
      <div class="fontpick">
        <input type="number" id="musicStart" min="0" step="0.5" value="0" placeholder="0">
        <span class="fontpreview">→</span>
        <input type="number" id="musicEnd" min="0" step="0.5" placeholder="fin">
      </div>
    </div>
    <div class="frow duck-section hidden" id="duckSection">
      <div class="ftext">
        <div class="ftitle">Bajar el volumen en momentos puntuales</div>
        <div class="fdesc">Ducking manual: marcá un tramo (ej. donde hablás) y la música
        baja sola ahí, y vuelve al terminar.</div>
      </div>
      <div id="duckZonesList"></div>
      <button type="button" class="addbtn duck-add" id="addDuckZone">➕ Agregar momento</button>
    </div>
    {% endif %}
  </div>
  {% endif %}

  <button class="go" id="go">Editar video</button>
</div>

<div class="side">
  <div class="card previewcard">
    <div class="cardhead"><h2>Resultado</h2></div>
    <div id="emptyState" class="empty-state">
      <div class="phone-frame">
        <svg width="26" height="26" viewBox="0 0 24 24" fill="none" opacity=".35">
          <rect x="3" y="3" width="18" height="18" rx="4" stroke="url(#g1)" stroke-width="1.6"/>
          <path d="M10 8.3L16 12L10 15.7V8.3Z" fill="url(#g1)"/>
        </svg>
        {% if features.subtitles %}
        <div class="cap-preview" id="capPreview">Así se ven tus <b>subtítulos</b></div>
        {% endif %}
      </div>
      <p>Así queda posicionado el texto – y así va a aparecer tu video, con la
      portada sugerida y el reporte de gates.</p>
    </div>
    <div id="status"></div>
    <div id="progwrap"><div id="progbar"><div id="progfill"></div></div><span id="progpct">0%</span></div>
    <div id="result"></div>
  </div>
</div>
</div>

<div class="modal-overlay" id="historyModal" hidden>
  <div class="modal">
    <div class="modal-head">
      <h2>Historial</h2>
      <button class="modal-close" id="closeHistory">×</button>
    </div>
    <div class="history-grid" id="historyList"></div>
  </div>
</div>

<script>
// Las tomas se ACUMULAN: cada selección del picker se agrega a esta lista
// (el <input type=file> del navegador reemplaza su selección en cada click,
// por eso mantenemos la lista nosotros). Cada toma es {file,name,size,
// existing} – "existing" son clips reusados de un proyecto del historial
// (ver reopenProject): no hay un File real (el navegador no puede
// reconstruir uno desde el disco del servidor), el backend los resuelve
// por referencia con el `manifest` que arma el submit.
let tomas = [];
const picker = document.getElementById('picker');
const list = document.getElementById('list');

picker.addEventListener('change', () => {
  const nuevas = Array.from(picker.files).map(f => ({
    file: f, name: f.name, size: f.size, existing: false, thumb: null,
    trimStart: null, trimEnd: null, trimOpen: false, sfxAfter: true,
  }));
  tomas.push(...nuevas);
  picker.value = '';          // permite volver a elegir el mismo archivo
  renderList();
  nuevas.forEach(t => generateThumb(t).then(() => renderList()));
});

// Miniatura por toma: 100% en el navegador, sin subir nada al servidor
// todavía (los archivos recién viajan al hacer click en Editar). Un
// <video> oculto carga el File local, se posiciona a 0.3s, y un <canvas>
// lo captura como JPG chico – así podés distinguir las tomas por su
// primer frame en vez de solo por nombre de archivo al reordenarlas.
function generateThumb(t) {
  return new Promise((resolve) => {
    if (t.existing || !t.file) { resolve(); return; }
    const url = URL.createObjectURL(t.file);
    const video = document.createElement('video');
    video.muted = true;
    video.preload = 'metadata';
    video.src = url;
    const cleanup = () => URL.revokeObjectURL(url);
    video.addEventListener('loadeddata', () => {
      video.currentTime = Math.min(0.3, (video.duration || 1) / 2);
    });
    video.addEventListener('seeked', () => {
      try {
        const canvas = document.createElement('canvas');
        canvas.width = 90; canvas.height = 160;
        const ctx = canvas.getContext('2d');
        const scale = Math.max(canvas.width / video.videoWidth, canvas.height / video.videoHeight);
        const w = video.videoWidth * scale, h = video.videoHeight * scale;
        ctx.drawImage(video, (canvas.width - w) / 2, (canvas.height - h) / 2, w, h);
        t.thumb = canvas.toDataURL('image/jpeg', 0.7);
      } catch (err) { /* codec sin soporte de canvas, etc. – fail-open, sin miniatura */ }
      cleanup();
      resolve();
    });
    video.addEventListener('error', () => { cleanup(); resolve(); });
  });
}

// Reordenar arrastrando: cada <li> es draggable; al soltar sobre otro
// se intercambian posiciones en el array `tomas` (fuente de verdad) y se
// re-renderiza toda la lista para que la numeración quede correcta.
let dragFrom = null;

// Panel de recorte manual: solo para tomas NUEVAS (una toma "existing"
// viene de un proyecto viejo, sin un File real en el navegador para
// armar un <video> de preview – ver reopenProject). trimOpen/trimStart/
// trimEnd viven en el objeto toma, no en el índice, así sobreviven a un
// reorder o a que se borre otra fila.
function fmtTrimTime(s) {
  const m = Math.floor(s / 60);
  const sec = (s % 60).toFixed(1).padStart(4, '0');
  return `${m}:${sec}`;
}

function splitLabel(name, label) {
  const m = name.match(/^(.*)(\.[^.]+)$/);
  return m ? `${m[1]} ${label}${m[2]}` : `${name} ${label}`;
}

function buildTrimPanel(t) {
  const panel = document.createElement('div');
  panel.className = 'trim-panel';
  const url = URL.createObjectURL(t.file);
  panel.innerHTML =
    `<video src="${url}" playsinline controls></video>` +
    `<div class="trim-bar">` +
      `<div class="trim-bar-track"></div>` +
      `<div class="trim-bar-selected"></div>` +
      `<div class="trim-handle start"><span class="trim-time"></span></div>` +
      `<div class="trim-handle end"><span class="trim-time"></span></div>` +
    `</div>` +
    `<div class="trim-controls">` +
      `<button type="button" class="trimPlay">▶ Reproducir selección</button>` +
      `<button type="button" class="trimSplit" title="Pausá el video donde quieras cortar y tocá acá">✂ Dividir acá</button>` +
      `<button type="button" class="trimClear">Quitar recorte</button>` +
    `</div>`;

  const video = panel.querySelector('video');
  const track = panel.querySelector('.trim-bar-track');
  const selected = panel.querySelector('.trim-bar-selected');
  const startHandle = panel.querySelector('.trim-handle.start');
  const endHandle = panel.querySelector('.trim-handle.end');
  let duration = 0;

  function syncBar() {
    if (!duration) return;
    const s = t.trimStart ?? 0;
    const e = t.trimEnd ?? duration;
    const sPct = (s / duration) * 100;
    const ePct = (e / duration) * 100;
    startHandle.style.left = sPct + '%';
    endHandle.style.left = ePct + '%';
    selected.style.left = sPct + '%';
    selected.style.width = Math.max(0, ePct - sPct) + '%';
    startHandle.querySelector('.trim-time').textContent = fmtTrimTime(s);
    endHandle.querySelector('.trim-time').textContent = fmtTrimTime(e);
  }

  video.addEventListener('loadedmetadata', () => {
    duration = video.duration;
    syncBar();
  });

  // Arrastre estilo CapCut: pointer capture para que el gesto no se corte
  // si el mouse se sale de la manija, y el video saltea al frame en vivo
  // mientras se arrastra – así se ve exactamente dónde va a cortar.
  function wireHandle(handleEl, isStart) {
    handleEl.addEventListener('pointerdown', (e) => {
      handleEl.setPointerCapture(e.pointerId);
      const onMove = (ev) => {
        if (!duration) return;
        const rect = track.getBoundingClientRect();
        const x = Math.min(rect.right, Math.max(rect.left, ev.clientX));
        let sec = ((x - rect.left) / rect.width) * duration;
        if (isStart) {
          const maxEnd = (t.trimEnd ?? duration) - 0.2;
          sec = Math.max(0, Math.min(sec, maxEnd));
          t.trimStart = sec < 0.05 ? null : sec;
        } else {
          const minStart = (t.trimStart ?? 0) + 0.2;
          sec = Math.min(duration, Math.max(sec, minStart));
          t.trimEnd = sec > duration - 0.05 ? null : sec;
        }
        video.currentTime = sec;
        syncBar();
      };
      const onUp = () => {
        handleEl.removeEventListener('pointermove', onMove);
        handleEl.removeEventListener('pointerup', onUp);
      };
      handleEl.addEventListener('pointermove', onMove);
      handleEl.addEventListener('pointerup', onUp);
    });
  }
  wireHandle(startHandle, true);
  wireHandle(endHandle, false);

  panel.querySelector('.trimPlay').addEventListener('click', () => {
    const stopAt = t.trimEnd ?? duration;
    video.currentTime = t.trimStart ?? 0;
    video.play();
    const check = () => {
      if (video.currentTime >= stopAt) {
        video.pause();
        video.removeEventListener('timeupdate', check);
      }
    };
    video.addEventListener('timeupdate', check);
  });

  panel.querySelector('.trimClear').addEventListener('click', () => {
    t.trimStart = null;
    t.trimEnd = null;
    syncBar();
  });

  // Dividir: no hay corte "en el medio" de una toma – lo que hacemos es
  // partirla en dos tomas nuevas en la lista, cada una con su propio
  // rango. Para sacar un tramo del medio: dividís dos veces (antes y
  // después) y borrás la pieza de en medio con el × de siempre.
  panel.querySelector('.trimSplit').addEventListener('click', () => {
    const splitAt = video.currentTime;
    const rangeStart = t.trimStart ?? 0;
    const rangeEnd = t.trimEnd ?? duration;
    if (splitAt <= rangeStart + 0.15 || splitAt >= rangeEnd - 0.15) {
      alert('Pausá el video en el punto donde querés dividir (adentro del rango elegido, no muy cerca de una punta).');
      return;
    }
    const idx = tomas.indexOf(t);
    if (idx === -1) return;
    const partA = { file: t.file, name: splitLabel(t.name, '(parte 1)'), size: t.size,
                    existing: false, trimStart: rangeStart, trimEnd: splitAt, trimOpen: false,
                    sfxAfter: true };
    const partB = { file: t.file, name: splitLabel(t.name, '(parte 2)'), size: t.size,
                    existing: false, trimStart: splitAt, trimEnd: rangeEnd, trimOpen: false,
                    sfxAfter: t.sfxAfter };
    tomas.splice(idx, 1, partA, partB);
    renderList();
  });

  return panel;
}

// Arrastre de un BORDE del bloque = recorte en vivo. Igual espíritu que
// la barra de antes (pointer capture, así el gesto no se corta si el
// mouse sale del handle) pero ahora escribe directo el ancho del bloque
// en vez de re-renderizar todo en cada pointermove – un render completo
// en pleno arrastre destruiría el propio handle capturado.
function renderList() {
  list.innerHTML = '';
  tomas.forEach((t, i) => {
    const li = document.createElement('li');
    li.draggable = !t.trimOpen;   // no arrastrar mientras se scrubea el video
    li.dataset.idx = i;
    li.style.animationDelay = (Math.min(i, 10) * 35) + 'ms';  // stagger corto, tope a 10 filas
    const reusedBadge = t.existing ? `<span class="reused" title="Reusada de un proyecto anterior">↺</span>` : '';
    const hasTrim = t.trimStart != null || t.trimEnd != null;
    const canTrim = !t.existing;
    const thumb = t.thumb
      ? `<span class="thumb" style="background-image:url('${t.thumb}')"></span>`
      : `<span class="thumb thumb-empty">${t.existing ? '↺' : '⋯'}</span>`;

    const row = document.createElement('div');
    row.className = 'filelist-row';
    row.innerHTML = `<span class="grip">⠿</span>` +
      thumb +
      `<span class="n">${i+1}</span>` +
      `<span class="name">${t.name}</span>` + reusedBadge +
      `<span class="size">${(t.size/1048576).toFixed(1)} MB</span>` +
      (canTrim ? `<button type="button" class="trimbtn${hasTrim ? ' active' : ''}" title="Recortar">✂</button>` : '') +
      `<button type="button" title="Quitar" onclick="quitar(${i})">×</button>`;
    li.appendChild(row);

    if (canTrim) {
      row.querySelector('.trimbtn').addEventListener('click', () => {
        t.trimOpen = !t.trimOpen;
        renderList();
      });
    }
    if (canTrim && t.trimOpen) {
      li.appendChild(buildTrimPanel(t));
    }

    li.addEventListener('dragstart', () => {
      dragFrom = i;
      li.classList.add('dragging');
    });
    li.addEventListener('dragend', () => li.classList.remove('dragging'));
    li.addEventListener('dragover', (e) => {
      e.preventDefault();
      li.classList.add('dragover');
    });
    li.addEventListener('dragleave', () => li.classList.remove('dragover'));
    li.addEventListener('drop', (e) => {
      e.preventDefault();
      li.classList.remove('dragover');
      if (dragFrom === null || dragFrom === i) return;
      const [moved] = tomas.splice(dragFrom, 1);
      tomas.splice(i, 0, moved);
      dragFrom = null;
      renderList();
    });
    list.appendChild(li);

    // Conector: el corte ENTRE esta toma y la siguiente. Solo tiene
    // sentido si la Feature de SFX está prendida y hay una toma después.
    if (document.getElementById('sfxfile') && i < tomas.length - 1) {
      const on = t.sfxAfter !== false;
      const conn = document.createElement('li');
      conn.className = 'connector';
      conn.innerHTML =
        `<label class="connector-toggle${on ? ' on' : ''}">` +
          `<input type="checkbox" ${on ? 'checked' : ''}>` +
          `<span>🔊 SFX en este corte</span>` +
        `</label>`;
      conn.querySelector('input').addEventListener('change', (e) => {
        t.sfxAfter = e.target.checked;
        conn.querySelector('.connector-toggle').classList.toggle('on', e.target.checked);
      });
      list.appendChild(conn);
    }
  });
}

function quitar(i) { tomas.splice(i, 1); renderList(); }

// ─── Historial ──────────────────────────────────────────────────────
// Vive del lado del servidor (meta.json por proyecto, ver /history en
// app.py) – sobrevive a cerrar y volver a abrir la app.
const historyBtn = document.getElementById('openHistory');
const historyModal = document.getElementById('historyModal');
const historyList = document.getElementById('historyList');
const closeHistoryBtn = document.getElementById('closeHistory');

function fmtDate(ts) {
  const d = new Date(ts * 1000);
  const date = d.toLocaleDateString('es-AR', { day: '2-digit', month: 'short' });
  const time = d.toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit' });
  return `${date} · ${time}`;
}

function renderHistoryCard(item) {
  const card = document.createElement('div');
  card.className = 'history-card';
  const thumbStyle = item.cover_url ? ` style="background-image:url('${item.cover_url}')"` : '';
  const tomasTxt = item.clip_count === 1 ? '1 toma' : `${item.clip_count} tomas`;
  card.innerHTML =
    `<div class="history-thumb"${thumbStyle} title="Ver el video">` +
      `<span class="badge">${item.all_green ? '✅' : '⚠'}</span>` +
      `<span class="playbtn"><svg viewBox="0 0 24 24" fill="none">` +
        `<circle cx="12" cy="12" r="11" fill="rgba(0,0,0,.4)" stroke="rgba(255,255,255,.7)" stroke-width="1.4"/>` +
        `<path d="M10 8.3L16 12L10 15.7V8.3Z" fill="#fff"/>` +
      `</svg></span>` +
    `</div>` +
    `<div class="history-body">` +
      `<div class="history-date">${fmtDate(item.created)}<br>${tomasTxt}</div>` +
      `<div class="history-actions">` +
        `<button class="history-reedit">Editar de nuevo</button>` +
        `<button class="history-del" title="Borrar">×</button>` +
      `</div>` +
    `</div>`;
  // Click en la miniatura: reproduce el video final ahí mismo, sin salir
  // del historial – así se sabe "de qué video se trata" sin descargarlo.
  card.querySelector('.history-thumb').addEventListener('click', function () {
    this.outerHTML = `<div class="history-thumb"><video src="${item.preview_url}" controls autoplay playsinline></video></div>`;
  });
  card.querySelector('.history-reedit').addEventListener('click', () => reopenProject(item.job_id));
  card.querySelector('.history-del').addEventListener('click', async () => {
    if (!confirm('¿Borrar este proyecto del historial? No se puede deshacer.')) return;
    try { await fetch(`/project/${item.job_id}`, { method: 'DELETE' }); } catch (err) { /* fail-open: igual lo sacamos de la vista */ }
    card.remove();
    if (!historyList.children.length) {
      historyList.innerHTML = '<div class="history-empty">Todavía no editaste ningún video.</div>';
    }
  });
  return card;
}

async function openHistoryModal() {
  historyModal.hidden = false;
  requestAnimationFrame(() => historyModal.classList.add('open'));
  historyList.innerHTML = '<div class="history-empty">Cargando…</div>';
  try {
    const resp = await fetch('/history');
    const data = await resp.json();
    if (!data.ok) { historyList.innerHTML = `<div class="history-empty">${data.error || 'Error'}</div>`; return; }
    if (!data.items.length) {
      historyList.innerHTML = '<div class="history-empty">Todavía no editaste ningún video.</div>';
      return;
    }
    historyList.innerHTML = '';
    data.items.forEach(item => historyList.appendChild(renderHistoryCard(item)));
  } catch (err) {
    historyList.innerHTML = `<div class="history-empty">Error de conexión: ${err}</div>`;
  }
}

function closeHistoryModal() {
  historyModal.classList.remove('open');
  setTimeout(() => { historyModal.hidden = true; }, 200);
}

historyBtn.addEventListener('click', openHistoryModal);
closeHistoryBtn.addEventListener('click', closeHistoryModal);
historyModal.addEventListener('click', (e) => { if (e.target === historyModal) closeHistoryModal(); });

// "Editar de nuevo": trae las tomas originales (como referencias, no
// Files reales – el navegador no puede reconstruir un File desde el
// disco del servidor) y las opciones que se usaron, y las precarga en el
// form de arriba. El usuario puede sumar más tomas o cambiar opciones
// antes de exportar; el resultado SIEMPRE es un proyecto nuevo (no pisa
// el que se reabrió).
async function reopenProject(jobId) {
  try {
    const resp = await fetch(`/project/${jobId}`);
    const data = await resp.json();
    if (!data.ok) { alert(data.error || 'No se pudo abrir ese proyecto.'); return; }

    tomas = data.clips.map(c => ({
      file: null, name: c.name, size: c.size,
      existing: true, source_job: data.job_id, source_name: c.name,
    }));
    renderList();

    const o = data.options || {};
    const el = id => document.getElementById(id);
    const setChecked = (id, val) => { if (el(id)) el(id).checked = !!val; };
    const setValue = (id, val) => {
      if (el(id) && val !== undefined) { el(id).value = val; el(id).dispatchEvent(new Event('input')); }
    };
    setChecked('cut_silence', o.cut_silence);
    setChecked('hook_punch', o.hook_punch);
    setChecked('subs', o.subs);
    setValue('transition', o.transition);
    setValue('accent', o.accent);
    setValue('font', o.font);
    setValue('subpos', o.subpos);
    setChecked('subBold', o.sub_bold !== undefined ? o.sub_bold : true);
    setChecked('subItalic', o.sub_italic);
    setValue('subLines', o.sub_lines || 2);

    closeHistoryModal();
    const status = document.getElementById('status');
    const n = data.clips.length;
    status.textContent = `Proyecto cargado (${n} toma${n === 1 ? '' : 's'}) – agregá más si querés y editá de nuevo.`;
  } catch (err) {
    alert('Error de conexión: ' + err);
  }
}

// ─── Presets de marca ───────────────────────────────────────────────
const presetSelect = document.getElementById('presetSelect');
const savePresetBtn = document.getElementById('savePreset');
const deletePresetBtn = document.getElementById('deletePreset');
let presetsCache = [];

function renderPresetOptions() {
  presetSelect.innerHTML = '<option value="">Preset de marca…</option>' +
    presetsCache.map(p => `<option value="${p.name}">${p.name}</option>`).join('');
}

async function loadPresets() {
  try {
    const resp = await fetch('/presets');
    const data = await resp.json();
    presetsCache = data.ok ? data.presets : [];
  } catch (err) {
    presetsCache = [];
  }
  renderPresetOptions();
}

if (presetSelect) {
  loadPresets();

  presetSelect.addEventListener('input', () => {
    const p = presetsCache.find(x => x.name === presetSelect.value);
    deletePresetBtn.disabled = !p;
    if (!p) return;
    const el = id => document.getElementById(id);
    const setValue = (id, val) => {
      if (el(id) && val !== undefined) { el(id).value = val; el(id).dispatchEvent(new Event('input')); }
    };
    setValue('font', p.font);
    setValue('accent', p.accent);
    setValue('subpos', p.subpos);
    setValue('transition', p.transition);
  });

  savePresetBtn.addEventListener('click', async () => {
    const name = prompt('Nombre del preset (ej. "Estilo Influwa"):');
    if (!name || !name.trim()) return;
    const el = id => document.getElementById(id);
    const body = {
      name: name.trim(),
      font: el('font') ? el('font').value : 'Arial',
      accent: el('accent') ? el('accent').value : '#F4DC1A',
      subpos: el('subpos') ? el('subpos').value : 'abajo',
      transition: el('transition') ? el('transition').value : 'fade',
    };
    try {
      const resp = await fetch('/presets', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await resp.json();
      if (!data.ok) { alert(data.error || 'No se pudo guardar el preset.'); return; }
      presetsCache = data.presets;
      renderPresetOptions();
      presetSelect.value = body.name;
      deletePresetBtn.disabled = false;
    } catch (err) {
      alert('Error de conexión: ' + err);
    }
  });

  deletePresetBtn.addEventListener('click', async () => {
    const name = presetSelect.value;
    if (!name || !confirm(`¿Borrar el preset "${name}"?`)) return;
    try {
      const resp = await fetch(`/presets/${encodeURIComponent(name)}`, { method: 'DELETE' });
      const data = await resp.json();
      presetsCache = data.ok ? data.presets : presetsCache;
    } catch (err) { /* fail-open: igual lo sacamos de la vista */ }
    renderPresetOptions();
    deletePresetBtn.disabled = true;
  });
}

// Preview en vivo de la tipografía elegida (el mismo nombre de familia
// que va a viajar al backend – ver FONT_CHOICES en app.py).
const fontSelect = document.getElementById('font');
const fontPreview = document.getElementById('fontPreview');
if (fontSelect && fontPreview) {
  const syncFontPreview = () => { fontPreview.style.fontFamily = fontSelect.value; };
  fontSelect.addEventListener('change', syncFontPreview);
  syncFontPreview();
}

// Música de fondo: el control de volumen solo tiene sentido una vez que
// hay una pista elegida, así que quedan ocultos hasta entonces.
const musicFile = document.getElementById('musicfile');
const musicGainRow = document.getElementById('musicGainRow');
const musicGain = document.getElementById('musicGain');
const musicGainLabel = document.getElementById('musicGainLabel');
const musicWindowRow = document.getElementById('musicWindowRow');
const duckSection = document.getElementById('duckSection');
if (musicFile) {
  musicFile.addEventListener('change', () => {
    const has = !!musicFile.files[0];
    if (musicGainRow) musicGainRow.classList.toggle('hidden', !has);
    if (musicWindowRow) musicWindowRow.classList.toggle('hidden', !has);
    if (duckSection) duckSection.classList.toggle('hidden', !has);
  });
}
if (musicGain && musicGainLabel) {
  musicGain.addEventListener('input', () => { musicGainLabel.textContent = musicGain.value + '%'; });
}

// Ducking manual: cada "momento" es {start, end, level} – nivel en % al
// que baja la música durante ese tramo (ver minieditor/music.py:
// _duck_expr para cómo se traduce a una rampa suave de ffmpeg).
let duckZones = [];
const duckZonesList = document.getElementById('duckZonesList');
const addDuckZoneBtn = document.getElementById('addDuckZone');

function renderDuckZones() {
  if (!duckZonesList) return;
  duckZonesList.innerHTML = '';
  duckZones.forEach((z, i) => {
    const row = document.createElement('div');
    row.className = 'duck-zone';
    row.innerHTML =
      `<span>Desde</span><input type="number" min="0" step="0.5" class="dzStart" value="${z.start}">` +
      `<span>hasta</span><input type="number" min="0" step="0.5" class="dzEnd" value="${z.end}">` +
      `<span>bajar a</span><input type="range" min="0" max="80" class="dzLevel" value="${z.level}">` +
      `<span class="dzLevelLabel">${z.level}%</span>` +
      `<button type="button" class="dzDel" title="Quitar">×</button>`;
    row.querySelector('.dzStart').addEventListener('input', (e) => {
      z.start = parseFloat(e.target.value) || 0;
    });
    row.querySelector('.dzEnd').addEventListener('input', (e) => {
      z.end = parseFloat(e.target.value) || 0;
    });
    const levelInput = row.querySelector('.dzLevel');
    const levelLabel = row.querySelector('.dzLevelLabel');
    levelInput.addEventListener('input', () => {
      z.level = parseInt(levelInput.value, 10);
      levelLabel.textContent = z.level + '%';
    });
    row.querySelector('.dzDel').addEventListener('click', () => {
      duckZones.splice(i, 1);
      renderDuckZones();
    });
    duckZonesList.appendChild(row);
  });
}

if (addDuckZoneBtn) {
  addDuckZoneBtn.addEventListener('click', () => {
    const lastEnd = duckZones.length ? duckZones[duckZones.length - 1].end : 0;
    duckZones.push({ start: lastEnd, end: lastEnd + 3, level: 30 });
    renderDuckZones();
  });
}

// Preview en vivo de color/tipografía/posición de subtítulos, todo junto,
// en el recuadro tipo teléfono del panel de resultado. Las fracciones son
// las MISMAS que POSITIONS en minieditor/captions.py – si se tocan ahí,
// hay que tocarlas acá también para que el preview no mienta.
// Se anima con transform:translateY (GPU) en vez de top (layout) – el
// offset en px se calcula desde la altura real del recuadro.
const capPreview = document.getElementById('capPreview');
const phoneFrame = document.querySelector('.phone-frame');
const accentSelect = document.getElementById('accent');
const subposSelect = document.getElementById('subpos');
const CAP_FRACTIONS = { arriba: 0.20, medio: 0.45, abajo: 0.625 };
if (capPreview) {
  const syncCapPreview = () => {
    if (accentSelect) capPreview.style.setProperty('--capaccent', accentSelect.value);
    if (fontSelect) capPreview.style.fontFamily = fontSelect.value;
    const frac = subposSelect ? (CAP_FRACTIONS[subposSelect.value] ?? CAP_FRACTIONS.abajo)
                               : CAP_FRACTIONS.abajo;
    const frameH = phoneFrame ? phoneFrame.getBoundingClientRect().height : 0;
    capPreview.style.transform = `translate(-50%, ${(frameH * frac).toFixed(1)}px)`;
  };
  [accentSelect, fontSelect, subposSelect].forEach(el => {
    if (el) el.addEventListener('input', syncCapPreview);
  });
  syncCapPreview();
}

const progwrap = document.getElementById('progwrap');
const progfill = document.getElementById('progfill');
const progpct = document.getElementById('progpct');
const emptyState = document.getElementById('emptyState');

function setProgress(pct) {
  // scaleX en vez de width: transform es GPU, width dispara layout.
  progfill.style.transform = `scaleX(${pct / 100})`;
  progpct.textContent = pct + '%';
}

document.getElementById('go').addEventListener('click', async () => {
  const go = document.getElementById('go');
  const status = document.getElementById('status');
  const result = document.getElementById('result');

  if (tomas.length < 1) { status.textContent = 'Agregá al menos una toma.'; return; }

  emptyState.style.display = 'none';
  go.disabled = true; result.innerHTML = '';
  progwrap.style.display = 'flex'; setProgress(0);
  status.innerHTML = '<span class="spin"></span>Subiendo tomas…';

  const fd = new FormData();
  // manifest: el orden final de tomas, marcando cuáles son un File nuevo
  // y cuáles son una referencia a un clip de un proyecto reusado (ver
  // reopenProject). El backend intercala ambos al armar el render.
  const manifest = tomas.map(t => t.existing
    ? { type: 'existing', job_id: t.source_job, name: t.source_name,
        trim_start: t.trimStart, trim_end: t.trimEnd }
    : { type: 'new', trim_start: t.trimStart, trim_end: t.trimEnd });
  fd.append('manifest', JSON.stringify(manifest));
  tomas.forEach(t => { if (!t.existing && t.file) fd.append('clips', t.file); });
  const el = id => document.getElementById(id);
  if (el('cut_silence')) fd.append('cut_silence', el('cut_silence').checked ? 'on' : '');
  if (el('hook_punch'))  fd.append('hook_punch', el('hook_punch').checked ? 'on' : '');
  if (el('subs'))        fd.append('subs', el('subs').checked ? 'on' : '');
  if (el('transition'))  fd.append('transition', el('transition').value);
  if (el('accent'))      fd.append('accent', el('accent').value);
  if (el('font'))        fd.append('font', el('font').value);
  if (el('subBold'))     fd.append('sub_bold', el('subBold').checked ? 'on' : '');
  if (el('subItalic'))   fd.append('sub_italic', el('subItalic').checked ? 'on' : '');
  if (el('subLines'))    fd.append('sub_lines', el('subLines').value);
  if (el('subpos'))      fd.append('subpos', el('subpos').value);
  if (el('sfxfile') && el('sfxfile').files[0]) fd.append('sfx', el('sfxfile').files[0]);
  if (el('musicfile') && el('musicfile').files[0]) {
    fd.append('music', el('musicfile').files[0]);
    fd.append('music_gain', (el('musicGain') ? el('musicGain').value : 22) / 100);
    if (el('musicStart')) fd.append('music_start', el('musicStart').value || '0');
    if (el('musicEnd') && el('musicEnd').value) fd.append('music_end', el('musicEnd').value);
    fd.append('music_duck', JSON.stringify(duckZones));
  }
  if (el('sfxfile') && el('sfxfile').files[0]) {
    const gaps = tomas.slice(0, -1).map(x => x.sfxAfter !== false);
    fd.append('sfx_gaps', JSON.stringify(gaps));
  }

  try {
    const resp = await fetch('/render', { method:'POST', body: fd });
    const data = await resp.json();
    if (!data.ok) {
      status.innerHTML = `<div class="err">${data.error}</div>`;
      go.disabled = false;
      return;
    }
    await pollStatus(data.job_id);
  } catch (err) {
    status.innerHTML = `<div class="err">Error de conexión: ${err}</div>`;
  }

  go.disabled = false;
});

// Guardar como: en la ventana nativa (pywebview) usamos el diálogo de
// "Guardar como" del sistema vía el puente Api.save_file (elegís carpeta
// Y nombre ahí mismo). El <a download> de un navegador normal no deja
// elegir ninguna de las dos cosas – solo cae ahí en modo navegador
// (MINIEDITOR_BROWSER=1), donde no hay puente pywebview disponible.
async function saveViaDialog(url, filename, kind) {
  const jobId = url.split('/').filter(Boolean).pop();
  if (window.pywebview && window.pywebview.api && window.pywebview.api.save_file) {
    const res = await window.pywebview.api.save_file(jobId, filename, kind);
    if (res && res.ok) return;
    if (res && !res.cancelled) alert('No se pudo guardar: ' + (res.error || 'error desconocido'));
    return;
  }
  const a = document.createElement('a');
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
}
document.addEventListener('click', (e) => {
  const btn = e.target.closest('.dl-btn');
  if (!btn) return;
  saveViaDialog(btn.dataset.url, btn.dataset.name, btn.dataset.kind);
});

// El render corre en background en el servidor; acá solo preguntamos
// "¿cómo vas?" cada 1.2s en vez de tener la request de /render colgada
// todo el tiempo que tarda ffmpeg+whisper.
async function pollStatus(jobId) {
  const status = document.getElementById('status');
  const result = document.getElementById('result');

  while (true) {
    await new Promise(r => setTimeout(r, 1200));

    let data;
    try {
      const resp = await fetch(`/status/${jobId}`);
      data = await resp.json();
    } catch (err) {
      status.innerHTML = `<div class="err">Error de conexión: ${err}</div>`;
      return;
    }

    if (!data.ok) {
      status.innerHTML = `<div class="err">${data.error}</div>`;
      progwrap.style.display = 'none';
      return;
    }
    if (data.status === 'error') {
      status.innerHTML = `<div class="err">${data.error}</div>`;
      progwrap.style.display = 'none';
      return;
    }
    if (data.status === 'done') {
      setProgress(100);
      status.innerHTML = 'Listo. Gates: ' + (data.all_green
        ? '<span class="status-ok">todos ✅</span>' : 'revisar ⚠');
      const coverImg = data.cover_url ? `<img class="cover" src="${data.cover_url}" alt="Portada sugerida">` : '';
      const coverLink = data.cover_url
        ? `<button type="button" class="dl-btn secondary" data-url="${data.cover_url}" data-name="Edito_portada.jpg" data-kind="cover">⬇ Portada sugerida (JPG)</button>`
        : '';
      result.innerHTML = `<div class="fade-in">` + coverImg +
                         `<video id="preview" src="${data.preview_url}" controls playsinline></video>` +
                         `<button type="button" class="dl-btn" data-url="${data.url}" data-name="Edito.mp4" data-kind="video">⬇ Descargar MP4</button>` +
                         coverLink +
                         `<div class="gates">${data.gates}</div></div>`;
      setTimeout(() => { progwrap.style.display = 'none'; }, 600);
      return;
    }
    if (data.status === 'awaiting_review') {
      progwrap.style.display = 'none';
      status.textContent = 'Revisá los subtítulos antes de continuar:';
      renderSubsReview(jobId, data.words);
      return;
    }
    // running: mostramos el paso actual y el % que reporta el servidor
    setProgress(data.progress || 0);
    status.innerHTML = `<span class="spin"></span>${data.step || 'Editando…'}`;
  }
}

// Revisión de subtítulos: el job queda pausado del lado del servidor
// (ver /confirm_subs) hasta que mandemos las palabras corregidas. Los
// tiempos NO se editan acá – solo el texto, o borrar una palabra entera
// si el ASR alucinó de más.
function renderSubsReview(jobId, words) {
  const result = document.getElementById('result');
  let editWords = words.map(w => ({ ...w }));

  const wrap = document.createElement('div');
  wrap.className = 'card fade-in';
  wrap.innerHTML =
    `<div class="cardhead"><h2>Revisá la transcripción</h2></div>` +
    `<p class="hint">Corregí lo que el ASR haya escuchado mal. Los tiempos ` +
    `quedan igual – si una palabra sobra, borrala con ×.</p>` +
    `<div id="subsedit"></div>` +
    `<button class="go" id="confirmSubs" style="margin-top:14px">Confirmar y continuar</button>`;
  result.innerHTML = '';
  result.appendChild(wrap);

  function renderWords() {
    const box = document.getElementById('subsedit');
    box.innerHTML = '';
    editWords.forEach((w, i) => {
      const row = document.createElement('div');
      row.className = 'subword';
      const input = document.createElement('input');
      input.type = 'text';
      input.value = w.w;
      input.addEventListener('input', () => { editWords[i].w = input.value; });
      const del = document.createElement('button');
      del.type = 'button';
      del.title = 'Quitar';
      del.textContent = '×';
      del.addEventListener('click', () => { editWords.splice(i, 1); renderWords(); });
      row.appendChild(input);
      row.appendChild(del);
      box.appendChild(row);
    });
  }
  renderWords();

  document.getElementById('confirmSubs').addEventListener('click', async () => {
    const btn = document.getElementById('confirmSubs');
    const status = document.getElementById('status');
    btn.disabled = true;
    try {
      const resp = await fetch(`/confirm_subs/${jobId}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ words: editWords })
      });
      const data = await resp.json();
      if (!data.ok) {
        status.innerHTML = `<div class="err">${data.error}</div>`;
        btn.disabled = false;
        return;
      }
      result.innerHTML = '';
      progwrap.style.display = 'flex';
      status.innerHTML = '<span class="spin"></span>Aplicando correcciones…';
      await pollStatus(jobId);
    } catch (err) {
      status.innerHTML = `<div class="err">Error de conexión: ${err}</div>`;
      btn.disabled = false;
    }
  });
}

// El banner de actualización ya viene resuelto del lado del servidor
// (ver index() en app.py) – acá solo hace falta el botón de cerrar, que
// saca el elemento del DOM directo (nada que mostrar/ocultar con clases:
// si no hay actualización, el div ni existe en la página).
document.getElementById('updateDismiss')?.addEventListener('click', (e) => {
  e.currentTarget.closest('.update-banner').remove();
});

// Botón "Actualizar": baja el .exe nuevo con progreso y, en la ventana
// nativa, se reemplaza y reabre solo. En modo navegador (sin puente
// pywebview) no hay forma de reemplazar el .exe desde ahí – cae al link
// directo de GitHub de siempre, que el navegador descarga por su cuenta.
(() => {
  const banner = document.getElementById('updateBanner');
  if (!banner) return;
  const btn = document.getElementById('updateActionBtn');
  const msg = document.getElementById('updateMsg');
  const progwrap = document.getElementById('updateProgwrap');
  const progfill = document.getElementById('updateProgfill');
  const progpct = document.getElementById('updateProgpct');
  const fallbackUrl = banner.dataset.url;
  let state = 'idle';

  async function pollDownload() {
    while (state === 'downloading') {
      await new Promise(r => setTimeout(r, 500));
      let data;
      try {
        const resp = await fetch('/update/status?t=' + Date.now(), { cache: 'no-store' });
        data = await resp.json();
      } catch (err) { continue; }
      if (data.status === 'downloading') {
        const pct = data.percent || 0;
        progfill.style.width = pct + '%';
        progpct.textContent = pct + '%';
      } else if (data.status === 'ready') {
        state = 'ready';
        progwrap.style.display = 'none';
        btn.disabled = false;
        btn.textContent = 'Reiniciar y actualizar';
      } else if (data.status === 'error') {
        state = 'error';
        progwrap.style.display = 'none';
        btn.disabled = false;
        btn.textContent = 'Reintentar';
        msg.textContent = 'No se pudo descargar la actualización. Probá de nuevo.';
      }
    }
  }

  btn.addEventListener('click', async () => {
    const api = window.pywebview && window.pywebview.api;
    if (!api || !api.apply_update) {
      window.open(fallbackUrl, '_blank');
      return;
    }
    if (state === 'ready') {
      btn.disabled = true;
      btn.textContent = 'Reiniciando…';
      await api.apply_update();  // si funciona, la app se cierra acá mismo
      return;
    }
    if (state === 'idle' || state === 'error') {
      state = 'downloading';
      btn.disabled = true;
      btn.textContent = 'Descargando…';
      progwrap.style.display = 'flex';
      progfill.style.width = '0%';
      progpct.textContent = '0%';
      try {
        const resp = await fetch('/update/download', { method: 'POST' });
        const data = await resp.json();
        if (!data.ok) throw new Error(data.error || 'no se pudo iniciar');
        pollDownload();
      } catch (err) {
        state = 'error';
        progwrap.style.display = 'none';
        btn.disabled = false;
        btn.textContent = 'Reintentar';
      }
    }
  });
})();
</script>
</body>
</html>
"""


@app.get("/")
def index():
    return render_template_string(PAGE, features=type("F", (), FEATURES),
                                   font_choices=FONT_CHOICES, favicon=FAVICON_HREF,
                                   embedded_fonts=EMBEDDED_FONT_FILES,
                                   update=_check_for_update())


@app.after_request
def _no_cache_index(resp):
    # WebView2 (la ventana nativa) tiene su propio caché en disco que
    # sobrevive a cerrar y reabrir la app entera (no es como una pestaña
    # de navegador que arranca de cero) – sin esto, un relanzamiento podría
    # seguir sirviendo la página vieja de caché en vez de pedirla de
    # nuevo. /preview, /cover, /download quedan afuera a propósito: esos
    # SÍ quieren cachear/soportar range requests (conditional=True en
    # send_file).
    if request.path == "/":
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
    return resp


def _await_subs_review(job_id: str, words: list[dict]) -> list[dict]:
    """Pausa el hilo del job hasta que /confirm_subs reciba la corrección
    humana. El texto que se quema es SIEMPRE el que confirma la persona
    – whisper solo aporta el primer borrador y los timestamps (mismo
    principio que el guion verbatim de minieditor/captions.py, aplicado
    acá sin requerir un guion previo)."""
    event = threading.Event()
    with JOBS_LOCK:
        JOB_EVENTS[job_id] = event
        JOBS[job_id].update(status="awaiting_review",
                             step="Revisá los subtítulos", words=words)
    if not event.wait(timeout=JOB_TTL_HOURS * 3600):
        raise TimeoutError("Nadie confirmó la revisión de subtítulos a tiempo.")
    with JOBS_LOCK:
        edited = JOB_EDITS.pop(job_id, words)
        JOB_EVENTS.pop(job_id, None)
        JOBS[job_id].update(status="running", step="Aplicando correcciones…")
    return edited


def _run_job(job_id: str, job_dir: str, paths: list[str], options: dict,
            clip_meta: list[dict] | None = None,
            trims: list[tuple[float | None, float | None]] | None = None) -> None:
    """Corre el pipeline completo en un hilo aparte.

    Es el MISMO pipeline que la línea de comandos, con dos diferencias de
    interfaz: 1) las opciones vienen del form en vez de un EDL, y 2) acá
    se arma un Edl "liviano" (shots=[]) solo para poder reusar sfx.py tal
    cual está, sin duplicar su lógica de amix/declick/preroll.
    """
    # Recorte manual por toma (ver panel de recorte en app.py): pisa el
    # recorte automático SOLO en las tomas donde el usuario eligió un
    # rango a mano. Las demás siguen la regla de "cut_silence" de siempre.
    has_manual_trim = any(t and (t[0] is not None or t[1] is not None) for t in (trims or []))
    run_trim_stage = bool(options["cut_silence"]) or has_manual_trim

    # Progreso real: la secuencia de etapas se conoce de antemano a partir
    # de las options (cada una corresponde a exactamente un llamado a
    # step() más abajo, en el mismo orden), así que el % es determinista
    # en vez de una estimación por tiempo transcurrido.
    stage_count = sum([
        run_trim_stage, True, True, bool(options["sfx_path"]),
        bool(options["music_path"]), True, bool(options["subs"]), True,
    ])
    stage_done = 0

    def step(msg: str) -> None:
        nonlocal stage_done
        stage_done += 1
        with JOBS_LOCK:
            JOBS[job_id]["step"] = msg
            JOBS[job_id]["progress"] = round(100 * stage_done / stage_count)

    workdir = tempfile.mkdtemp(prefix=f"miniedit_{job_id}_")
    try:
        t0 = time.time()

        # 1 · recorte, automático y/o manual por toma
        if run_trim_stage:
            step("Recortando…")
            new_paths = []
            for i, p in enumerate(paths):
                ts, te = trims[i] if trims and i < len(trims) else (None, None)
                if ts is not None or te is not None:
                    new_paths.append(trim.manual_trim(p, workdir, i, ts or 0.0, te))
                elif options["cut_silence"]:
                    new_paths.append(trim.trim_shot(p, workdir, i)[0])
                else:
                    new_paths.append(p)
            paths = new_paths

        # 2 · canvas 1080×1920 @30 + cadena de voz (siempre: sin esto no
        #     se pueden unir clips de distinta resolución)
        step("Normalizando canvas y voz…")
        paths = [normalize.normalize_shot(p, workdir, i, fade_in=(i == 0),
                                          hook_punch=(i == 0 and options["hook_punch"]))
                 for i, p in enumerate(paths)]
        durs = [stream_duration(p, "video") for p in paths]

        # 3 · montaje (la transición es "fade" hasta activar la Feature 2).
        #     Cada frontera decide su propio SFX según sfx_gaps (ver
        #     connectors en la lista de tomas) – ya no es todo-o-nada.
        step("Montando transiciones…")
        sfx_gaps = options.get("sfx_gaps") or [True] * max(0, len(paths) - 1)
        bounds = [Boundary(transition=options["transition"], duration_s=0.35,
                            sfx=(options["sfx_path"] if sfx_gaps[i] else None),
                            sfx_peak_offset_s=options["sfx_peak"] or 0.0,
                            sfx_gain=0.82)
                  for i in range(len(paths) - 1)]
        video, cut_times = concat.concat_shots(paths, bounds, workdir)
        expected = sum(durs) - sum(b.duration_s for b in bounds)

        # 4 · pista de sonido (Feature 4) – ANTES de loudnorm, siempre.
        if options["sfx_path"]:
            step("Agregando sonido en los cortes…")
            edl = Edl(shots=[], boundaries=bounds,
                      opening_sting=options["sfx_path"],
                      closing_sting=options["sfx_path"])
            video = sfx.apply_sfx_track(video, edl, bounds, cut_times, workdir)

        # 4b · música de fondo (Feature 6) – después del SFX, antes de
        # loudnorm: mismo invariante, todo lo que suma buses va antes del
        # limitador final.
        if options["music_path"]:
            step("Sumando música de fondo…")
            video = music.apply_music(video, options["music_path"], workdir,
                                      gain=options["music_gain"],
                                      start_s=options.get("music_start", 0.0),
                                      end_s=options.get("music_end"),
                                      duck_zones=options.get("music_duck"))

        # 5 · loudness – siempre, y siempre al final del audio
        step("Ajustando loudness (-14 LUFS)…")
        video = loudnorm.apply_loudnorm(video, workdir)

        # 6 · subtítulos subs-last (Feature 3): whisper sobre el video FINAL
        if options["subs"]:
            step("Transcribiendo subtítulos (whisper)…")
            words = asr.transcribe_words(video)
            if words:
                words = _await_subs_review(job_id, words)
                ass = captions.build_ass(words, os.path.join(workdir, "subs.ass"),
                                         accent=options["accent"],
                                         font=options["font"],
                                         position=options["subpos"],
                                         bold=options["sub_bold"],
                                         italic=options["sub_italic"],
                                         max_lines=options["sub_lines"])
                video = captions.burn(video, ass, workdir)

        # 7 · gates sobre el entregable. check_edges y cut_times solo si
        # hay SFX: sin diseño sonoro, el aire del recorte hace que el
        # primer/último medio segundo sea silencio A PROPÓSITO, y sin
        # cortes con SFX no hay energía que verificar en las fronteras.
        step("Verificando gates…")
        # G8 solo debe mirar los cortes que REALMENTE llevan SFX – con
        # sfx_gaps, ya no es "todos o ninguno" (ver Boundary más arriba).
        sfx_cut_times = ([c for c, on in zip(cut_times, sfx_gaps) if on]
                         if options["sfx_path"] else None)
        results = gates.run_gates(
            video, expected_duration_s=expected,
            cut_times=sfx_cut_times or None,
            check_edges=bool(options["sfx_path"]),
            check_hook_punch=options["hook_punch"])

        out = os.path.join(job_dir, "final.mp4")
        shutil.copy2(video, out)

        # Portada sugerida: ornamento, nunca tumba el render (invariante 4).
        cover_url = None
        try:
            cover = thumbnail.pick_cover(
                out, os.path.join(job_dir, "cover.jpg"),
                stream_duration(out, "video"))
            if cover:
                cover_url = f"/cover/{job_id}"
        except Exception:
            pass

        report = "\n".join(
            f"{'✅' if r['ok'] else '❌'} {name}: {r['detail']}"
            for name, r in results.items())
        report += f"\n⏱ {time.time() - t0:.0f}s"
        all_green = all(r["ok"] for r in results.values())

        # meta.json: lo que persiste en DISCO para el historial. JOBS es
        # en memoria y se pierde al reiniciar la app – esto no. También es
        # lo que lee "reeditar" para reconstruir la lista de tomas y las
        # opciones sin pedirte que subas los clips de nuevo.
        try:
            with open(os.path.join(job_dir, "meta.json"), "w", encoding="utf-8") as f:
                json.dump({
                    "job_id": job_id, "created": t0,
                    "clips": clip_meta or [],
                    "options": {k: v for k, v in options.items()
                                if k not in ("sfx_path", "sfx_peak", "music_path")},
                    "had_sfx": bool(options.get("sfx_path")),
                    "had_music": bool(options.get("music_path")),
                    "all_green": all_green,
                    "has_cover": cover_url is not None,
                }, f)
        except OSError:
            pass  # el historial es un extra – no tumba un render que sí funcionó

        with JOBS_LOCK:
            JOBS[job_id].update(
                status="done", step="Listo", progress=100,
                url=f"/download/{job_id}",
                preview_url=f"/preview/{job_id}",
                cover_url=cover_url,
                all_green=all_green,
                gates=report)
    except Exception:
        with JOBS_LOCK:
            JOBS[job_id].update(status="error",
                                 error=traceback.format_exc()[-1200:])
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


CLIP_NAME_RE = re.compile(r"^upload_\d{3}\.mp4$")


def _parse_trim(item) -> tuple[float | None, float | None]:
    """(trim_start, trim_end) desde una entrada del manifest, o (None,None)
    si no vino o vino corrupta – un recorte manual mal formado nunca debe
    tumbar el render, solo se ignora."""
    if not isinstance(item, dict):
        return None, None
    ts, te = item.get("trim_start"), item.get("trim_end")
    try:
        ts = max(0.0, float(ts)) if ts is not None else None
    except (TypeError, ValueError):
        ts = None
    try:
        te = max(0.0, float(te)) if te is not None else None
    except (TypeError, ValueError):
        te = None
    if ts is not None and te is not None and te <= ts:
        return None, None
    return ts, te


@app.post("/render")
def do_render():
    """Las tomas llegan de dos formas, mezclables en cualquier orden:

    - Nuevas: en el multipart `clips`, como siempre.
    - Reusadas de un proyecto viejo (reeditar desde el historial): una
      referencia {job_id, name} en `manifest`, sin volver a subir el
      archivo – un <input type=file> no puede "recordar" un File que
      nunca vino del disco del usuario en esta sesión, así que el reuso
      se resuelve acá, copiando el .mp4 original al job NUEVO (nunca se
      toca el proyecto viejo: reeditar es siempre "guardar como").

    Sin `manifest`, el comportamiento es el de siempre: todo lo que venga
    en `clips`, en orden.
    """
    files = request.files.getlist("clips")
    manifest_raw = request.form.get("manifest")

    _cleanup_old_jobs()

    job_id = uuid.uuid4().hex[:10]
    job_dir = os.path.join(JOBS_DIR, job_id)
    os.makedirs(job_dir)

    paths: list[str] = []
    clip_meta: list[dict] = []
    trims: list[tuple[float | None, float | None]] = []

    if manifest_raw:
        try:
            manifest = json.loads(manifest_raw)
            assert isinstance(manifest, list)
        except (ValueError, TypeError, AssertionError):
            shutil.rmtree(job_dir, ignore_errors=True)
            return {"ok": False, "error": "Manifest inválido."}

        new_files = iter(files)
        for i, item in enumerate(manifest):
            dest_name = f"upload_{i:03d}.mp4"
            dest = os.path.join(job_dir, dest_name)
            if isinstance(item, dict) and item.get("type") == "existing":
                src_job = "".join(c for c in str(item.get("job_id", "")) if c.isalnum())
                src_name = str(item.get("name", ""))
                if not CLIP_NAME_RE.match(src_name):
                    shutil.rmtree(job_dir, ignore_errors=True)
                    return {"ok": False, "error": "Referencia de clip inválida."}
                src = os.path.join(JOBS_DIR, src_job, src_name)
                if not os.path.isfile(src):
                    shutil.rmtree(job_dir, ignore_errors=True)
                    return {"ok": False, "error": "Uno de los clips reusados ya no existe (¿venció?)."}
                shutil.copy2(src, dest)
            else:
                f = next(new_files, None)
                if f is None:
                    shutil.rmtree(job_dir, ignore_errors=True)
                    return {"ok": False, "error": "Faltan archivos nuevos en la subida."}
                f.save(dest)
            paths.append(dest)
            clip_meta.append({"name": dest_name, "size": os.path.getsize(dest)})
            trims.append(_parse_trim(item))
    else:
        if not files or not files[0].filename:
            shutil.rmtree(job_dir, ignore_errors=True)
            return {"ok": False, "error": "No subiste ninguna toma."}
        for i, f in enumerate(files):
            dest_name = f"upload_{i:03d}.mp4"
            p = os.path.join(job_dir, dest_name)
            f.save(p)
            paths.append(p)
            clip_meta.append({"name": dest_name, "size": os.path.getsize(p)})
            trims.append((None, None))

    if not paths:
        shutil.rmtree(job_dir, ignore_errors=True)
        return {"ok": False, "error": "No subiste ninguna toma."}
    if len(paths) > 25:
        shutil.rmtree(job_dir, ignore_errors=True)
        return {"ok": False, "error": "Máximo 25 tomas."}

    # Cada feature solo actúa si está activada en FEATURES.
    cut_silence = FEATURES["cut_silence"] and request.form.get("cut_silence") == "on"
    hook_punch = FEATURES["hook_punch"] and request.form.get("hook_punch") == "on"
    subs = FEATURES["subtitles"] and request.form.get("subs") == "on"
    transition = (request.form.get("transition", "fade")
                  if FEATURES["transitions"] else "fade")
    accent = request.form.get("accent", "#F4DC1A")
    # Whitelist: font y subpos viajan a una línea de estilo .ass tal cual
    # (ver captions.py) – nunca confiar en texto libre del form ahí.
    font = request.form.get("font", "Arial")
    if font not in {value for value, _ in FONT_CHOICES}:
        font = "Arial"
    subpos = request.form.get("subpos", "abajo")
    if subpos not in {"arriba", "medio", "abajo"}:
        subpos = "abajo"
    sub_bold = request.form.get("sub_bold", "on") == "on"
    sub_italic = request.form.get("sub_italic") == "on"
    try:
        sub_lines = int(request.form.get("sub_lines", 2))
    except (TypeError, ValueError):
        sub_lines = 2
    if sub_lines not in (1, 2):
        sub_lines = 2

    # SFX (Feature 4): opcional, un solo archivo para todos los cortes.
    # Sin sidecar .peak (el usuario lo acaba de subir), así que medimos el
    # pico una vez acá – fail-open: si el análisis falla, sfx.py igual
    # degrada solo (ver minieditor/sfx.py:_peak_of).
    sfx_path, sfx_peak = None, None
    sfx_file = request.files.get("sfx")
    if FEATURES["sfx"] and sfx_file and sfx_file.filename:
        sfx_path = os.path.join(job_dir, "sfx" + os.path.splitext(sfx_file.filename)[1])
        sfx_file.save(sfx_path)
        try:
            sfx_peak = measure_peak(sfx_path)
        except Exception:
            sfx_peak = 0.0

    # Música de fondo (Feature 6): opcional, un archivo, volumen fijo.
    music_path = None
    music_file = request.files.get("music")
    if FEATURES["music"] and music_file and music_file.filename:
        music_path = os.path.join(job_dir, "music" + os.path.splitext(music_file.filename)[1])
        music_file.save(music_path)
    try:
        music_gain = float(request.form.get("music_gain", music.DEFAULT_GAIN))
    except (TypeError, ValueError):
        music_gain = music.DEFAULT_GAIN
    music_gain = max(0.03, min(0.6, music_gain))  # cota de cordura: nunca ahoga la voz sin querer
    try:
        music_start = max(0.0, float(request.form.get("music_start", 0) or 0))
    except (TypeError, ValueError):
        music_start = 0.0
    music_end_raw = request.form.get("music_end", "")
    try:
        music_end = float(music_end_raw) if music_end_raw not in ("", None) else None
    except (TypeError, ValueError):
        music_end = None
    if music_end is not None and music_end <= music_start:
        music_start, music_end = 0.0, None  # ventana sin sentido: la ignoramos, no rompemos el render

    # Ducking manual: zonas [inicio, fin, nivel%] donde la música baja y
    # vuelve sola. Cualquier zona corrupta se descarta en silencio – es un
    # ornamento, no debe tumbar el render (invariante 4 de AGENT.md).
    try:
        duck_raw = json.loads(request.form.get("music_duck", "[]"))
    except (ValueError, TypeError):
        duck_raw = []
    music_duck = []
    if isinstance(duck_raw, list):
        for z in duck_raw:
            if not isinstance(z, dict):
                continue
            try:
                zs = max(0.0, float(z.get("start", 0)))
                ze = float(z.get("end", 0))
                lvl = max(0.0, min(100.0, float(z.get("level", 30))))
            except (TypeError, ValueError):
                continue
            if ze > zs:
                music_duck.append((zs, ze, lvl / 100))
    music_duck.sort(key=lambda z: z[0])

    # SFX por corte (Feature 4, refinado): antes era todo-o-nada en cada
    # frontera; ahora cada corte puede prender/apagar el golpe individual.
    # sfx_gaps[i] = True → la frontera entre toma i y toma i+1 lleva SFX.
    try:
        sfx_gaps_raw = json.loads(request.form.get("sfx_gaps", "[]"))
        sfx_gaps = [bool(x) for x in sfx_gaps_raw] if isinstance(sfx_gaps_raw, list) else []
    except (ValueError, TypeError):
        sfx_gaps = []
    n_bounds = max(0, len(paths) - 1)
    if len(sfx_gaps) != n_bounds:
        sfx_gaps = [True] * n_bounds  # default: como antes, SFX en todos los cortes

    options = {"cut_silence": cut_silence, "hook_punch": hook_punch, "subs": subs,
               "transition": transition, "accent": accent, "font": font,
               "subpos": subpos, "sub_bold": sub_bold, "sub_italic": sub_italic,
               "sub_lines": sub_lines, "sfx_path": sfx_path, "sfx_peak": sfx_peak,
               "sfx_gaps": sfx_gaps, "music_path": music_path, "music_gain": music_gain,
               "music_start": music_start, "music_end": music_end, "music_duck": music_duck}

    with JOBS_LOCK:
        JOBS[job_id] = {"status": "running", "step": "En cola…", "progress": 0,
                         "created": time.time()}

    threading.Thread(target=_run_job, args=(job_id, job_dir, paths, options, clip_meta, trims),
                     daemon=True).start()

    return {"ok": True, "job_id": job_id}


@app.get("/status/<job_id>")
def status(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        return {"ok": False, "error": "No existe ese trabajo (¿ya expiró?)"}, 404
    return {"ok": True, **job}


@app.post("/confirm_subs/<job_id>")
def confirm_subs(job_id):
    """Recibe la corrección humana de la transcripción y despierta al
    hilo del job (ver _await_subs_review). El texto es libre; los
    timestamps viajan tal cual los devolvió whisper – la sync no se
    edita a mano, solo el contenido."""
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        event = JOB_EVENTS.get(job_id)
    if not job or not event or job.get("status") != "awaiting_review":
        return {"ok": False, "error": "No hay una revisión pendiente para ese trabajo."}, 404

    data = request.get_json(silent=True) or {}
    raw_words = data.get("words")
    if not isinstance(raw_words, list) or not raw_words:
        return {"ok": False, "error": "Mandá al menos una palabra."}, 400

    clean = []
    try:
        for w in raw_words:
            text = str(w.get("w", "")).strip()[:60]
            if not text:
                continue        # el usuario la vació: equivale a borrarla
            clean.append({"w": text, "t0": float(w["t0"]), "t1": float(w["t1"])})
    except (KeyError, TypeError, ValueError):
        return {"ok": False, "error": "Formato de palabra inválido."}, 400
    if not clean:
        return {"ok": False, "error": "No quedó ninguna palabra."}, 400

    with JOBS_LOCK:
        JOB_EDITS[job_id] = clean
    event.set()
    return {"ok": True}


@app.get("/download/<job_id>")
def download(job_id):
    safe = "".join(c for c in job_id if c.isalnum())
    path = os.path.join(JOBS_DIR, safe, "final.mp4")
    if not os.path.exists(path):
        return "No existe ese trabajo", 404
    return send_file(path, as_attachment=True, download_name="editado.mp4")


@app.get("/preview/<job_id>")
def preview(job_id):
    """Mismo archivo que /download, pero inline (sin Content-Disposition:
    attachment) para que el <video> del resultado lo pueda reproducir
    en vez de forzar la descarga."""
    safe = "".join(c for c in job_id if c.isalnum())
    path = os.path.join(JOBS_DIR, safe, "final.mp4")
    if not os.path.exists(path):
        return "No existe ese trabajo", 404
    return send_file(path, as_attachment=False, conditional=True)


@app.get("/cover/<job_id>")
def cover(job_id):
    safe = "".join(c for c in job_id if c.isalnum())
    path = os.path.join(JOBS_DIR, safe, "cover.jpg")
    if not os.path.exists(path):
        return "No existe esa portada", 404
    return send_file(path, as_attachment=False, conditional=True)


@app.get("/fonts/<path:filename>")
def font_file(filename):
    """Sirve las fuentes propias (assets/fonts/) para el @font-face del
    preview – whitelist explícita, nunca un path arbitrario del disco."""
    if filename not in EMBEDDED_FONT_FILES.values():
        return "No existe esa fuente", 404
    return send_from_directory(FONTS_DIR, filename, conditional=True)


def _job_meta(safe_id: str) -> dict | None:
    path = os.path.join(JOBS_DIR, safe_id, "meta.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


@app.get("/history")
def history():
    """Historial: cualquier job con meta.json es un proyecto terminado.
    Vive en DISCO (no en el dict JOBS en memoria) – sobrevive a reiniciar
    la app, y se autolimpia solo con _cleanup_old_jobs (30 días)."""
    items = []
    try:
        names = os.listdir(JOBS_DIR)
    except OSError:
        names = []
    for name in names:
        safe = "".join(c for c in name if c.isalnum())
        if safe != name:
            continue
        meta = _job_meta(safe)
        if not meta:
            continue
        items.append({
            "job_id": safe,
            "created": meta.get("created", 0),
            "clip_count": len(meta.get("clips", [])),
            "all_green": meta.get("all_green", False),
            "cover_url": f"/cover/{safe}" if meta.get("has_cover") else None,
            "preview_url": f"/preview/{safe}",
            "download_url": f"/download/{safe}",
        })
    items.sort(key=lambda it: it["created"], reverse=True)
    return {"ok": True, "items": items}


@app.get("/project/<job_id>")
def project(job_id):
    """Lo que necesita 'Editar de nuevo': las tomas originales (si siguen
    en disco) y las opciones que se usaron, para prellenar el form."""
    safe = "".join(c for c in job_id if c.isalnum())
    meta = _job_meta(safe)
    if not meta:
        return {"ok": False, "error": "No existe ese proyecto (¿venció?)."}, 404
    clips = []
    for c in meta.get("clips", []):
        name = c.get("name", "") if isinstance(c, dict) else ""
        if CLIP_NAME_RE.match(name) and os.path.isfile(os.path.join(JOBS_DIR, safe, name)):
            clips.append({"name": name, "size": c.get("size", 0)})
    return {"ok": True, "job_id": safe, "clips": clips,
            "options": meta.get("options", {}), "had_sfx": meta.get("had_sfx", False)}


@app.delete("/project/<job_id>")
def delete_project(job_id):
    safe = "".join(c for c in job_id if c.isalnum())
    path = os.path.join(JOBS_DIR, safe)
    if not os.path.isdir(path):
        return {"ok": False, "error": "No existe ese proyecto."}, 404
    shutil.rmtree(path, ignore_errors=True)
    with JOBS_LOCK:
        JOBS.pop(safe, None)
    return {"ok": True}


def _load_presets() -> list[dict]:
    try:
        with open(PRESETS_FILE, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save_presets(presets: list[dict]) -> None:
    with open(PRESETS_FILE, "w", encoding="utf-8") as f:
        json.dump(presets, f, ensure_ascii=False)


def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(p) if p.isdigit() else 0 for p in v.split("."))


def _check_for_update(timeout: int = 2) -> dict:
    """Fail-open: sin UPDATE_REPO configurado, o si GitHub no responde,
    simplemente no hay novedades – nunca bloquea ni ensucia el arranque.

    Se llama DIRECTO desde index() (no vía fetch de JS – ver el comentario
    en el <template>, más abajo, para el porqué). Timeout corto porque
    esto ahora corre sincrónico en cada carga de "/", que solo pasa una
    vez al abrir la app."""
    no_update = {"ok": True, "update_available": False, "current_version": EDITO_VERSION}
    if not UPDATE_REPO or "/" not in UPDATE_REPO:
        return no_update
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "Edito-updater"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
        latest = str(data.get("tag_name", "")).lstrip("vV")
        if _version_tuple(latest) <= _version_tuple(EDITO_VERSION):
            return no_update
        assets = data.get("assets", []) or []
        download_url = next(
            (a["browser_download_url"] for a in assets
             if str(a.get("name", "")).lower().endswith(".exe")),
            data.get("html_url"))
        return {"ok": True, "update_available": True, "current_version": EDITO_VERSION,
                "latest_version": latest, "download_url": download_url,
                "notes_url": data.get("html_url")}
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return no_update


def _download_update_bg(url: str, dest: str) -> None:
    """Baja el .exe nuevo a `dest`, reportando progreso en UPDATE_DL_STATE
    (mismo patrón que _run_job/JOBS). Corre en un hilo aparte – nunca
    bloquea la UI mientras descarga ~270MB."""
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "Edito-updater"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            downloaded = 0
            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    pct = round(downloaded / total * 100) if total else 0
                    with UPDATE_DL_LOCK:
                        UPDATE_DL_STATE.update(status="downloading", percent=pct)
        # Sanity check: un .exe con ffmpeg+whisper embebido pesa cientos de
        # MB – si quedó muy chico, la descarga se cortó a mitad de camino.
        # Nunca dejamos que algo así se copie encima del .exe que funciona.
        if total and downloaded < total:
            raise OSError(f"descarga incompleta: {downloaded}/{total} bytes")
        if os.path.getsize(dest) < 10_000_000:
            raise OSError("el archivo bajado es demasiado chico, parece corrupto")
        with UPDATE_DL_LOCK:
            UPDATE_DL_STATE.update(status="ready", percent=100, path=dest)
    except (urllib.error.URLError, OSError, ValueError) as e:
        with UPDATE_DL_LOCK:
            UPDATE_DL_STATE.update(status="error", error=str(e))


@app.post("/update/download")
def update_download():
    """Arranca (o reusa, si ya está en curso) la descarga del .exe nuevo.

    A propósito NO toma la URL del client – la vuelve a resolver acá
    mismo contra GitHub, así nunca hay una URL controlada por el
    navegador llegando a un download-a-disco del lado del servidor."""
    info = _check_for_update()
    if not info.get("update_available"):
        return {"ok": False, "error": "No hay actualización disponible."}, 400
    with UPDATE_DL_LOCK:
        if UPDATE_DL_STATE.get("status") != "downloading":
            UPDATE_DL_STATE.clear()
            UPDATE_DL_STATE.update(status="downloading", percent=0)
            dest = os.path.join(DATA_DIR, "update_download", "Edito_new.exe")
            threading.Thread(target=_download_update_bg,
                              args=(info["download_url"], dest), daemon=True).start()
    return {"ok": True}


@app.get("/update/status")
def update_status():
    with UPDATE_DL_LOCK:
        return {k: v for k, v in UPDATE_DL_STATE.items() if k != "path"}


@app.get("/presets")
def list_presets():
    with PRESETS_LOCK:
        return {"ok": True, "presets": _load_presets()}


@app.post("/presets")
def save_preset():
    """Upsert por nombre. Los mismos whitelists que /render – un preset
    no es más confiable que un form cualquiera."""
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()[:40]
    if not name:
        return {"ok": False, "error": "Ponele un nombre al preset."}, 400

    font = data.get("font", "Arial")
    if font not in {v for v, _ in FONT_CHOICES}:
        font = "Arial"
    subpos = data.get("subpos", "abajo")
    if subpos not in {"arriba", "medio", "abajo"}:
        subpos = "abajo"
    transition = data.get("transition", "fade")
    if transition not in {"fade", "fadewhite", "fadegrays", "zoomin", "circleopen"}:
        transition = "fade"
    accent = str(data.get("accent", "#F4DC1A"))
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", accent):
        accent = "#F4DC1A"

    preset = {"name": name, "font": font, "accent": accent,
              "subpos": subpos, "transition": transition}
    with PRESETS_LOCK:
        presets = [p for p in _load_presets() if p.get("name") != name]
        presets.append(preset)
        presets.sort(key=lambda p: p["name"].lower())
        _save_presets(presets)
    return {"ok": True, "presets": presets}


@app.delete("/presets/<name>")
def delete_preset(name):
    with PRESETS_LOCK:
        presets = [p for p in _load_presets() if p.get("name") != name]
        _save_presets(presets)
    return {"ok": True, "presets": presets}


if __name__ == "__main__":
    activas = [k for k, v in FEATURES.items() if v] or ["ninguna (modo mínimo)"]
    print("features activas:", ", ".join(activas))

    def _serve() -> None:
        app.run(host="127.0.0.1", port=5000, debug=False,
                threaded=True, use_reloader=False)

    # Ventana nativa por defecto: pywebview usa el webview DEL SISTEMA
    # (WebView2 en Windows, WebKit en mac/Linux) – nada de Electron, cero
    # peso extra. Si no está instalado, o si se pide a propósito, cae a
    # modo navegador: fail-open, la app sigue siendo usable de las dos
    # formas (mismo principio que los efectos "aditivos" del pipeline).
    try:
        import webview
    except ImportError:
        webview = None

    if webview and os.environ.get("MINIEDITOR_BROWSER") != "1":
        # Sin esto, Windows agrupa la ventana bajo el proceso "python.exe" y
        # le muestra SU ícono en la barra de tareas, sin importar el Icon
        # que le pongamos a la ventana – hay que decirle explícitamente que
        # somos una app propia, distinta del intérprete que nos corre.
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                    "Influwa.Edito.Desktop")
            except Exception:
                pass

        class Api:
            """Puente JS→Python para cosas que el WebView2 sandboxed no
            puede hacer solo: abrir el diálogo nativo de "Guardar como"
            (save_file) y reemplazarse a sí mismo con la versión nueva ya
            descargada (apply_update). pywebview expone este objeto como
            `window.pywebview.api` en el JS de la página."""

            def save_file(self, job_id: str, filename: str, kind: str = "video") -> dict:
                safe = "".join(c for c in job_id if c.isalnum())
                src_name = "final.mp4" if kind == "video" else "cover.jpg"
                src = os.path.join(JOBS_DIR, safe, src_name)
                if not os.path.isfile(src):
                    return {"ok": False, "error": "No existe ese archivo"}
                ext = "mp4" if kind == "video" else "jpg"
                label = "Video MP4" if kind == "video" else "Imagen JPG"
                result = webview.windows[0].create_file_dialog(
                    webview.FileDialog.SAVE, save_filename=filename,
                    file_types=(f"{label} (*.{ext})",))
                if not result:
                    return {"ok": False, "cancelled": True}
                dest = result[0]
                shutil.copyfile(src, dest)
                return {"ok": True, "path": dest}

            def apply_update(self) -> dict:
                """Reemplaza este .exe por el nuevo ya descargado y lo
                reabre solo.

                Un .exe no puede sobreescribirse a sí mismo mientras corre
                (Windows lo tiene bloqueado) – por eso el swap lo hace un
                script .bat aparte, DETACHED (sobrevive aunque este
                proceso muera), que reintenta el copy en loop hasta que
                el archivo se libera (apenas cerramos la ventana) y
                recién ahí relanza el .exe. Esta función solo dispara ese
                script y cierra la ventana; no espera nada después,
                porque el proceso entero se va a terminar."""
                with UPDATE_DL_LOCK:
                    ready = UPDATE_DL_STATE.get("status") == "ready"
                    new_exe = UPDATE_DL_STATE.get("path")
                if not ready or not new_exe or not os.path.isfile(new_exe):
                    return {"ok": False, "error": "La descarga todavía no terminó."}
                if not getattr(sys, "frozen", False):
                    return {"ok": False, "error": "Solo disponible en la versión empaquetada."}

                old_exe = sys.executable
                bat_path = os.path.join(tempfile.gettempdir(), "edito_update.bat")
                bat = (
                    "@echo off\r\n"
                    ":retry\r\n"
                    f'copy /Y "{new_exe}" "{old_exe}" >NUL 2>&1\r\n'
                    "if errorlevel 1 (\r\n"
                    "  timeout /t 1 /nobreak >NUL\r\n"
                    "  goto retry\r\n"
                    ")\r\n"
                    f'start "" "{old_exe}"\r\n'
                    'del "%~f0"\r\n'
                )
                with open(bat_path, "w", encoding="utf-8") as f:
                    f.write(bat)
                subprocess.Popen(
                    ["cmd", "/c", bat_path],
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS)
                webview.windows[0].destroy()
                return {"ok": True}

        threading.Thread(target=_serve, daemon=True).start()
        webview.create_window("Edito", "http://127.0.0.1:5000",
                               width=1040, height=820, min_size=(480, 640),
                               js_api=Api())
        # Ícono de ventana/taskbar: mismo isotipo que el header, rasterizado
        # a mano (ver icongen.py) para no sumar Pillow solo por esto.
        icon_path = icongen.ensure_icon(os.path.join(DATA_DIR, "assets", "edito.ico"))
        webview.start(icon=icon_path)
    else:
        if not webview:
            print("(pywebview no instalado – abriendo en el navegador. "
                  "`pip install pywebview` para la ventana nativa)")
        print("Edito → http://localhost:5000")
        _serve()
