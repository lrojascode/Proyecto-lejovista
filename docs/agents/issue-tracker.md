# Issue tracker: Markdown local

Las especificaciones y unidades de trabajo de este repositorio viven como archivos versionados en `docs/specs/`.

## Convenciones

- `docs/specs/README.md` mantiene el índice, orden, prioridad, dependencias y estado.
- La especificación maestra utiliza el prefijo `00-`.
- Cada unidad implementable utiliza un archivo numerado: `docs/specs/<NN>-<slug>.md`.
- Los estados permitidos son: `draft`, `ready-for-agent`, `in-progress`, `blocked` y `completed`.
- El estado se registra cerca del comienzo de cada archivo.
- No se crean GitHub Issues, comentarios, etiquetas o PRs salvo solicitud explícita.
- GitHub puede utilizarse como fuente de requisitos, pero no como destino predeterminado.

## Cuando una skill diga “publish to the issue tracker”

Crear una especificación Markdown en `docs/specs/` y actualizar `docs/specs/README.md`.

Si la skill solicita la etiqueta `ready-for-agent`, registrar:

`Estado: ready-for-agent`

## Cuando una skill diga “fetch the relevant ticket”

Leer la ruta local proporcionada o resolverla desde `docs/specs/README.md`.
