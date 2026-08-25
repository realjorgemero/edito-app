# Arma Edito.exe – UN SOLO ARCHIVO, con todo adentro (ffmpeg embebido,
# faster-whisper, pywebview). Whisper el modelo NO se empaqueta, se
# descarga solo la primera vez que se usan subtítulos (~465MB aparte).
#
# --onefile: arranca un poco más lento que un build por carpeta (se
# autoextrae a una carpeta temporal en cada apertura) porque hay que
# extraer ~600-700MB cada vez – a cambio, repartir una actualización es
# "pasale este único archivo", sin carpetas ni "_internal" que puedan
# quedar desincronizados entre el .exe viejo y el nuevo.
#
# Uso:  powershell -ExecutionPolicy Bypass -File build_installer.ps1
#
# Requisitos ÚNICA VEZ (no van en requirements.txt – son de build, no de
# runtime): pip install pyinstaller, y vendor\ffmpeg\{ffmpeg,ffprobe}.exe
# (copiados de una instalación de ffmpeg cualquiera – ver PASO-A-PASO.md).
#
# Antes de subir una actualización: subí el número de EDITO_VERSION en
# app.py, y después de este build, subí dist\Edito.exe como asset de un
# Release nuevo en GitHub con tag "vX.Y.Z" que coincida – de ahí lo lee
# /update-check (ver UPDATE_REPO en app.py).

pyinstaller --name Edito --onefile --windowed --icon assets\edito.ico `
  --add-binary "vendor\ffmpeg\ffmpeg.exe;." `
  --add-binary "vendor\ffmpeg\ffprobe.exe;." `
  --add-data "assets\fonts;assets\fonts" `
  --collect-all faster_whisper `
  --collect-all ctranslate2 `
  --collect-all webview `
  --exclude-module torch `
  --exclude-module torchvision `
  --exclude-module torchaudio `
  --noconfirm `
  app.py

Write-Output ""
Write-Output "Listo -> dist\Edito.exe (un solo archivo, se puede repartir tal cual)"
