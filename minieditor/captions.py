"""PASO 6 – SUBTÍTULOS (karaoke de bloque estable, formato ASS).

El texto mostrado es SIEMPRE el guion verbatim – el ASR solo aporta
números (timestamps). Nunca muestres la transcripción del ASR: trae
alucinaciones y errores, y el operador vería palabras que no escribió.

Los tres aciertos que valen la pena copiar a cualquier stack:

1. **El bloque estable.** Todas las palabras del bloque se colocan de
   entrada; las que aún no se dijeron se ven al ~25 % de opacidad; cada
   palabra "se enciende" en su instante real. El layout nunca se
   re-centra – cero salto visual. El espectador puede MIRAR el texto sin
   tener que LEERLO.

2. **Máximo 2 líneas × 3 palabras = 6 palabras en pantalla.** El corte de
   línea llega a las 3 palabras o en puntuación blanda (, ; :), lo que
   ocurra primero.

3. **Zona segura de plataforma.** La columna de botones de TikTok/Reels
   come el borde derecho; el caption del post come el inferior. Bloque
   anclado ARRIBA-CENTRO a ~62.5 % de la altura, con 13.9 % de margen
   lateral. SIEMPRE fracciones del canvas, nunca píxeles copiados.

Énfasis – heurística determinista, NO LLM (el clasificador LLM era flaky:
elegía 0 keywords en frases cortas – nada resaltado):

   resaltar si  len(palabra) ≥ 7  ∧  no es stopword  ∧  no es negación

Las negaciones (NO, NUNCA, SIN…) van en itálica: la negación se marca por
FORMA, el contenido por COLOR. Dos canales independientes de énfasis.

⚠ Sanitizá el texto contra el marcado del renderer: en ASS, un `{` del
guion abre un bloque de override y rompe el render. (El motor de
producción NO lo hace – es una de sus deudas conocidas.)
"""

from __future__ import annotations

import os

from . import ff

MAX_WORDS_PER_LINE = 3
MAX_LINES = 2
SOFT_PUNCT = (",", ";", ":")
MIN_WORD_EVENT_MS = 80
HIGHLIGHT_MIN_LEN = 7
INACTIVE_ALPHA = "C0"      # ~25 % de opacidad (ASS: 00 opaco, FF invisible)
FONT = "Arial"             # fallback si no llega una fuente elegida por el
                           # usuario. Nombres de familia del SISTEMA (libass
                           # los resuelve vía DirectWrite en Windows): no
                           # hace falta embeberla, pero sí que esté instalada
                           # (la sustitución silenciosa de fuentes es un bug
                           # clásico si no lo está).

# Zona segura vertical (ver docstring del módulo): el bloque se ancla
# ARRIBA (Alignment=8) con MarginV medido desde ese borde. Cada preset es
# la fracción de canvas_h a la que arranca el bloque.
POSITIONS = {
    "arriba": 0.20,
    "medio": 0.45,
    "abajo": 0.625,   # default histórico: bajo el centro, sobre la zona
                       # de caption/botones de TikTok-Reels
}

STOPWORDS = {
    "para", "pero", "como", "porque", "aunque", "mientras", "cuando",
    "donde", "esta", "este", "esto", "esos", "esas", "estos", "estas",
    "ella", "ello", "ellos", "ellas", "desde", "hasta", "sobre", "entre",
    "cada", "todo", "toda", "todos", "todas", "tanto", "tanta", "unas",
    "unos", "sino", "según", "segun", "hacia", "ante", "tras", "muy",
    "más", "mas", "que", "con", "por", "los", "las", "una", "del", "sus",
    "les", "nos", "son", "fue", "han", "hay", "está", "ser", "haber",
    "tener",
}
# ⚠ La lista tiene entradas CON y SIN tilde a propósito ("más"/"mas").
# Si tu normalizador quita tildes, la mitad deja de matchear.

NEGATIONS = {"no", "nunca", "nada", "sin", "jamás", "jamas", "tampoco", "ni"}


def _sanitize(text: str) -> str:
    return text.replace("{", "(").replace("}", ")").replace("\n", " ")


def _norm(w: str) -> str:
    return "".join(c.lower() for c in w if c.isalnum())


def _is_highlight(word: str) -> bool:
    n = _norm(word)
    if len(n) < HIGHLIGHT_MIN_LEN or n in STOPWORDS or n in NEGATIONS:
        return False
    return True


def _hex_to_ass(color: str) -> str:
    """'#F4DC1A' → '&H1ADCF4&' (ASS usa BGR)."""
    c = color.lstrip("#")
    return f"&H{c[4:6]}{c[2:4]}{c[0:2]}&".upper()


def _chunk(words: list[dict]) -> list[list[list[dict]]]:
    """words → bloques; cada bloque = hasta 2 líneas de hasta 3 palabras."""
    lines, line = [], []
    for w in words:
        line.append(w)
        if len(line) >= MAX_WORDS_PER_LINE or w["w"].rstrip().endswith(SOFT_PUNCT):
            lines.append(line)
            line = []
    if line:
        lines.append(line)
    return [lines[i:i + MAX_LINES] for i in range(0, len(lines), MAX_LINES)]


def _ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int(seconds % 3600 // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def build_ass(words: list[dict], out_path: str, canvas_w: int = 1080,
              canvas_h: int = 1920, accent: str = "#F4DC1A",
              font: str = FONT, position: str = "abajo") -> str:
    """words: [{"w": str, "t0": float, "t1": float}] en tiempo ABSOLUTO
    del video final. Genera el .ass y devuelve su ruta."""
    accent_ass = _hex_to_ass(accent)
    fontsize = round(canvas_h * 0.0479)          # 4.79 % de la altura
    margin_h = round(canvas_w * 0.1389)          # 13.89 % del ancho
    margin_v = round(canvas_h * POSITIONS.get(position, POSITIONS["abajo"]))

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {canvas_w}
PlayResY: {canvas_h}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,{font},{fontsize},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,3,0,8,{margin_h},{margin_h},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    events = []
    blocks = _chunk(words)

    for bi, block in enumerate(blocks):
        flat = [w for line in block for w in line]
        block_end = (blocks[bi + 1][0][0]["t0"] if bi + 1 < len(blocks)
                     else flat[-1]["t1"])
        # El bloque vive hasta el arranque del siguiente: nunca hay un
        # frame vacío entre subtítulos.

        for gi, active_word in enumerate(flat):
            ev_s = active_word["t0"]
            ev_e = flat[gi + 1]["t0"] if gi + 1 < len(flat) else block_end
            if ev_e - ev_s < MIN_WORD_EVENT_MS / 1000:
                ev_e = ev_s + MIN_WORD_EVENT_MS / 1000

            rendered_lines = []
            k = 0
            for line in block:
                parts = []
                for w in line:
                    txt = _sanitize(w["w"])
                    if k > gi:      # todavía no dicha – tenue
                        parts.append(
                            rf"{{\1c&HFFFFFF&\alpha&H{INACTIVE_ALPHA}&}}{txt}")
                    elif _norm(w["w"]) in NEGATIONS:   # negación → forma
                        parts.append(
                            rf"{{\i1\alpha&H00&\1c&HFFFFFF&}}{txt}{{\i0}}")
                    elif _is_highlight(w["w"]):        # contenido → color
                        parts.append(
                            rf"{{\alpha&H00&\1c{accent_ass}}}{txt}")
                    else:                              # dicha, normal
                        parts.append(
                            rf"{{\alpha&H00&\1c&HFFFFFF&}}{txt}")
                    k += 1
                rendered_lines.append(" ".join(parts))

            fad = ""
            if bi == 0 and gi == 0:
                fad = r"{\fad(120,0)}"
            if bi == len(blocks) - 1 and gi == len(flat) - 1:
                fad = r"{\fad(0,120)}"

            text = fad + r"\N".join(rendered_lines)
            events.append(
                f"Dialogue: 0,{_ts(ev_s)},{_ts(ev_e)},Karaoke,,0,0,0,,{text}")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(events) + "\n")
    return out_path


def burn(video: str, ass_path: str, workdir: str) -> str:
    """Quema el .ass sobre el video.

    crf 18 (mejor que el 20 del resto del pipeline): el texto de alto
    contraste es lo primero que revela el ringing del compresor. El texto
    quemado exige más bitrate que la imagen.

    ⚠ El nombre del .ass viaja como VALOR DE FILTRO (`-vf ass=...`), y ese
    valor lo parsea el filtergraph de ffmpeg, que usa ':' como separador
    de opciones. En Windows toda ruta absoluta tiene un ':' después de la
    letra de unidad ("C:\\Users\\..."), y el escape recomendado (\\:) no
    es fiable en todas las versiones – reventaba con "Unable to parse
    'original_size'..." (el ':' partía la ruta en dos valores). En vez de
    pelear con el escape, lo evitamos: corremos ffmpeg parado en la
    carpeta del .ass (cwd) y le pasamos solo su nombre, que nunca tiene
    ':'. -i y la salida sí pueden ser absolutos: esos no pasan por el
    parser de filtros.
    """
    out = os.path.join(workdir, "final.mp4")
    ass_dir = os.path.dirname(os.path.abspath(ass_path))
    ass_name = os.path.basename(ass_path)
    ff.run(["-i", os.path.abspath(video), "-vf", f"ass={ass_name}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-pix_fmt", "yuv420p", "-c:a", "copy",
            "-movflags", "+faststart", os.path.abspath(out)],
           cwd=ass_dir)
    return out
