# 04 · Backups cifrados fuera del VPS y restauración verificada

## Estado

Completada. Implementación en `scripts/backup/` (respaldo, verificación,
restauración y drill) más `backend/scripts/verificar_restauracion.py` para la
comprobación funcional. Los modos de fallo que la verificación debe detectar se
prueban en `backend/tests/test_spec04_respaldo.py`; el ejercicio completo dejó
[evidencia fechada](../infra/backups/drills/2026-09-01-drill.md).

**El primer drill encontró tres fallos en el propio sistema de respaldo**, que es
exactamente para lo que existe:

1. La verificación extraía el keyid con un carácter de más y nunca encontraba la
   clave.
2. La comprobación funcional no soportaba un socket unix de PostgreSQL.
3. **El más serio**: la verificación solo miraba la cabecera OpenPGP, y un
   archivo truncado por el cuerpo la conserva intacta — habría pasado la
   comprobación diaria durante meses y fallado el día del incidente. Se corrigió
   publicando la suma `sha256` junto al artefacto.

**Pendiente para producción.** El drill se ejecutó con el driver `local` contra
PostgreSQL real. Falta repetirlo contra el destino remoto definitivo (`rclone`) y
con la clave privada realmente custodiada fuera del VPS.

## Prioridad

P0.

## Objetivo

Que la pérdida total del VPS productivo no signifique la pérdida de los datos de los clientes,
y que la capacidad de restaurar esté demostrada por un ejercicio fechado y repetible, no
supuesta.

## Problema

Existe un script de respaldo de PostgreSQL con rotación diaria, semanal y mensual, y un script
de restauración. Ambos escriben y leen **en el mismo VPS** que protegen: un fallo de disco, un
borrado del volumen o la pérdida del servidor se lleva por delante la base y sus copias a la
vez. El respaldo tampoco está cifrado en reposo ni cubre la configuración ni los medios subidos
por los clientes, que hoy viven en el sistema de archivos del propio servidor.

No hay constancia de que la restauración se haya ejercitado nunca. Un backup que no se ha
restaurado es una hipótesis: un volcado truncado, una versión de PostgreSQL incompatible o una
variable de entorno perdida solo se descubren el día del incidente. Tampoco existen objetivos
declarados de pérdida máxima ni de tiempo de recuperación, de modo que no hay forma de decir si
el resultado de un incidente fue aceptable.

## Alcance

- Respaldo diario, cifrado en reposo, replicado a un destino externo al VPS productivo.
- Cobertura de los tres conjuntos: base de datos, configuración de despliegue y medios.
- Objetivos declarados: pérdida máxima de 24 horas y recuperación en menos de ocho horas.
- Procedimiento de restauración completo, ejecutable desde cero sobre un host limpio.
- Ejercicio de restauración trimestral y después de cualquier cambio material al sistema de
  respaldo, con evidencia fechada del resultado.
- Verificación automática de que cada respaldo existe, es legible y tiene antigüedad dentro de
  objetivo, con alerta cuando no lo está.
- Custodia de la clave de cifrado fuera del VPS y fuera del propio respaldo.

## Historias de usuario

Tomadas de la spec maestra:

- **102** — backups diarios cifrados fuera del VPS.
- **103** — pérdida máxima objetivo de 24 horas.
- **104** — recuperación objetivo inferior a ocho horas.
- **105** — comprobación de una restauración trimestral.

## Decisiones de implementación

- **Un respaldo que vive en el host que protege no es un respaldo.** La copia local se conserva
  como camino rápido de recuperación, pero el respaldo de referencia es la copia remota
  cifrada. El criterio de éxito del proceso diario es que la copia remota exista y sea legible,
  no que el volcado local se haya escrito.
- **Cifrado en origen, antes de salir del servidor.** El destino externo se trata como no
  confiable: el contenido se cifra en el VPS y solo el texto cifrado sale. La clave se custodia
  fuera del VPS y fuera del respaldo; un respaldo que contiene su propia clave no protege
  frente al escenario que motiva el cifrado.
- **Tres conjuntos, un mismo ciclo.** La base de datos es el volcado consistente. La
  configuración es el conjunto de variables y secretos necesarios para levantar el sistema,
  cifrado con la misma clave. Los medios son los archivos subidos por los clientes; mientras
  vivan en el sistema de archivos del VPS entran en este ciclo, y cuando `07` los mueva a
  almacenamiento de objetos, este ciclo se reduce a verificar la política de durabilidad de ese
  almacenamiento en lugar de copiarlos.
- **Los objetivos son cifras verificables, no aspiraciones.** La pérdida máxima de 24 horas se
  comprueba midiendo la antigüedad del respaldo remoto más reciente. La recuperación en menos
  de ocho horas se comprueba cronometrando el ejercicio de restauración de extremo a extremo.
  Ambas cifras se registran; incumplirlas es una alerta, no una nota.
- **La restauración se ejercita sobre un host limpio, no sobre producción.** El ejercicio parte
  únicamente del respaldo remoto y de la clave custodiada, sin ningún artefacto del servidor
  original. Es la única forma de descubrir qué falta.
- **El criterio de éxito del ejercicio es funcional, no de tamaño.** Se considera restaurado
  cuando la aplicación levanta, una Organización real puede autenticarse, su histórico de
  Snapshot responde y su configuración de Meta está presente y descifrable. Comparar bytes o
  contar filas no demuestra eso.
- **La evidencia se conserva versionada.** Cada ejercicio deja un registro fechado con quién lo
  ejecutó, qué respaldo se usó, cuánto tardó cada fase y qué falló. Sin ese registro, el
  requisito trimestral no es auditable.

### Contradicción con ADR implícitas

Ninguna. Esta spec no modifica decisiones existentes; cubre un área que las ADR implícitas no
documentan.

## Criterios de aceptación

1. Existe un respaldo diario de la base de datos en un destino externo al VPS productivo. Sí/No.
2. El contenido del respaldo remoto está cifrado y no es legible sin la clave custodiada. Sí/No.
3. La clave de cifrado no está almacenada en el VPS ni dentro del respaldo. Sí/No.
4. El respaldo cubre base de datos, configuración de despliegue y medios. Sí/No.
5. Existe una comprobación automática diaria de que el respaldo remoto más reciente existe, es
   legible y tiene menos de 24 horas. Sí/No.
6. Cuando esa comprobación falla, se emite una alerta que llega por un canal externo al VPS.
   Sí/No.
7. Existe un procedimiento escrito de restauración que parte de un host limpio y solo del
   respaldo remoto más la clave. Sí/No.
8. El procedimiento se ha ejecutado completo al menos una vez y su duración medida es inferior
   a ocho horas. Sí/No.
9. Tras la restauración de ejercicio, la aplicación levanta, un usuario de una Organización
   real se autentica, su histórico de Snapshot responde y su credencial de Meta se descifra.
   Sí/No.
10. Existe evidencia fechada del ejercicio, versionada en el repositorio, con duración por fase
    y hallazgos. Sí/No.
11. Existe una cadencia trimestral declarada y un disparador documentado para repetir el
    ejercicio tras cambios materiales al sistema de respaldo. Sí/No.
12. El ejercicio de restauración no toca la base de datos de producción en ningún paso. Sí/No.

## Decisiones de testing

- La frontera es el **drill operativo completo**: respaldo, transferencia, cifrado,
  restauración sobre host limpio y verificación funcional de la aplicación. No hay unidad de
  prueba que sustituya esto, porque lo que falla en una restauración es siempre lo que no está
  en el guion.
- La comprobación diaria de antigüedad y legibilidad sí es automatizable y se prueba
  inyectando los dos fallos que importan: no hay respaldo nuevo, y el respaldo está corrupto.
- El descifrado se prueba con la clave correcta y con una incorrecta; la segunda debe fallar de
  forma limpia y ruidosa, no producir un archivo parcial.
- La verificación funcional posterior a la restauración reutiliza el mismo recorrido de sistema
  que valida el producto —autenticación, lectura de histórico, configuración de Meta— en vez de
  inventar comprobaciones propias.
- La evidencia del ejercicio es parte del entregable: una prueba verde sin registro fechado no
  satisface el criterio 10.

## Dependencias

Ninguna. Puede ejecutarse en paralelo con `01`, `02` y `03`; es la red de seguridad bajo la que
esas tres se despliegan.

## Fuera de alcance

- Alta disponibilidad, réplica en caliente y conmutación automática. La spec maestra los
  excluye explícitamente hasta que la carga los justifique.
- Respaldo continuo o recuperación a un punto en el tiempo arbitrario.
- Migración de los medios a almacenamiento de objetos: pertenece a `07`. Aquí solo se respaldan
  donde estén.
- Eliminación de datos por baja de un cliente y su interacción con los respaldos ya tomados:
  pertenece a `09`.
- Monitorización general del VPS —disco, memoria, colas— más allá de la salud del respaldo:
  pertenece a `13`.

## Riesgos y rollback

- **Daño máximo si no se hace:** la pérdida del VPS destruye el histórico completo de todos los
  clientes, incluidas las credenciales de Meta y los medios. Es el único fallo de esta lista
  del que no se vuelve.
- **Daño máximo al hacerlo:** el proceso de respaldo satura el disco o la CPU de un servidor de
  2 vCPU y 4 GB durante su ejecución, degradando el producto; o el volcado se toma sin
  consistencia y produce un respaldo que restaura una base rota.
- **Activación:** el ciclo se activa en horario de baja actividad, con límites de recursos
  aplicados al proceso y con la copia local conservada durante el periodo de transición. No se
  retira ningún mecanismo actual hasta que el primer ejercicio de restauración haya pasado.
- **Rollback:** el ciclo de respaldo es aditivo y se puede desactivar sin afectar al producto.
  El riesgo real no es el rollback sino su ausencia: desactivarlo debe emitir una alerta, no
  pasar inadvertido.

## Checklist de finalización

- [ ] Proceso de respaldo diario cifrado hacia destino externo, con límites de recursos
      adecuados al VPS.
- [ ] Custodia de la clave de cifrado documentada, fuera del VPS y fuera del respaldo.
- [ ] Comprobación automática diaria de existencia, legibilidad y antigüedad, con alerta
      externa.
- [ ] Procedimiento de restauración escrito, ejecutable desde un host limpio.
- [ ] Primer ejercicio de restauración completo, cronometrado, con evidencia fechada versionada
      en el repositorio.
- [ ] Cadencia trimestral declarada y disparador documentado para cambios materiales.
- [ ] Documentación de infraestructura actualizada: qué se respalda, dónde, con qué clave, con
      qué objetivos y quién los verifica.
- [ ] Observabilidad: antigüedad del último respaldo remoto y resultado de la última
      verificación, expuestos como señal monitorizable.

## Trazabilidad

Secciones de la spec maestra que originan esta spec: *Recuperación y observabilidad* (párrafos
de backup, RPO/RTO y restauración trimestral); *Problem Statement* (párrafo del VPS limitado y
la necesidad de backups externos y restauraciones comprobadas); *Testing Decisions* (párrafo
del seam operativo y la evidencia fechada); *Entrega por fases* → **P0**.
