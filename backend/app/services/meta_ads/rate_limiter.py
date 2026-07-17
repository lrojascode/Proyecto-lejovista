import asyncio
import logging
from typing import Dict, Any, Optional
import httpx
from app.config import settings
from app.database import async_session
from app.models.api_call_log import APICallLog

logger = logging.getLogger("lejovista.rate_limiter")

class MetaAPIRateLimiter:
    def __init__(self, ad_account_id: Optional[str] = None):
        self.ad_account_id = ad_account_id
        # Contador de llamadas en la sesión actual
        self.call_count = 0
        
        # User-Agent honesto que identifica a la app (cumplimiento de Meta Platform
        # Terms). NO se falsifica un navegador: la Marketing API es para consumo
        # programático y el ritmo se controla con backoff por headers de Meta.
        self.headers = {
            'User-Agent': 'LejoVista/1.0 (+https://lejovista.com)',
            'Accept': 'application/json',
        }

    async def _log_api_call(self, method: str, endpoint: str, params: Dict[str, Any], status_code: int, response_preview: str):
        # Guardar en base de datos de auditoría
        async with async_session() as session:
            try:
                log_entry = APICallLog(
                    ad_account_id=self.ad_account_id,
                    method=method,
                    endpoint=endpoint,
                    params={k: (v if k != 'access_token' else '***') for k, v in params.items()},
                    status_code=status_code,
                    response_preview=response_preview[:1000] # Limitar tamaño
                )
                session.add(log_entry)
                await session.commit()
            except Exception as e:
                logger.error(f"Error logging API call to DB: {e}")

    async def _apply_delay(self, es_post: bool):
        """Pausa de cortesía mínima entre llamadas.

        El control de tasa real lo hace el backoff basado en la cabecera
        `x-business-use-case-usage` de Meta (ver call_api). Aquí solo espaciamos
        levemente para no ráfagas; NO se simula comportamiento humano.
        """
        self.call_count += 1

        if settings.MOCK_SERVICES:
            await asyncio.sleep(0.05)
            return

        # Escrituras un poco más espaciadas que lecturas; valores fijos y modestos.
        await asyncio.sleep(0.5 if es_post else 0.2)

    async def call_api(
        self, 
        endpoint: str, 
        params: Dict[str, Any], 
        method: str = "GET", 
        access_token: str = ""
    ) -> Dict[str, Any]:
        """
        Realiza una llamada a la API de Meta Ads Graph de forma segura y respetuosa con los límites.
        Soporta MOCK_SERVICES para simular la API localmente.
        """
        es_post = method.upper() == "POST"
        es_delete = method.upper() == "DELETE"
        # DELETE es una escritura tanto como POST: debe respetar el mismo gating
        # y las mismas pausas. Antes solo se distinguía POST, así que un DELETE
        # habría caído en la rama del GET (sin borrar nada) y sin gatear.
        es_escritura = es_post or es_delete

        # Lecturas: reales si MOCK_SERVICES=false.
        # Escrituras (POST/DELETE): reales solo si el write-back está explícitamente
        # habilitado Y (sin allowlist, o) esta cuenta está en la piloto (F-2).
        simulate = settings.simulate_meta_writes_for(self.ad_account_id) if es_escritura else settings.MOCK_SERVICES

        # Aplicar el delay antes de la llamada
        await self._apply_delay(es_escritura)

        if simulate:
            # Retornamos respuestas mockeadas (serán manejadas por los servicios específicos)
            logger.info(f"[MOCK] API CALL - {method} - {endpoint} - Params: {list(params.keys())}")
            # Registramos la llamada ficticia en base de datos
            await self._log_api_call(method, endpoint, params, 200, "MOCK RESPONSE OK")
            return {"mock": True}

        # ---- PRODUCCIÓN: Llamada real ----
        url = f"https://graph.facebook.com/{settings.META_API_VERSION}/{endpoint}"
        
        # Asegurar token en params
        all_params = {**params}
        if access_token:
            all_params["access_token"] = access_token

        # Reintentos seguros
        intentos = 0
        max_intentos = 2
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            while intentos < max_intentos:
                try:
                    if es_post:
                        response = await client.post(url, data=all_params, headers=self.headers)
                    elif es_delete:
                        response = await client.delete(url, params=all_params, headers=self.headers)
                    else:
                        response = await client.get(url, params=all_params, headers=self.headers)
                    
                    status = response.status_code
                    data = response.json()
                    
                    # Registrar log
                    await self._log_api_call(method, endpoint, all_params, status, response.text)
                    
                    # Procesar cabeceras de rate limit de Meta (x-business-use-case-usage)
                    x_usage = response.headers.get("x-business-use-case-usage")
                    if x_usage:
                        try:
                            import json
                            usage_data = json.loads(x_usage)
                            for biz_id, usage_list in usage_data.items():
                                for usage in usage_list:
                                    call_count = usage.get("call_count", 0)
                                    total_time = usage.get("total_time", 0)
                                    total_cpu = usage.get("total_cputime", 0)
                                    regain_access = usage.get("estimated_time_to_regain_access", 0)
                                    
                                    max_usage = max(call_count, total_time, total_cpu)
                                    if regain_access > 0:
                                        wait_time = regain_access * 60 + 5
                                        logger.warning(f"Meta Rate Limit alcanzado ({max_usage}%). Esperando {wait_time}s...")
                                        await asyncio.sleep(wait_time)
                                    elif max_usage > 85:
                                        logger.warning(f"Meta Rate Limit alto ({max_usage}%). Pausa preventiva de 15s...")
                                        await asyncio.sleep(15)
                                    elif max_usage > 50:
                                        logger.info(f"Meta Rate Limit moderado ({max_usage}%). Pausa preventiva de 3s...")
                                        await asyncio.sleep(3)
                        except Exception as e:
                            logger.error(f"Error al analizar x-business-use-case-usage: {e}")
                    
                    # Si es error de rate limit u otros errores temporales, esperar
                    if status in [408, 429, 500, 502, 503, 504]:
                        intentos += 1
                        if intentos < max_intentos:
                            logger.warning(f"Error {status} detectado. Esperando 60 segundos antes de reintentar...")
                            await asyncio.sleep(60)
                            continue
                    
                    return data
                    
                except httpx.RequestError as exc:
                    logger.error(f"Error de red al llamar a {url}: {exc}")
                    intentos += 1
                    if intentos < max_intentos:
                        await asyncio.sleep(60)
                except Exception as exc:
                    logger.error(f"Excepción inesperada en llamada API: {exc}")
                    await self._log_api_call(method, endpoint, all_params, 500, str(exc))
                    return {"error": "excepcion", "detalle": str(exc)}

            return {"error": "failed_after_retries", "detalle": "Llamada fallida tras reintentos"}

    async def call_api_multipart(
        self,
        endpoint: str,
        data: Dict[str, Any],
        file_bytes: bytes,
        filename: str,
        access_token: str = "",
    ) -> Dict[str, Any]:
        """POST multipart/form-data (subida de imágenes a `/adimages`).

        Es una escritura: gateada por `SIMULATE_META_WRITES` igual que `call_api`
        con `method="POST"`, con el mismo logging/delay, pero usando `files=`
        en vez de `data=` porque Meta espera el archivo como parte multipart
        (un JSON o form-urlencoded normal no sirve para subir binarios).
        """
        await self._apply_delay(True)

        if settings.simulate_meta_writes_for(self.ad_account_id):
            logger.info(f"[MOCK] API CALL (multipart) - POST - {endpoint} - file={filename}")
            await self._log_api_call("POST", endpoint, data, 200, "MOCK RESPONSE OK")
            return {"mock": True, "images": {filename: {"hash": f"mock_hash_{filename}"}}}

        url = f"https://graph.facebook.com/{settings.META_API_VERSION}/{endpoint}"
        all_data = {**data}
        if access_token:
            all_data["access_token"] = access_token

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                files = {filename: (filename, file_bytes, "application/octet-stream")}
                response = await client.post(url, data=all_data, files=files, headers=self.headers)
                status_code = response.status_code
                resp_data = response.json()

                await self._log_api_call("POST", endpoint, all_data, status_code, response.text)
                return resp_data
            except httpx.RequestError as exc:
                logger.error(f"Error de red al subir imagen a {url}: {exc}")
                return {"error": "network", "detalle": str(exc)}
            except Exception as exc:
                logger.error(f"Excepción inesperada subiendo imagen: {exc}")
                await self._log_api_call("POST", endpoint, all_data, 500, str(exc))
                return {"error": "excepcion", "detalle": str(exc)}

    async def call_api_paginated(
        self,
        endpoint: str,
        params: Dict[str, Any],
        method: str = "GET",
        access_token: str = "",
        max_pages: int = 50,
    ) -> Dict[str, Any]:
        """Como `call_api` pero sigue la paginación por cursor de Meta.

        Meta pagina los endpoints de listas (insights, campaigns, adsets, ads,
        adimages, customaudiences) y devuelve por defecto ~25 filas con un
        `paging.cursors.after` para pedir la siguiente página. `call_api` hacía
        UNA sola request y se perdía el resto (bug crítico: solo se guardaban las
        25 filas más viejas). Aquí concatenamos `data` de todas las páginas,
        pasando cada página por `call_api` (respeta rate limiter, backoff y log).

        Tope de seguridad `max_pages` para no quedar en un bucle si Meta devuelve
        un cursor mal formado. Si una página falla:
          - sin datos acumulados: se devuelve el error tal cual (el caller decide).
          - con datos acumulados: se corta y se devuelve lo obtenido (parcial),
            sin la clave `error`, para no descartar lo ya sincronizado.
        """
        all_data: list = []
        page_params = {**params}

        for _ in range(max_pages):
            res = await self.call_api(endpoint, page_params, method=method, access_token=access_token)

            if not isinstance(res, dict) or "error" in res or "ERROR" in res:
                if all_data:
                    logger.warning(
                        "Paginación de %s interrumpida por error tras %d filas: %s",
                        endpoint, len(all_data), res,
                    )
                    return {"data": all_data}
                return res

            all_data.extend(res.get("data", []))

            paging = res.get("paging") or {}
            after = (paging.get("cursors") or {}).get("after")
            if not paging.get("next") or not after:
                break
            page_params["after"] = after
        else:
            logger.warning("Paginación de %s alcanzó el tope de %d páginas.", endpoint, max_pages)

        return {"data": all_data}
