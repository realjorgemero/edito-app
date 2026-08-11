# Tu librería de SFX

Este directorio está vacío a propósito: los efectos de sonido no se versionan con el
código, se **curan**. El demo sintetiza los suyos (`scripts/make_demo.py`); para piezas
reales armá tu propia librería.

## Dónde conseguir (gratis y legal)

- **Freesound.org** – filtrá por licencia CC0. Buscá: `whoosh`, `riser`, `impact`, `braam`,
  `glitch`, `stinger`.
- **Kenney.nl** (packs "Audio") – CC0, calidad pareja.
- **Mixkit** – SFX gratuitos con licencia amplia (leela igual).

Guardá la licencia de cada archivo en un `LICENSES.md` acá. Futuro-vos lo agradece.

## Qué comprar/elegir – el criterio que no es obvio

Cada rol editorial pide una forma de onda distinta:

| Rol | Para qué | Forma |
|---|---|---|
| Whoosh / sweep | cortes con movimiento (zoom) | 0.6-1.8 s, transitorio claro |
| Impacto / stab | la revelación, cae sobre el corte | ataque duro, cola corta |
| Riser | expectativa antes de un corte fuerte | corto (se usa ~0.5 s), pico inequívoco |
| Glitch | ruptura, urgencia | textura digital, < 1.5 s |
| Sting apertura | romper el scroll en el frame 1 | punchy, con el golpe TEMPRANO en el archivo |
| Sting cierre | cerrar la pieza | impacto con cola resonante |

**La regla de oro**: el asset necesita **UN único pico RMS con ≥3 dB de ventaja** sobre el
segundo máximo local. Si tiene "meseta" (dos golpes casi iguales), el alineador elige uno
arbitrario y el golpe se desplaza entre renders sin que nada falle. Verificalo AL INGRESAR
el asset, no en producción.

## Ingresar un asset al catálogo

```bash
python -m minieditor.peaks mi_whoosh.wav
# → mi_whoosh.wav: pico en 0.646s → sidecar .peak escrito
```

El sidecar `.peak` es lo que permite que el render nunca haga análisis de audio: la
colocación es una resta (`start = corte - 0.040 - pico`). Ese mismo catálogo precomputado
es lo que haría portable tu pista de sonido a un stack sin DSP (Remotion, un SaaS de
video, etc.).
