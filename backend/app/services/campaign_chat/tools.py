"""Herramientas de consulta del analista.

El chat funcionaba al revés: Python decidía de antemano qué datos meter en una
bolsa fija y el LLM redactaba sobre esa bolsa. Si la pregunta necesitaba algo
que no estaba en la bolsa, no había análisis posible — no porque no se le
permitiera, sino porque no lo tenía.

Acá el LLM elige qué consultar. Cada función de este módulo es una herramienta
que puede pedir; Python la ejecuta contra la base y le devuelve las filas
reales.

**Todas filtran por `organization_id` Y `ad_account_id`, sin excepción y sin
que el modelo pueda cambiarlo.** Ese scope no es un parámetro de la
herramienta: se inyecta acá. Un LLM que eligiera a qué organización consultar
sería el fin del aislamiento por tenant, así que la decisión no llega a
tomarla.

Y sigue valiendo la regla AI-1: los números salen de estas queries, no del
modelo. La diferencia es que ahora puede pedir los que necesita.
"""
import datetime
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import String, and_, cast, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad_account_spend_monthly import AdAccountSpendMonthly
from app.models.ad_snapshot import AdSnapshot
from app.models.adset_snapshot import AdsetSnapshot
from app.models.campaign_snapshot import CampaignSnapshot

logger = logging.getLogger("lejovista.chat_tools")

# Topes por consulta. No son de seguridad: evitan que una respuesta traiga
# 40.000 filas y se coma la ventana de contexto entera.
MAX_FILAS = 100
MAX_FILAS_SERIE = 400


def _rango(periodo_dias: Optional[int], desde: Optional[str], hasta: Optional[str]):
    """Ventana de la consulta. `desde`/`hasta` en ISO ganan sobre `periodo_dias`."""
    hoy = datetime.date.today()
    def _p(v):
        try:
            return datetime.date.fromisoformat(v) if v else None
        except (TypeError, ValueError):
            return None
    ini, fin = _p(desde), _p(hasta)
    if ini and fin:
        return ini, fin
    dias = int(periodo_dias or 30)
    dias = max(1, min(dias, 1200))
    return hoy - datetime.timedelta(days=dias), hoy


def _redondear(v, n=2):
    return round(float(v or 0), n)


class ChatTools:
    """Ejecuta las consultas del analista, siempre acotadas a una cuenta."""

    def __init__(self, db: AsyncSession, organization_id: str, ad_account_id: str):
        self.db = db
        self.organization_id = organization_id
        self.ad_account_id = ad_account_id

    def _scope(self, modelo):
        """El filtro que el modelo NO puede tocar."""
        return [
            modelo.organization_id == self.organization_id,
            modelo.ad_account_id == self.ad_account_id,
        ]

    # ------------------------------------------------------------ campañas

    async def buscar_campanas(
        self,
        periodo_dias: Optional[int] = 30,
        desde: Optional[str] = None,
        hasta: Optional[str] = None,
        solo_entregando: bool = False,
        solo_sin_resultados: bool = False,
        min_gasto: Optional[float] = None,
        ordenar_por: str = "gasto",
        limite: int = 25,
        nombre_contiene: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Campañas agregadas en un período, con filtros."""
        ini, fin = _rango(periodo_dias, desde, hasta)
        cond = self._scope(CampaignSnapshot) + [
            CampaignSnapshot.date >= ini, CampaignSnapshot.date <= fin,
        ]
        if nombre_contiene:
            cond.append(CampaignSnapshot.campaign_name.ilike(f"%{nombre_contiene}%"))

        filas = (await self.db.execute(
            select(
                CampaignSnapshot.campaign_id,
                func.max(CampaignSnapshot.campaign_name),
                func.max(CampaignSnapshot.status),
                func.max(CampaignSnapshot.effective_status),
                func.sum(CampaignSnapshot.spend),
                func.sum(CampaignSnapshot.impressions),
                func.sum(CampaignSnapshot.clicks),
                func.sum(CampaignSnapshot.conversions),
                # `results` es nulo en snapshots viejos: ahí se cae a la
                # métrica anterior en vez de contar 0 y mentir hacia abajo.
                func.sum(func.coalesce(CampaignSnapshot.results, CampaignSnapshot.conversations)),
            ).where(and_(*cond)).group_by(CampaignSnapshot.campaign_id)
        )).all()

        campanas = []
        for cid, nombre, status, efectivo, gasto, impr, clics, leads, resultados in filas:
            gasto = _redondear(gasto)
            resultados = int(resultados or 0)
            leads = int(leads or 0)
            if solo_entregando and efectivo and efectivo != "ACTIVE":
                continue
            if solo_sin_resultados and resultados > 0:
                continue
            if min_gasto is not None and gasto < float(min_gasto):
                continue
            campanas.append({
                "id": cid,
                "nombre": nombre,
                "estado": status,
                "estado_efectivo": efectivo,
                "gasto": gasto,
                "impresiones": int(impr or 0),
                "clics": int(clics or 0),
                "leads_formulario": leads,
                "resultados": resultados,
                "costo_por_resultado": _redondear(gasto / resultados) if resultados else None,
                "ctr": _redondear((clics or 0) / impr * 100) if impr else 0.0,
            })

        claves = {
            "gasto": lambda c: -c["gasto"],
            "resultados": lambda c: -c["resultados"],
            "costo": lambda c: (c["costo_por_resultado"] is None, c["costo_por_resultado"] or 0),
            "ctr": lambda c: -c["ctr"],
            "nombre": lambda c: (c["nombre"] or "").lower(),
        }
        campanas.sort(key=claves.get(ordenar_por, claves["gasto"]))
        total = len(campanas)
        campanas = campanas[: max(1, min(int(limite or 25), MAX_FILAS))]

        return {
            "periodo": {"desde": ini.isoformat(), "hasta": fin.isoformat()},
            "campanas_encontradas": total,
            "campanas_devueltas": len(campanas),
            "campanas": campanas,
        }

    async def resumen_cuenta(
        self, periodo_dias: Optional[int] = 30,
        desde: Optional[str] = None, hasta: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Totales de la cuenta en un período."""
        ini, fin = _rango(periodo_dias, desde, hasta)
        fila = (await self.db.execute(
            select(
                func.sum(CampaignSnapshot.spend),
                func.sum(CampaignSnapshot.impressions),
                func.sum(CampaignSnapshot.clicks),
                func.sum(CampaignSnapshot.reach),
                func.sum(CampaignSnapshot.conversions),
                func.sum(func.coalesce(CampaignSnapshot.results, CampaignSnapshot.conversations)),
                func.count(func.distinct(CampaignSnapshot.campaign_id)),
                func.count(func.distinct(CampaignSnapshot.date)),
            ).where(and_(*self._scope(CampaignSnapshot),
                         CampaignSnapshot.date >= ini, CampaignSnapshot.date <= fin))
        )).one()
        gasto, impr, clics, alcance, leads, resultados, n_camp, n_dias = fila
        gasto = _redondear(gasto)
        resultados = int(resultados or 0)
        leads = int(leads or 0)
        return {
            "periodo": {"desde": ini.isoformat(), "hasta": fin.isoformat()},
            "dias_con_datos": int(n_dias or 0),
            "campanas_con_actividad": int(n_camp or 0),
            "gasto": gasto,
            "impresiones": int(impr or 0),
            "clics": int(clics or 0),
            "alcance": int(alcance or 0),
            "leads_formulario": leads,
            "conversaciones_whatsapp": max(0, resultados - leads),
            "resultados": resultados,
            "costo_por_resultado": _redondear(gasto / resultados) if resultados else None,
            "ctr": _redondear((clics or 0) / impr * 100) if impr else 0.0,
            "gasto_promedio_diario": _redondear(gasto / n_dias) if n_dias else 0.0,
        }

    # ------------------------------------------------------------ evolución

    async def serie_temporal(
        self, granularidad: str = "dia", periodo_dias: Optional[int] = 90,
        desde: Optional[str] = None, hasta: Optional[str] = None,
        campana: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Evolución en el tiempo. `granularidad`: dia | mes."""
        ini, fin = _rango(periodo_dias, desde, hasta)
        cond = self._scope(CampaignSnapshot) + [
            CampaignSnapshot.date >= ini, CampaignSnapshot.date <= fin,
        ]
        if campana:
            cond.append(CampaignSnapshot.campaign_name.ilike(f"%{campana}%"))

        if granularidad == "mes":
            periodo_col = func.substr(cast(CampaignSnapshot.date, String), 1, 7)
        else:
            periodo_col = cast(CampaignSnapshot.date, String)

        filas = (await self.db.execute(
            select(
                periodo_col.label("p"),
                func.sum(CampaignSnapshot.spend),
                func.sum(CampaignSnapshot.impressions),
                func.sum(CampaignSnapshot.clicks),
                func.sum(func.coalesce(CampaignSnapshot.results, CampaignSnapshot.conversations)),
            ).where(and_(*cond)).group_by("p").order_by("p")
        )).all()

        puntos = []
        for p, gasto, impr, clics, resultados in filas[-MAX_FILAS_SERIE:]:
            gasto = _redondear(gasto)
            resultados = int(resultados or 0)
            puntos.append({
                "periodo": p,
                "gasto": gasto,
                "impresiones": int(impr or 0),
                "clics": int(clics or 0),
                "resultados": resultados,
                "costo_por_resultado": _redondear(gasto / resultados) if resultados else None,
            })
        return {"granularidad": granularidad, "puntos": len(puntos), "serie": puntos}

    async def gasto_mensual_historico(self) -> Dict[str, Any]:
        """Gasto por mes desde el inicio de la cuenta. Llega más atrás que el
        detalle diario: es lo que responde "cuántos años lleva activa"."""
        filas = (await self.db.execute(
            select(
                AdAccountSpendMonthly.year_month,
                AdAccountSpendMonthly.spend,
                AdAccountSpendMonthly.impressions,
                AdAccountSpendMonthly.reach,
                AdAccountSpendMonthly.results,
            ).where(and_(
                AdAccountSpendMonthly.organization_id == self.organization_id,
                AdAccountSpendMonthly.ad_account_id == self.ad_account_id,
            )).order_by(AdAccountSpendMonthly.year_month)
        )).all()
        meses = [
            {"mes": ym, "gasto": _redondear(g), "impresiones": int(i or 0),
             "alcance": int(a or 0), "resultados": int(r or 0)}
            for ym, g, i, a, r in filas
        ]
        return {
            "meses_con_datos": len(meses),
            "primer_mes": meses[0]["mes"] if meses else None,
            "ultimo_mes": meses[-1]["mes"] if meses else None,
            "gasto_total_historico": _redondear(sum(m["gasto"] for m in meses)),
            "meses": meses,
        }

    # ------------------------------------------------------------ desgloses

    async def desgloses(
        self, tipo: str = "edad", periodo_dias: Optional[int] = 30,
        desde: Optional[str] = None, hasta: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Rendimiento por segmento. `tipo`: edad | genero | dispositivo |
        region | placement."""
        ini, fin = _rango(periodo_dias, desde, hasta)
        filas = (await self.db.execute(
            select(
                CampaignSnapshot.placements_breakdown,
                CampaignSnapshot.demographics_breakdown,
                CampaignSnapshot.extra_breakdowns,
            ).where(and_(*self._scope(CampaignSnapshot),
                         CampaignSnapshot.date >= ini, CampaignSnapshot.date <= fin))
        )).all()

        acumulado: Dict[str, Dict[str, float]] = {}
        for placements, demo, extra in filas:
            if tipo == "placement":
                fuente = placements or {}
            elif tipo == "edad":
                fuente = demo or {}
            else:
                fuente = (extra or {}).get(
                    {"genero": "gender", "dispositivo": "device", "region": "region"}.get(tipo, tipo)
                ) or {}
            for clave, valores in (fuente or {}).items():
                if not isinstance(valores, dict):
                    continue
                b = acumulado.setdefault(clave, {"gasto": 0.0, "resultados": 0})
                b["gasto"] += float(valores.get("spend") or 0)
                b["resultados"] += int(valores.get("conversions") or valores.get("conversations") or 0)

        segmentos = [
            {"segmento": k, "gasto": _redondear(v["gasto"]), "resultados": int(v["resultados"]),
             "costo_por_resultado": _redondear(v["gasto"] / v["resultados"]) if v["resultados"] else None}
            for k, v in acumulado.items()
        ]
        segmentos.sort(key=lambda s: -s["gasto"])
        return {
            "tipo": tipo,
            "periodo": {"desde": ini.isoformat(), "hasta": fin.isoformat()},
            "segmentos": segmentos[:MAX_FILAS],
        }

    # ------------------------------------------------------------ anuncios

    async def buscar_anuncios(
        self, campana: Optional[str] = None, periodo_dias: Optional[int] = 30,
        desde: Optional[str] = None, hasta: Optional[str] = None,
        ordenar_por: str = "gasto", limite: int = 15, incluir_copy: bool = True,
    ) -> Dict[str, Any]:
        """Anuncios con su copy real, para ver qué texto funciona."""
        ini, fin = _rango(periodo_dias, desde, hasta)
        cond = self._scope(AdSnapshot) + [AdSnapshot.date >= ini, AdSnapshot.date <= fin]
        if campana:
            sub = select(CampaignSnapshot.campaign_id).where(and_(
                *self._scope(CampaignSnapshot),
                CampaignSnapshot.campaign_name.ilike(f"%{campana}%"),
            ))
            cond.append(AdSnapshot.campaign_id.in_(sub))

        filas = (await self.db.execute(
            select(
                AdSnapshot.ad_id,
                func.max(AdSnapshot.name),
                func.max(AdSnapshot.creative_title),
                func.max(AdSnapshot.creative_body),
                func.max(AdSnapshot.creative_cta_type),
                func.max(AdSnapshot.effective_status),
                func.sum(AdSnapshot.spend),
                func.sum(AdSnapshot.impressions),
                func.sum(AdSnapshot.clicks),
                func.sum(func.coalesce(AdSnapshot.results, AdSnapshot.conversations)),
            ).where(and_(*cond)).group_by(AdSnapshot.ad_id)
        )).all()

        anuncios = []
        for aid, nombre, titulo, cuerpo, cta, efectivo, gasto, impr, clics, resultados in filas:
            gasto = _redondear(gasto)
            resultados = int(resultados or 0)
            item = {
                "id": aid, "nombre": nombre, "estado_efectivo": efectivo,
                "gasto": gasto, "impresiones": int(impr or 0), "clics": int(clics or 0),
                "resultados": resultados,
                "costo_por_resultado": _redondear(gasto / resultados) if resultados else None,
                "ctr": _redondear((clics or 0) / impr * 100) if impr else 0.0,
                "cta": cta,
            }
            if incluir_copy:
                item["titulo"] = titulo
                # El copy completo puede ser larguísimo; se recorta para no
                # comerse la ventana de contexto con un solo anuncio.
                item["copy"] = (cuerpo or "")[:600]
                item["tiene_acortador"] = bool(cuerpo) and any(
                    d in cuerpo.lower() for d in ("bit.ly", "tinyurl", "cutt.ly", "acortar.link")
                )
            anuncios.append(item)

        claves = {
            "gasto": lambda a: -a["gasto"],
            "resultados": lambda a: -a["resultados"],
            "costo": lambda a: (a["costo_por_resultado"] is None, a["costo_por_resultado"] or 0),
            "ctr": lambda a: -a["ctr"],
        }
        anuncios.sort(key=claves.get(ordenar_por, claves["gasto"]))
        total = len(anuncios)
        return {
            "periodo": {"desde": ini.isoformat(), "hasta": fin.isoformat()},
            "anuncios_encontrados": total,
            "anuncios": anuncios[: max(1, min(int(limite or 15), 40))],
        }

    async def segmentacion_de_conjuntos(
        self, campana: Optional[str] = None, limite: int = 20,
    ) -> Dict[str, Any]:
        """Cómo está segmentado cada conjunto: edad, geo, plataformas."""
        cond = self._scope(AdsetSnapshot)
        if campana:
            sub = select(CampaignSnapshot.campaign_id).where(and_(
                *self._scope(CampaignSnapshot),
                CampaignSnapshot.campaign_name.ilike(f"%{campana}%"),
            ))
            cond.append(AdsetSnapshot.campaign_id.in_(sub))

        filas = (await self.db.execute(
            select(
                AdsetSnapshot.adset_id,
                func.max(AdsetSnapshot.name),
                func.max(AdsetSnapshot.effective_status),
                func.sum(AdsetSnapshot.spend),
                func.sum(func.coalesce(AdsetSnapshot.results, AdsetSnapshot.conversations)),
            ).where(and_(*cond)).group_by(AdsetSnapshot.adset_id)
            .order_by(desc(func.sum(AdsetSnapshot.spend))).limit(max(1, min(int(limite or 20), 40)))
        )).all()

        ids = [f[0] for f in filas]
        targeting = {}
        if ids:
            for aid, t in (await self.db.execute(
                select(AdsetSnapshot.adset_id, AdsetSnapshot.targeting_summary)
                .where(and_(*self._scope(AdsetSnapshot), AdsetSnapshot.adset_id.in_(ids)))
                .order_by(desc(AdsetSnapshot.date))
            )).all():
                targeting.setdefault(aid, t or {})

        conjuntos = []
        for aid, nombre, efectivo, gasto, resultados in filas:
            gasto = _redondear(gasto)
            resultados = int(resultados or 0)
            conjuntos.append({
                "id": aid, "nombre": nombre, "estado_efectivo": efectivo,
                "gasto": gasto, "resultados": resultados,
                "costo_por_resultado": _redondear(gasto / resultados) if resultados else None,
                "segmentacion": targeting.get(aid, {}),
            })
        return {"conjuntos": conjuntos}


# --------------------------------------------------------------------------
# Contrato con el LLM
# --------------------------------------------------------------------------
#
# Ojo con lo que NO está acá: ningún parámetro de organización ni de cuenta.
# El scope se inyecta en `ChatTools.__init__` y el modelo no puede pedirlo ni
# cambiarlo. Si pudiera, el aislamiento por tenant dependería de que un LLM
# eligiera bien.

def _p(tipo: str, desc: str, **extra) -> Dict[str, Any]:
    return {"type": tipo, "description": desc, **extra}


_PERIODO = {
    "periodo_dias": _p("integer", "Días hacia atrás desde hoy. Por defecto 30."),
    "desde": _p("string", "Fecha inicial YYYY-MM-DD. Gana sobre periodo_dias."),
    "hasta": _p("string", "Fecha final YYYY-MM-DD."),
}

TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "resumen_cuenta",
            "description": (
                "Totales de la cuenta en un período: gasto, resultados, costo por "
                "resultado, alcance, CTR. Empezá por acá para cualquier pregunta general."
            ),
            "parameters": {"type": "object", "properties": dict(_PERIODO)},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_campanas",
            "description": (
                "Campañas con su rendimiento en un período. Sirve para rankear, comparar, "
                "buscar una por nombre, o encontrar las que gastan sin traer resultados."
            ),
            "parameters": {"type": "object", "properties": {
                **_PERIODO,
                "solo_entregando": _p("boolean", "Solo las que Meta reporta entregando AHORA (effective_status ACTIVE)."),
                "solo_sin_resultados": _p("boolean", "Solo las que gastaron y no trajeron ningún resultado. Para '¿dónde pierdo plata?'."),
                "min_gasto": _p("number", "Descarta las que gastaron menos que esto."),
                "nombre_contiene": _p("string", "Filtra por texto en el nombre."),
                "ordenar_por": _p("string", "gasto | resultados | costo | ctr | nombre", enum=["gasto", "resultados", "costo", "ctr", "nombre"]),
                "limite": _p("integer", "Cuántas devolver (máx 100)."),
            }},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "serie_temporal",
            "description": (
                "Evolución en el tiempo por día o por mes. Para tendencias, comparar "
                "meses, ver cuándo se encareció el costo o detectar estacionalidad."
            ),
            "parameters": {"type": "object", "properties": {
                **_PERIODO,
                "granularidad": _p("string", "dia | mes", enum=["dia", "mes"]),
                "campana": _p("string", "Limitar a las campañas cuyo nombre contenga esto."),
            }},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gasto_mensual_historico",
            "description": (
                "Gasto por mes desde el inicio de la cuenta. Llega más atrás que el "
                "detalle diario: usalo para antigüedad de la cuenta, gasto por año, "
                "o comparar contra el mismo mes de años anteriores."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "desgloses",
            "description": "Rendimiento por segmento: edad, género, dispositivo, región o posición.",
            "parameters": {"type": "object", "properties": {
                **_PERIODO,
                "tipo": _p("string", "edad | genero | dispositivo | region | placement",
                           enum=["edad", "genero", "dispositivo", "region", "placement"]),
            }},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_anuncios",
            "description": (
                "Anuncios con su copy real y su rendimiento. Para saber qué texto "
                "funciona, qué formato rinde, o detectar acortadores en el copy."
            ),
            "parameters": {"type": "object", "properties": {
                **_PERIODO,
                "campana": _p("string", "Limitar a una campaña por nombre."),
                "ordenar_por": _p("string", "gasto | resultados | costo | ctr", enum=["gasto", "resultados", "costo", "ctr"]),
                "limite": _p("integer", "Cuántos devolver (máx 40)."),
                "incluir_copy": _p("boolean", "Traer el texto del anuncio. Por defecto sí."),
            }},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "segmentacion_de_conjuntos",
            "description": (
                "Cómo está segmentado cada conjunto de anuncios: edad, geografía, "
                "plataformas, Advantage+. Para revisar si la segmentación tiene sentido."
            ),
            "parameters": {"type": "object", "properties": {
                "campana": _p("string", "Limitar a una campaña por nombre."),
                "limite": _p("integer", "Cuántos devolver (máx 40)."),
            }},
        },
    },
]

TOOL_NAMES = frozenset(t["function"]["name"] for t in TOOL_SCHEMAS)


async def ejecutar(tools: ChatTools, nombre: str, argumentos: Dict[str, Any]) -> Dict[str, Any]:
    """Ejecuta una herramienta pedida por el modelo.

    Un nombre desconocido o un argumento inválido devuelven un error legible en
    vez de reventar: el modelo lo lee y reintenta con otra consulta, que es
    mejor que perder el turno entero.
    """
    if nombre not in TOOL_NAMES:
        return {"error": f"No existe la herramienta '{nombre}'. Disponibles: {sorted(TOOL_NAMES)}"}
    metodo = getattr(tools, nombre, None)
    if metodo is None:
        return {"error": f"La herramienta '{nombre}' no está implementada."}
    try:
        return await metodo(**(argumentos or {}))
    except TypeError as exc:
        return {"error": f"Argumentos inválidos para '{nombre}': {exc}"}
    except Exception:
        logger.exception("Fallo ejecutando la herramienta %s", nombre)
        return {"error": f"La consulta '{nombre}' falló. Probá con otros parámetros."}
