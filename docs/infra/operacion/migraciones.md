# Migraciones: expandir ahora, contraer después

El rollback de esta aplicación es volver a la imagen anterior. Todo lo de este
documento existe para que eso siga siendo cierto.

Una migración que borra una columna en el mismo despliegue que el código que
dejó de usarla no rompe nada visible: aplica limpia, el despliegue queda verde y
el producto funciona. El problema aparece más tarde, el día que hay que volver
atrás, y entonces volver ya no es cambiar una etiqueta sino restaurar un
respaldo: horas de indisponibilidad en el peor momento posible.

## La regla

**Toda migración es compatible hacia atrás por defecto.** Se añade lo nuevo, se
escribe en los dos sitios, se conmuta la lectura, y solo en un despliegue
*posterior* se retira lo antiguo.

En cuatro despliegues, y ninguno de los tres primeros impide volver al anterior:

| # | Migración | Código | Se puede volver a |
|---|---|---|---|
| 1 | Añade la columna nueva, nullable | Ignora la columna nueva | 0 |
| 2 | — | Escribe en las dos, lee la vieja | 1 |
| 3 | Rellena la nueva con lo que había | Lee la nueva, sigue escribiendo las dos | 2 |
| 4 | Borra la vieja (contracción) | Solo la nueva | 3, no antes |

El paso 4 solo se aplica cuando la imagen del paso 3 ya está desplegada y
estable. Hasta entonces la columna vieja se queda, aunque no la lea nadie: cuesta
espacio y no cuesta nada más.

## Lo que lo hace cumplirse

`backend/scripts/verificar_migraciones_compatibles.py` corre en CI y lee el
`upgrade()` de cada migración. Falla la construcción si encuentra un
`drop_column`, `drop_table`, `rename_table`, un `alter_column` a `nullable=False`
o un renombrado de columna.

Lo que hace `downgrade()` no se mira: ahí ser destructivo es lo correcto.
Tampoco cuenta borrar una restricción o un índice que se vuelven a crear en el
mismo `upgrade()`: eso es un reemplazo, y es como se cambia el `ondelete` de una
clave foránea.

Una contracción planificada se declara en el propio archivo de migración:

```python
def upgrade() -> None:
    # CONTRACCION-APROBADA: la columna quedó sin lectores en la revisión abc123,
    # desplegada el 2026-08-01 y estable desde entonces.
    op.drop_column("campaigns", "objetivo_viejo")
```

No es una forma de saltarse la regla: es la constancia de que alguien comprobó
que el código anterior ya no la necesita, con revisión y fecha. Sin esa
comprobación no hay nada que escribir ahí.

## Cuando de verdad no se puede

Algunos cambios no admiten la escalera: un tipo que reescribe la tabla entera, un
`NOT NULL` sobre una tabla enorme, una partición. Esos requieren **ventana de
mantenimiento**, y una ventana de mantenimiento es una decisión, no un despliegue
rutinario.

### Antes

```bash
TOKEN=<credencial de agencia> \
  scripts/despliegue/preflight-migracion.sh https://api.lejovista.agenciakibo.com
```

La puerta comprueba dos cosas y se niega si falta cualquiera:

1. **Respaldo externo verificado.** No que exista un respaldo: que
   `scripts/backup/verify.sh` confirme que el artefacto remoto está, es legible,
   está cifrado para nuestra clave y es más nuevo que el RPO. Un respaldo que
   nadie comprobó tiene el mismo valor que ninguno.
2. **Modo de solo lectura encendido.** Con él, lo que ve el cliente es un aviso
   y no un error, y nadie escribe sobre una tabla que se está reescribiendo.

```bash
curl -X PUT "$API/api/v1/operacion/interruptores/solo_lectura" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"encendido": true, "motivo": "ventana de mantenimiento: <qué migración>"}'
```

El modo de solo lectura es de la spec 13 y su semántica está invertida respecto a
las otras capacidades: aquí `encendido: true` significa **está bloqueando**.

### Después

1. Aplicar la migración.
2. Desplegar la imagen nueva por su identificador de commit.
3. Apagar el modo de solo lectura.
4. Correr el smoke test (`scripts/despliegue/smoke.sh`). Un despliegue sin él no
   está verificado, solo está hecho.

Si algo sale mal entre 1 y 2, el camino de vuelta ya no es la imagen anterior: es
el respaldo del paso previo. Por eso la puerta se niega a seguir sin él.

## Lo que no cubre esto

El verificador lee la migración, no lo que el código hace con ella. Una migración
puramente aditiva que va acompañada de código que asume el valor nuevo en cada
lectura también rompe la imagen anterior, y eso no lo ve ningún analizador
estático. Sigue siendo criterio de quien escribe el cambio.
