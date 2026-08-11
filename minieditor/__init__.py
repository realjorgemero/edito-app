"""mini-editor – motor didáctico de edición de video publicitario vertical.

Destilado de un motor de producción real (Python + ffmpeg). Cada módulo es
un paso de la receta y documenta el POR QUÉ, no solo el cómo.

Orden del pipeline (ver pipeline.py):

  1. trim       – recorte de aire muerto
  2. normalize  – canvas 9:16 @30fps + cadena de voz
  3. concat     – montaje con transiciones (una sola pasada)
  4. sfx        – pista de efectos de sonido alineados por pico
  5. loudnorm   – -14 LUFS / -1 dBTP en dos pasadas (último paso de audio)
  6. captions   – subtítulos karaoke de bloque estable (lo último de todo)
  7. gates      – verificación sobre el entregable final
"""
