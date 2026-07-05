# Posibles mejoras

Notas de cosas que se evaluaron y se decidió NO hacer todavía (o que quedaron
como idea suelta), para no perder el contexto de la discusión.

---

## Reporte: hallazgos/recomendaciones vía LLM (2026-07-05)

Hoy `_findings`, `_recommendations`, `_alerts` (`backend/app/services/report_builder.py`)
son reglas fijas en Python, no IA — ver discusión completa en el historial de
sesión del 2026-07-05. Decisión: **dejarlo como está por ahora**.

Si se retoma, la opción intermedia recomendada es: mantener los cálculos en
Python (números garantizados, sin riesgo de alucinación) y que el LLM solo
redacte la frase final a partir de esos datos ya verificados — conserva AI-1
(nunca inventar cifras) pero gana variedad de redacción. Tamaño: mediano
(nuevo prompt + parseo + fallback a las reglas actuales si falla la llamada).

---

## Otras ideas que surgieron trabajando en el proyecto

- **Exportar el reporte a Markdown además de PDF**: ya se evaluó como opción
  en la sesión de exportación (ver HANDOFF/historial); se eligió PDF vía
  impresión del navegador primero. Markdown queda como añadido barato si se
  quiere un formato editable/versionable además del PDF (endpoint que arma
  `.md` desde los datos que ya calcula `ReportBuilder`, sin dependencias
  nuevas).
- **PDF generado en el backend (WeasyPrint)** como alternativa a "imprimir
  desde el navegador", si en algún momento se necesita generar el PDF sin
  intervención del usuario (ej. enviarlo por email automáticamente, o
  adjuntarlo a un reporte programado). Ver la comparación de opciones en el
  historial de sesión — la imagen base de `backend/Dockerfile` (Debian slim)
  lo hace viable sin mucho dolor.
- **Reporte programado/recurrente**: con Celery Beat ya armado (I-1/I-2), no
  sería difícil generar el PDF/reporte automáticamente cada semana/mes y
  mandarlo por email al cliente — sería la extensión natural de "exportar a
  PDF" hacia algo proactivo en vez de bajo demanda.
- **Comparar periodos** en `/reporte` (ej. "estos 30 días vs. los 30
  anteriores") — hoy el reporte es siempre una ventana fija de 30 días sin
  punto de comparación, lo que dificulta ver si se está mejorando o
  empeorando.
- **Alertas proactivas por WebSocket/email cuando una campaña cruza un
  umbral crítico** (hoy las alertas solo se ven si el usuario entra al
  dashboard o corre el diagnóstico manualmente).
