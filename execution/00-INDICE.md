# Plan de ejecución — Agente DX IA

## Contexto

Tres frentes que llegaron juntos y conviene no mezclar:

1. **Producto de cara al cliente.** El producto deja de llamarse LejoVista y pasa a
   ser **Agente DX IA**; el cliente ve una app recortada (sin Resumen, sin Reporte,
   sin Diagnósticos, sin Uso de IA), en celeste, y en Campañas solo lo que está
   realmente al aire. Eso obliga a construir lo que hoy no existe: un **panel de
   administración** para dar de alta clientes y prender/apagar módulos.
2. **Datos completos.** El primer sync tiene que bajar **todo** el historial de la
   cuenta sin excepción, con estimación de tiempo visible; los siguientes solo
   traen lo reciente y mantienen el histórico acumulado.
3. **El chat, contra dos documentos de referencia.** `PROMPT-CONSULTAS-ITS.md` y
   `ORDEN-CAMPANA-ITS.md` describen cómo opera hoy un analista humano con Claude
   Code sobre la cuenta de ITS. Donde el documento y la app difieran, **manda el
   documento**.

## Los archivos

| # | Archivo | Qué cubre | Depende de |
|---|---|---|---|
| 01 | [marca-y-vista-cliente](01-marca-y-vista-cliente.md) | Rebrand a Agente DX IA, acento celeste, ocultar módulos, solo campañas al aire | — |
| 02 | [panel-admin-y-modulos](02-panel-admin-y-modulos.md) | Alta de clientes, roles de plataforma, toggles de módulos | 01 |
| 03 | [sync-historico-completo](03-sync-historico-completo.md) | Primer sync total + estimación de tiempo + incrementales | — |
| 04 | [chat-consultas](04-chat-consultas.md) | Brechas contra `PROMPT-CONSULTAS-ITS.md` | 03 |
| 05 | [chat-creacion](05-chat-creacion.md) | Brechas contra `ORDEN-CAMPANA-ITS.md` | — |
| 06 | [bugs-pendientes](06-bugs-pendientes.md) | Lo que quedó abierto del plan anterior | — |

## Orden recomendado

```
06  bugs pendientes        → son chicos y uno de ellos (CPL en cero) se ve en pantalla
01  marca y vista cliente  → visible, barato, sin backend
03  sync histórico         → habilita casi todo lo del chat
04  chat consultas         → el mayor salto de calidad percibida
02  panel admin            → cuando ya haya módulos que valga la pena apagar
05  chat creación          → el más grande; ver la advertencia de abajo
```

**06 y 01 se pueden hacer en paralelo con 03.** El 04 depende del 03 (sin
histórico completo no hay nada que consultar). El 05 es el más largo y el que más
decisiones de producto pide.

## Advertencia de alcance, antes de empezar

Los dos documentos de ITS describen un analista **de una sola cuenta, leyendo CSV
de disco**. La app es **multi-tenant sobre Postgres**. Lo que hay que portar son
los **comportamientos** —cómo mide, cómo responde, qué se niega a inventar—, no
las rutas de archivo ni las cifras de ITS. Cualquier constante de ITS que termine
hardcodeada en el producto es un bug, no una feature.

Y el 05 toca la parte más delicada: hoy la creación de campañas está clavada a
**WhatsApp, geo Lima y segmentación por edad**, y el documento pide justo lo
contrario. Eso no es un ajuste, es abrir el motor a otros tipos de campaña.

## Restricciones que siguen sin negociarse

- **Regla AI-1**: ningún número mostrado o usado para decidir sale del LLM.
- **Aislamiento por organización** en toda query.
- **Compuertas humanas** antes de cualquier escritura a Meta; todo nace `PAUSED`.
- **Suites en verde**: 725 backend / 31 frontend, cobertura ≥80% (hoy 86.81%).
