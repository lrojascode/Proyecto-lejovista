# Ensayo de verificación de despliegue — 2026-09-04

Ejercicio de las piezas nuevas de la spec 14 contra un stack real: PostgreSQL 16
con la cadena de migraciones aplicada desde cero, Redis, y el backend levantado
con `GIT_SHA` horneado como lo hace la imagen.

No es un despliegue a producción. Lo que se ejercitó es todo lo que un despliegue
a producción va a usar, y sobre todo **que detecta cuando algo está mal**: un
verificador que solo se prueba contra el caso bueno no verifica nada.

## Entorno

| | |
|---|---|
| Base de datos | PostgreSQL real, 52 migraciones aplicadas desde vacío |
| Redis | Instancia real |
| Backend | `uvicorn app.main:app`, con `GIT_SHA=abc1234smoke` |
| Commit desplegado | `abc1234smoke` (valor de ensayo) |

## 1 · Smoke test, caso bueno

```
  [OK] commit desplegado            abc1234smoke
  [OK] migraciones                  al dia (s13f6b2d8a4c7)
  [OK] autenticacion                sesion emitida
  [OK] lectura de historico         campaigns/recent respondio 200
  [OK] base de datos y Redis        ambas responden

DESPLIEGUE VERIFICADO        (salida: 0)
```

## 2 · Detecta que corre otro commit del que se desplegó

```
  [MAL] commit desplegado            corre abc1234smoke y se desplego otro-sha

DESPLIEGUE NO VERIFICADO (1 comprobaciones fallidas)     (salida: 1)
```

## 3 · Detecta que `migrate` no corrió

Es el fallo más caro de un despliegue y el que no se nota: la app arranca igual y
responde igual. Se provocó con `alembic downgrade -1`, dejando el esquema una
revisión por detrás del código.

```
  [OK] commit desplegado            abc1234smoke
  [MAL] migraciones                  codigo en s13f6b2d8a4c7, base en s12e5a9c3f7b4
  [OK] autenticacion                sesion emitida
  [OK] lectura de historico         campaigns/recent respondio 200
  [OK] base de datos y Redis        ambas responden

DESPLIEGUE NO VERIFICADO (1 comprobaciones fallidas)     (salida: 1)
```

Todo lo demás sigue en verde. Sin esta comprobación, el despliegue habría pasado
por bueno.

## 4 · Detecta Redis caído

`/operacion/senales` responde 200 igual con Redis caído; lo que cambia es la
señal `dependencias`, que es lo que mira el smoke test.

```
  [MAL] base de datos y Redis        la señal de dependencias no esta en verde

DESPLIEGUE NO VERIFICADO (1 comprobaciones fallidas)     (salida: 1)
```

## 5 · Reversibilidad de la última migración

Sobre PostgreSQL real, no sobre el motor embebido:

```
alembic downgrade -1   →  s13f6b2d8a4c7 -> s12e5a9c3f7b4   (limpio)
alembic upgrade head   →  s12e5a9c3f7b4 -> s13f6b2d8a4c7   (limpio)
```

Entre una y otra, `/version` reportó `migraciones_al_dia: false`, que es
exactamente lo que tiene que decir un sistema cuyo esquema no corresponde a su
código.

## 6 · Puerta previa a una migración riesgosa

En los dos sentidos.

Sin respaldo verificado y con el modo de solo lectura apagado:

```
1. Respaldo externo
   [MAL] la verificacion del respaldo fallo — NO apliques la migracion
2. Modo de solo lectura
   [MAL] el modo de solo lectura NO esta activo. Enciendelo primero: ...

NO APLIQUES LA MIGRACION: 2 condiciones sin cumplir.     (salida: 1)
```

Tras encender el modo de solo lectura por la API de la spec 13:

```
2. Modo de solo lectura
   [OK ] el modo de solo lectura esta activo
```

El respaldo sigue en rojo porque esta máquina no tiene la clave pública de
respaldo: es correcto que se niegue.

## Lo que este ensayo NO cubre

- **El rollback de imagen en producción.** Es el criterio 14 y sigue pendiente.
  Requiere dos imágenes publicadas en GHCR con su sha, repin de `IMAGE_TAG` en
  Coolify y un Redeploy. No se puede ensayar desde una máquina de desarrollo sin
  publicar imágenes.
- **La construcción de las imágenes.** El entorno de trabajo no tenía Docker
  disponible. Las construye CI en cada corrida desde esta spec, que es donde
  tiene que fallar si los Dockerfiles no cierran.
- **La medición bajo carga sintética** proporcional a 20–50 cuentas. Los límites
  de recursos son una estimación razonada, no una medición.
