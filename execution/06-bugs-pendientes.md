# 06 · Bugs abiertos del plan anterior

## Contexto

Del plan anterior (`~/.claude/plans/quiero-que-se-cree-toasty-cat.md`) quedaron
cosas sin cerrar. Son chicas y una se ve en pantalla, así que conviene sacarlas
antes de empezar con lo grande.

---

## 1 · "S/. 0,00 por contacto" — un typo de clave

`backend/app/services/campaign_chat/facts.py:282`:

```python
"cost_per_conv": round(float(c.get("cpl", 0.0)), 2),
```

`c` viene de `ReportBuilder._agg_campaigns`, que emite **`cost_per_conv`** y
**nunca** tuvo una clave `cpl` (`grep -c '"cpl"' report_builder.py` → 0). Cae al
default `0.0` para toda campaña, desde que `facts.py` existe.

Lo grave no es el texto: ese cero **entra a los datos que ve el LLM y al conjunto
de grounding**. El modelo cree que todas las campañas cuestan cero y puede
repetirlo como dato verificado.

**Arreglo:** `c.get("cost_per_conv", 0.0)`.

**Y el fixture que lo tapaba** — `tests/test_campaign_chat_facts.py:59` inventa la
clave `cpl`, que el `ReportBuilder` real jamás produce. Test verde, producto roto.

**Auditar todos los fixtures del chat:** comparar las claves que inventan contra
las que emiten de verdad `_agg_campaigns`, `get_kpis_with_deltas` y `_agg_adsets`.
Es la tercera vez en este proyecto que un fixture esconde un bug — vale buscar el
patrón, no solo este caso.

**Bug vecino:** el ranking (`facts.py:276`) no filtra `conversations > 0`. Como
`_agg_campaigns` le asigna `cost = spend` a las campañas sin conversiones, una
campaña con `spend=0, conv=0` da `cost_per_conv=0` y **queda primera** — "tu mejor
campaña" podría ser una campaña muerta. El resto del producto sí filtra.

---

## 2 · El formato del alcance rompe el grounding

`facts.py` formatea alcance e impresiones como `789.412`. La guarda parsea eso
como *789 coma 412* (decimal inglés), no como 789412 → el número no aparece en los
facts → **se descarta la respuesta entera del LLM**.

Por eso las respuestas del chat se leen como plantilla: cada vez que el analista
menciona el alcance, su redacción se cae y queda el texto canónico.

**Arreglo:** que `_parse_float_candidates` genere también la lectura de miles
cuando el grupo tras el punto tiene exactamente 3 dígitos (`789.412` →
`[789.412, 789412]`). Con test en las dos direcciones: `789.412` groundea contra
789412, y la colisión decimal/entero sigue rechazándose.

**Trampa:** esas líneas aplican `.replace(",", ".")` a **toda la cadena**. Meter un
`_money()` adentro convierte `S/. 7.417,19` en `S/. 7.417.19`.

---

## 3 · El archivador puede duplicar filas

`snapshot_archiver.py`: los `db.add(...)` al archivo ocurren dentro del `try`, el
`except` solo loguea, y el `commit` está **fuera del bucle**. Si el `delete` de una
tabla falla, la fila queda **en caliente y en archivo**.

Hoy es inocuo porque nadie lee `_archive`. En cuanto se lea —[03](03-sync-historico-completo.md)—
es **doble conteo de plata**. Prerrequisito, no extra.

De paso: no batchea. Hidrata todas las filas de todos los tenants anteriores al
corte en una transacción.

---

## 4 · Crash latente por `topic: null`

`llm.py` lee `data.get("topic")` del planificador **sin validar** contra ningún
catálogo (a diferencia de `blocks` y `period_key`, que sí se validan), y
`facts.py:147` hace `list(topic)` → `TypeError` si es `None`. El `except` del motor
se lo traga y el usuario recibe *"Todavía no tengo datos sincronizados"* — un
mensaje **falso** que manda a buscar el problema al lugar equivocado.

**Arreglo:** validar `topic` contra el catálogo, igual que los otros dos campos.

---

## 5 · Coherencia del formato de moneda

`_money` en `facts.py` produce `S/. 7.417,19`, y su docstring dice que sigue *"la
convención del resto del producto"*. No es cierto: el resto —PDF, reporte,
dashboard, detalle de campaña— usa `S/.` + `.toFixed(2)` **sin separador de
miles**.

La convención nueva es defendible para es-PE, pero hoy conviven las dos: el chat
dice `S/. 7.417,19` y el PDF del mismo dato dice `S/.7417.19`. Hay que elegir una
y alinear, o el cliente ve dos formatos del mismo número el mismo día.

---

## Orden

```
1. typo del CPL + auditoría de fixtures   → una línea, y es lo que se ve
2. grounding de miles                     → desbloquea las respuestas del analista
4. validar topic                          → cierra un crash con mensaje engañoso
5. formato de moneda                      → decisión, después alineación
3. archivador                             → antes de que el 03 lea el archivo
```

## Verificación

- "¿cuál es tu mejor campaña?" → costo real, nunca `S/. 0,00`, y nunca una campaña
  sin conversiones.
- Una respuesta que menciona el alcance → se muestra el análisis del LLM, no cae al
  canónico.
- Los fixtures del chat solo usan claves que los agregadores reales emiten.
- Suites en verde: 725 backend / 31 frontend, cobertura ≥80%.
