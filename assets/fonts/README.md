# Fuentes propias

Poné acá los archivos `.ttf`/`.otf` que quieras ofrecer en el selector de
subtítulos además de las que ya trae Windows. Esta carpeta viaja embebida
dentro del `.exe` (ver `build_installer.ps1`, `--add-data`), así que
funciona en cualquier PC sin importar si esa fuente está instalada o no.

Pasos para agregar una fuente nueva:

1. Copiá el archivo `.ttf`/`.otf` acá.
2. Agregá su nombre de familia a `FONT_CHOICES` en `app.py`.
3. Reconstruí el `.exe` (`build_installer.ps1`).

`minieditor/captions.py` (`_ensure_fontconfig`) ya apunta fontconfig a
esta carpeta además de `C:\Windows\Fonts` – no hace falta tocar nada más.
