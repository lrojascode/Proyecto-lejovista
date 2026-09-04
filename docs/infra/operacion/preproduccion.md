# Preproducción: local y efímera, nunca en el VPS productivo

No hay un entorno de staging permanente, y no lo hay por decisión. Un staging en
el VPS productivo compite por los mismos 4 GB con lo que atiende a los clientes;
uno fuera del VPS cuesta dinero todos los meses para usarse unas pocas veces al
año.

Lo que ocupa su lugar son dos cosas distintas, según lo que haya que validar.

## Para un cambio normal: el stack local

El compose de desarrollo levanta el mismo software que producción: PostgreSQL 16,
Redis 7, la misma imagen de backend para API, workers y beat, y el frontend con
Node 22. Que las versiones coincidan no se confía, se comprueba:
`backend/scripts/verificar_paridad.py` corre en CI y falla si divergen.

```bash
docker compose up -d
```

Las diferencias que quedan, y que conviene tener presentes:

- El compose local trae `nginx`; en producción el proxy y el TLS los pone
  Coolify.
- El backend local monta el código del host y arranca con `--reload`. La imagen
  de producción no.
- Los límites de memoria del compose local son algo más holgados: no comparte el
  servidor con Coolify.

## Para una actualización mayor de proveedor: infraestructura temporal

Una versión mayor de PostgreSQL, del framework o del runtime no se valida en
local, porque lo que hay que probar es la migración de los datos reales y su
comportamiento bajo un volumen parecido. Para eso se levanta infraestructura
aparte, se valida y se destruye.

1. **Levantar** un VPS del mismo proveedor y tamaño, por horas.
2. **Restaurar** ahí el último respaldo verificado, con el procedimiento de
   restauración de la spec 04 (`scripts/backup/restore.sh`). Es la misma
   operación que un simulacro de restauración, así que sirve para las dos cosas.
3. **Aplicar** la actualización sobre esa copia y correr las migraciones.
4. **Comprobar** con el smoke test apuntando a ese host:

   ```bash
   COMMIT_ESPERADO=<sha> SMOKE_EMAIL=... SMOKE_PASSWORD=... \
     scripts/despliegue/smoke.sh https://<host temporal>
   ```

5. **Anotar** lo que se aprendió en `docs/infra/operacion/despliegues/`, con
   fecha. Lo que falla en una actualización mayor es siempre lo que no estaba en
   el guion, y el guion solo mejora si alguien escribe lo que faltaba.
6. **Destruir** el host. Un entorno temporal que sobrevive a su motivo se
   convierte en un staging permanente sin que nadie lo haya decidido, y con datos
   reales de clientes sin la vigilancia que tiene producción.

El paso 6 es el que se olvida. Si el host sigue vivo una semana después, ya no es
infraestructura temporal.

## Nada de esto corre en el VPS productivo

Es comprobable, no una promesa: `verificar_limites_de_recursos.py` falla la
construcción si aparece un servicio con pinta de preproducción en el compose de
producción, y el propio presupuesto de memoria no da para un segundo stack.
