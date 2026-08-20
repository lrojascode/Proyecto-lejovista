# 03 · Primer sync completo, con estimación de tiempo

## Contexto

Pedido: **el primer sincronizado baja toda la base de datos sin excepción, cada
dato**, mostrando en el front un estimado de cuánto va a tardar; los siguientes
syncs solo traen lo reciente y van acumulando, de modo que la base mantenga el
histórico y el presente.

Hoy no pasa eso, y se nota: el chat en producción respondió *"en los últimos 36
días (es todo lo que tengo sincronizado)"* a una pregunta de 90 días.

---

## 1 · Por qué hoy solo hay 36 días

Tres causas encadenadas, verificadas:

1. **El sync `FULL` pide `date_preset=last_30d`.** No es "completo": son 30 días.
2. **`INCREMENTAL` pide `yesterday`.** Suma un día por corrida. 30 + 6 = 36.
3. **El `BACKFILL` existe pero nunca corrió en esa cuenta.** Solo se encola
   colgado de un `FULL`, y un `FULL` solo ocurre al conectar o reconectar. Esa
   cuenta se conectó **antes** de que el backfill existiera, así que jamás lo va a
   recibir sola. No hay ninguna entrada en Beat ni endpoint HTTP que lo dispare.

Y hay un techo adicional: `SNAPSHOT_RETENTION_DAYS = 180` gobierna a la vez hasta
dónde baja el backfill, qué borra el archivador y hasta dónde puede preguntar el
chat. Son cuatro decisiones distintas atadas a un número.

---

## 2 · La política de datos

Decidida: **12 meses de detalle diario en caliente + agregados mensuales sin
límite hacia atrás.** Con eso una cuenta de 5 años responde "cuántos años lleva
activa" y "cuánto gastó en 2024" con precisión, sin guardar cada día de cada
campaña de 2021.

| Nivel | Rango | Fuente | Responde |
|---|---|---|---|
| Diario caliente | 0 → ~400 días | `campaign/adset/ad_snapshots` | Todo: ranking, desgloses, alertas, comparativas |
| Diario frío | 400 días → primer dato | tablas `_archive` | Agregados: gasto, resultados, costo |
| Mensual | todo el horizonte de Meta | `ad_account_spend_monthly` | Gasto por mes y la verdad de referencia de cobertura |

400 y no 365: 12 meses **más un mes de margen**, para que "este mes contra el
mismo mes del año pasado" se resuelva en caliente. Es un número en `config.py`.

**Hallazgo útil:** `AdAccountSpendMonthly` ya se llena con `date_preset=maximum`,
o sea que **el histórico mensual de gasto completo ya está en la base y nadie lo
consulta**. Le falta ancho: solo guarda `spend`. Ampliar esa llamada a
`spend,impressions,reach,actions` sigue siendo una fila por mes — barato aun para
5 años, y es lo que permite responder de verdad sobre años viejos.

**Separar la constante en cuatro:** alcance del backfill, corte del archivador,
techo del chat y ventana de la Capa 2 estadística son cosas distintas.

---

## 3 · Primer sync: "todo, sin excepción"

### Cómo se decide qué falta

En vez del guard actual —*"¿ya corrí alguna vez?"*, frágil porque un backfill a
medias queda marcado como hecho para siempre— se usa **reconciliación mensual**:

```
faltante_pct(mes) = (gasto que Meta reporta ese mes − gasto que tenemos) / gasto de Meta
```

El backfill trae **solo los meses que no cuadran**. Efectos: es idempotente,
re-ejecutable sin costo (sobre una cuenta completa son cero llamadas a Meta),
auto-reparador, y distingue *"ese mes no hubo campañas"* de *"me falta el dato"* —
que es lo que evita reportar huecos falsos.

### Cómo se baja

- **Chunk = un mes calendario**, del más reciente al más viejo. Si se corta, lo que
  quedó es lo más útil.
- **Por qué chunkear y no pedir 3 años de una:** `time_increment=1` sobre rangos
  largos a nivel anuncio hace que Meta rechace por volumen, y `call_api_paginated`
  tiene un tope de 50 páginas × 500 filas = **25.000 filas, tras las cuales trunca
  en silencio** (loguea un warning y devuelve datos parciales **sin** marca de
  error, así que el job se marca `COMPLETED` con la historia incompleta).
- **La estructura se pide una sola vez**, fuera del bucle: `/campaigns`, `/adsets`
  y `/ads` no están acotados por tiempo; pedirlos por chunk multiplicaría las
  llamadas por 37.
- **Commit por chunk**, con avance en `SyncJob.payload_json` (columna que ya
  existe y hoy está sin usar en jobs de sync). Un fallo en el mes 20 no puede
  perder los 19 anteriores.
- **Chunks secuenciales**, apoyados en el backoff por `x-business-use-case-usage`
  que el rate limiter ya implementa.

### Disparo automático

Al **conectar** y al **reconectar** ya se dispara un `FULL` que encola el
backfill. Falta el caso de las cuentas **ya conectadas**: agregar
`schedule_pending_history_backfills` —que ya existe, con jitter, pero es solo
CLI— al `beat_schedule`. Con la reconciliación mensual esa tarea es barata, así
que puede correr periódicamente sin costo.

---

## 4 · La estimación de tiempo en el front

Es la parte que se ve, y hoy no hay nada parecido.

### De dónde sale el número

**No inventarlo.** Después del `FULL` inicial ya se conoce `AdAccountSpendMonthly`,
o sea cuántos meses tuvieron gasto. Y del `FULL` se sabe cuántas campañas,
conjuntos y anuncios tiene la cuenta. Con eso:

```
chunks          = meses con gasto
requests        ≈ chunks × 3 niveles × páginas por nivel
tiempo estimado ≈ requests × segundos por request medidos
```

Los segundos por request no son una constante inventada: `APICallLog` ya registra
cada llamada con su timestamp. Se puede derivar de la propia cuenta.

Órdenes de magnitud de referencia: cuenta mediana con 24 meses de gasto ≈ 100-170
requests ≈ **3 minutos**. Cuenta grande (150 campañas) ≈ **15-25 minutos**.

### Cómo se muestra

- Al conectar la cuenta: *"Estamos trayendo todo el historial de tu cuenta. Son
  unos N meses de datos, alrededor de X minutos. Podés seguir usando la app; te
  avisamos cuando termine."*
- **Progreso real, no una barra falsa**: `SyncJob.payload_json` lleva
  `meses_hechos / meses_totales`. Reusar el mecanismo de eventos WebSocket
  `sync_progress` que ya existe, y el **polling de respaldo** que ya se implementó
  para el dashboard.
- **Solo la primera vez.** Los syncs incrementales no muestran estimación: duran
  segundos.
- Si la estimación se pasa, decirlo en vez de quedarse en 99%.

### Honestidad mientras tanto

Mientras el backfill corre, el chat y el dashboard responden sobre datos
parciales. Eso hay que decirlo — es exactamente el bloque `cobertura` del
[04](04-chat-consultas.md): *"todavía estoy trayendo tu historial, por ahora tengo
desde X"*.

---

## 5 · Prerrequisito: el archivador puede duplicar filas

Antes de que nada lea las tablas `_archive`, hay que arreglar esto:
`snapshot_archiver` hace los `db.add(...)` al archivo dentro de un `try`, el
`except` solo loguea, y el `commit` está **fuera del bucle**. Si el `delete` de una
tabla falla, la fila queda **en caliente y en archivo**.

Hoy es inocuo porque nadie lee el archivo. En cuanto el chat lo lea, es **doble
conteo de plata**. Va antes, no después.

De paso: el archivador no batchea — hidrata todas las filas de todos los tenants
anteriores al corte en una sola transacción. Tras un backfill de 3 años, el
domingo siguiente intenta mover meses enteros de golpe.

---

## Verificación

- Conectar una cuenta nueva en mock: se encola el backfill y el front muestra
  meses totales y estimación.
- `SyncJob.payload_json` refleja el avance; cortar el WebSocket no congela la UI
  (el polling de respaldo la cierra igual).
- Segunda corrida del backfill sobre una cuenta completa: **cero llamadas** a
  `/insights`.
- Borrar los snapshots de un mes y re-correr: pide **ese** mes y solo ese.
- La estructura (`/campaigns`, `/adsets`, `/ads`) se pide **una vez**, no una por
  chunk.
- Un sync incremental posterior no borra ni pisa el histórico: lo extiende.
