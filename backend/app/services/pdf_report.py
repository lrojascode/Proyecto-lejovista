"""5.3 (F-3.2) — PDF real del reporte de rendimiento.

Render server-side con WeasyPrint (HTML→PDF, sin navegador headless) a
partir de los mismos datos que ya arma `ReportBuilder` — nunca se recalculan
ni se inventan cifras aquí (regla AI-1), solo se formatean para impresión.
"""
import datetime
import re
from typing import Any, Dict, Optional

from jinja2 import Template
from weasyprint import HTML

_TEMPLATE = Template("""
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<style>
  @page { size: A4; margin: 1.6cm; }
  body { font-family: 'DejaVu Sans', sans-serif; font-size: 10px; color: #1a1a1a; }
  h1 { font-size: 20px; margin-bottom: 2px; }
  h2 { font-size: 14px; margin-top: 20px; margin-bottom: 8px; border-bottom: 1px solid #ddd; padding-bottom: 4px; }
  .subtitle { color: #555; font-size: 11px; margin-bottom: 16px; }
  table { width: 100%; border-collapse: collapse; margin-bottom: 12px; }
  th, td { text-align: left; padding: 4px 6px; border-bottom: 1px solid #eee; font-size: 9px; }
  th { color: #555; font-weight: 600; }
  .kpis { display: flex; gap: 12px; margin-bottom: 12px; }
  .kpi { border: 1px solid #ddd; border-radius: 6px; padding: 8px 12px; flex: 1; }
  .kpi .label { color: #666; font-size: 9px; }
  .kpi .value { font-size: 15px; font-weight: 600; }
  .label-GANADORA, .label-BUENA { color: #1a7a3c; }
  .label-CARA, .label-PAUSAR { color: #b3261e; }
  .label-REGULAR { color: #8a6d00; }
  ul { margin: 4px 0; padding-left: 16px; }
  li { margin-bottom: 3px; }
</style>
</head>
<body>
  <h1>Reporte de Rendimiento — {{ ad_account_name }}</h1>
  <p class="subtitle">{{ organization_name }} · {{ external_account_id }} · últimos {{ report.window_days }} días · generado {{ generated_at }}</p>

  <h2>Resumen Ejecutivo</h2>
  <div class="kpis">
    <div class="kpi"><div class="label">Total invertido</div><div class="value">S/.{{ "%.2f"|format(report.kpis.total_spend) }}</div></div>
    <div class="kpi"><div class="label">Conversaciones</div><div class="value">{{ report.kpis.total_conversations }}</div></div>
    <div class="kpi"><div class="label">Mejor costo/conv.</div><div class="value">S/.{{ "%.2f"|format(report.kpis.best_cost_per_conv) }}</div></div>
    <div class="kpi"><div class="label">CTR promedio</div><div class="value">{{ "%.2f"|format(report.kpis.avg_ctr) }}%</div></div>
  </div>

  {% if findings %}
  <h2>Lo más importante</h2>
  <ul>{% for f in findings %}<li>{{ f }}</li>{% endfor %}</ul>
  {% endif %}

  {% if recommendations %}
  <h2>Recomendaciones</h2>
  <ul>{% for r in recommendations %}<li>{{ r }}</li>{% endfor %}</ul>
  {% endif %}

  <h2>Rendimiento por Campaña</h2>
  <table>
    <thead><tr><th>Campaña</th><th>Gasto</th><th>Conv.</th><th>Costo/Conv.</th><th>CTR</th><th>Estado</th></tr></thead>
    <tbody>
    {% for c in report.campaigns %}
      <tr>
        <td>{{ c.name }}{% if c.created_by_ai %} <span style="color:#6a4bd6;font-size:8px;">(creada por IA)</span>{% endif %}</td>
        <td>S/.{{ "%.2f"|format(c.spend) }}</td>
        <td>{{ c.conversations }}</td>
        <td>S/.{{ "%.2f"|format(c.cost_per_conv) }}</td>
        <td>{{ c.ctr }}%</td>
        <td class="label-{{ c.label }}">{{ c.label }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>

  <h2>Parámetros Óptimos para Nuevas Campañas</h2>
  <table>
    <thead><tr><th>Parámetro</th><th>Valor recomendado</th><th>Justificación</th></tr></thead>
    <tbody>
    {% for p in report.optimal_params %}
      <tr><td>{{ p.param }}</td><td>{{ p.value }}</td><td>{{ p.justification }}</td></tr>
    {% endfor %}
    </tbody>
  </table>

  {% if narrative %}
  <h2>Diagnóstico de la IA</h2>
  <div>{{ narrative_html|safe }}</div>
  {% endif %}
</body>
</html>
""")


def _inline_markdown(text: str) -> str:
    """Negrita (**texto**) — lo único que el prompt del Master Brief v2 usa
    dentro del texto de una campaña. No se soporta cursiva con guion bajo
    inline: los `campaign_name` reales de Meta suelen tener guiones bajos
    (ej. "MTV_MSJ_CABLE_GPT_..."), y un regex `_texto_` los interpreta como
    marcado y destruye el nombre."""
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)


def _narrative_to_html(narrative: str) -> str:
    """Conversión mínima de Markdown a HTML para el narrative del brief —
    encabezados (##), listas (*), negrita inline y una línea completa entre
    guiones bajos como marcador de cursiva (`_Ninguna campaña..._`, el único
    uso real que hace la plantilla mock de la sección vacía). Es todo lo que
    usa el prompt v2 (5 secciones fijas de Master Brief v2) — no se trae un
    parser de Markdown completo para esto."""
    lines_html = []
    in_list = False
    for line in narrative.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            if in_list:
                lines_html.append("</ul>")
                in_list = False
            lines_html.append(f"<h3>{_inline_markdown(stripped[3:])}</h3>")
        elif stripped.startswith("* ") or stripped.startswith("- "):
            if not in_list:
                lines_html.append("<ul>")
                in_list = True
            lines_html.append(f"<li>{_inline_markdown(stripped[2:])}</li>")
        elif stripped.startswith("_") and stripped.endswith("_") and len(stripped) > 1:
            if in_list:
                lines_html.append("</ul>")
                in_list = False
            lines_html.append(f"<p><em>{stripped[1:-1]}</em></p>")
        elif stripped:
            if in_list:
                lines_html.append("</ul>")
                in_list = False
            lines_html.append(f"<p>{_inline_markdown(stripped)}</p>")
    if in_list:
        lines_html.append("</ul>")
    return "\n".join(lines_html)


def render_report_pdf(
    organization_name: str,
    ad_account_name: str,
    external_account_id: str,
    report: Dict[str, Any],
    narrative: Optional[str] = None,
) -> bytes:
    raw_generated_at = report.get("generated_at", "")
    try:
        generated_at = datetime.datetime.fromisoformat(raw_generated_at).strftime("%d/%m/%Y %H:%M")
    except (ValueError, TypeError):
        generated_at = raw_generated_at

    html_str = _TEMPLATE.render(
        organization_name=organization_name,
        ad_account_name=ad_account_name,
        external_account_id=external_account_id,
        report=report,
        generated_at=generated_at,
        findings=report.get("findings") or [],
        recommendations=report.get("recommendations") or [],
        narrative=narrative,
        narrative_html=_narrative_to_html(narrative) if narrative else "",
    )
    return HTML(string=html_str).write_pdf()
