# Dimensionado: 4 GB, 2 vCPU, 20–50 cuentas publicitarias

El VPS tiene 4 GB de memoria y 2 vCPU, y sobre ellos corren PostgreSQL, Redis, la
API, tres workers, el beat y el frontend. Además corre Coolify, que trae su
propia base de datos y su proxy.

En un servidor así, un worker sin techo de memoria no se degrada: crece hasta que
el núcleo elige a quién matar, y el núcleo elige por tamaño, no por importancia.
El proceso más grande suele ser PostgreSQL.

Hasta la spec 14, el compose de desarrollo declaraba límites y el de producción
no declaraba ninguno. Estaban justo donde no hacían falta.

## El reparto

| Servicio | Memoria | vCPU | Por qué |
|---|---|---|---|
| `db` | 640m | 1.0 | El que más se beneficia de caché, y el que peor lo pasa si le falta |
| `redis` | 320m | 0.5 | `maxmemory 256mb` más lo que ocupa el propio proceso |
| `backend` | 448m | 1.0 | Uvicorn con el modelo de datos cargado; los picos son de lectura |
| `celery-worker-sync` | 384m | 0.75 | Concurrencia 2 y las respuestas de Meta en memoria |
| `celery-worker-ai` | 256m | 0.5 | Concurrencia 1; el trabajo pesado ocurre en el proveedor |
| `celery-worker-creation` | 384m | 0.5 | Convierte imágenes y video antes de subirlos |
| `celery-beat` | 128m | 0.25 | Solo programa; no ejecuta nada |
| `frontend` | 320m | 0.75 | Next.js en modo standalone |
| **En régimen permanente** | **2880m** | | |
| `migrate` | 448m | 1.0 | No suma: corre hasta terminar y sale antes de que arranque el backend |

Quedan unos 1.1 GB para el sistema operativo y para Coolify. La suma de vCPU
excede las 2 disponibles a propósito: son techos, no reservas, y los picos de
cada servicio no coinciden.

`backend/scripts/verificar_limites_de_recursos.py` corre en CI y falla si algún
servicio se queda sin techo o si la suma se pasa del presupuesto. También es lo
que comprueba que no aparezca un entorno de preproducción en el compose de
producción.

## De dónde salen los números

De la carga observada más un margen, no de una medición bajo carga sintética
proporcional a 50 cuentas. **Esa medición está pendiente**, y hasta que exista
estos valores son una estimación razonada: holgados a propósito, como pide la
spec, para ajustarse después con datos.

Lo que sí se puede afirmar es la forma del crecimiento. Con 20–50 cuentas:

- **La sincronización es lo que crece.** Cada cuenta hace del orden de 12 a 15
  llamadas a Meta por sync, y los syncs van escalonados con jitter para no
  amontonarse. Lo que crece con el número de cuentas es la profundidad de la
  cola, no la memoria de un worker.
- **La base de datos crece por snapshots**, no por cuentas. La retención está en
  400 días y el archivado la contiene (spec 06).
- **La IA no escala con las cuentas** sino con el uso del chat, que es humano y
  tiene otro techo.

Por eso el primer límite que se toca al crecer no es la memoria sino la
profundidad de la cola, que es una de las señales de la spec 13
(`colas_profundidad`, umbral 200) y avisa antes de que se note.

## Cuando 50 se quede corto

Superar el rango es una decisión con evidencia, no una sorpresa. Las señales que
la anticipan ya existen y se consultan en `/api/v1/operacion/senales`: cola
profunda de forma sostenida, latencia del trabajo más antiguo por encima de 30
minutos, o sync más viejo de 26 horas.

Lo que **no** se hace al llegar ahí es alta disponibilidad ni arquitectura
distribuida: la spec maestra las excluye hasta que la carga las justifique. Lo
que se hace es un VPS con más memoria, que es una línea de configuración.
