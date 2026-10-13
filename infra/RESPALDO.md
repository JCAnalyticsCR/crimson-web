# Respaldo, recuperación y salida de datos

Cómo se respalda la plataforma Crimson, dónde se guarda, cuánto se conserva y cómo se **prueba** que el
respaldo sirve. Un respaldo que nunca se restauró no es un respaldo: es una esperanza.

> Nada de esto está configurado en Railway todavía. Este documento dice qué hacer; los pasos en Railway
> los ejecuta una persona con acceso, a mano, una vez.

---

## 1. Qué hay que respaldar

| Qué | Dónde vive hoy | Cómo se respalda |
|---|---|---|
| Base de datos (todas las tablas) | Postgres de Railway | `python -m app.tools.backup` (zip portable) y opcionalmente `pg_dump` |
| Archivos (fotos, PDF, logos, comprobantes) | **Dentro de Postgres**, tabla `media`, columna `data` (bytea) | Van dentro del mismo zip (`blobs/media/...`) |
| Variables de entorno (`JWT_SECRET`, `DATABASE_URL`, llaves de correo, BCCR, ONVO) | Railway → Variables | A mano, en el gestor de contraseñas de la empresa. **Nunca** junto al respaldo |
| Código | GitHub | Ya está versionado; el respaldo anota la migración de Alembic para saber con qué versión restaurar |

Notas importantes:

- **`JWT_SECRET` es parte del respaldo aunque no viaje en el zip.** Los secretos de pasarelas y de la
  bandeja de correo se guardan cifrados con una llave derivada de `JWT_SECRET`. Sin ese valor la base se
  restaura completa, pero esos secretos hay que volver a escribirlos en Ajustes.
- Las **sesiones** (`refresh_token`) no se respaldan nunca: después de restaurar todos vuelven a iniciar sesión.
- Si algún día los archivos salen de Postgres (a S3/R2 o a un volumen de Railway), ver la sección 8.

## 2. El archivo de respaldo

`crimson-respaldo-<alcance>-<AAAAMMDD-HHMMSS>.zip`:

```
manifest.json          versión de migración (alembic), fecha UTC, motor, alcance, conteos por tabla,
                       SHA-256 de cada archivo y un SHA-256 global
data/<tabla>.json      filas de cada tabla, en orden de llave primaria, con valores canónicos
blobs/media/<id>_data  cada archivo de multimedia, tal cual
media_index.csv        lista legible de archivos: id, empresa, nombre, tipo, tamaño, sha256, fecha
```

- Es **independiente de pg_dump**: se restaura igual en SQLite o en Postgres, y de uno a otro.
- Los valores se guardan de forma canónica (fechas en UTC, decimales sin ceros sobrantes, JSON ordenado),
  así que el hash de una tabla es el mismo antes y después de restaurar. La restauración lo comprueba.
- Contiene **datos personales** (clientes, colaboradores, planillas) y, en el respaldo completo, los hashes
  de contraseñas. Se trata como información confidencial: cifrado en reposo y acceso restringido.

## 3. Respaldo manual

Desde `api/` (con `DATABASE_URL` apuntando a la base que se quiere respaldar):

```bash
uv run python -m app.tools.backup                          # toda la base -> ./respaldos/
uv run python -m app.tools.backup --salida /ruta/x.zip     # ruta exacta
uv run python -m app.tools.backup --tenant crimson         # solo una empresa
uv run python -m app.tools.backup --sin-archivos           # sin los binarios (mucho más liviano)
uv run python -m app.tools.backup --redactar               # sin contraseñas ni secretos (para terceros)
uv run python -m app.tools.backup --database-url "postgresql://..."   # otra base
```

Termina con código 0 y una línea `OK <ruta> sha256=<hash>`. En Postgres usa una transacción
`REPEATABLE READ` de solo lectura: la foto es consistente aunque la gente esté trabajando.

Si `pg_dump` está en el PATH y la base es Postgres, deja además `<mismo-nombre>.pgdump` (formato custom,
se restaura con `pg_restore`). Es el plan B; el zip es el que se prueba. Se desactiva con `--sin-pg-dump`.
La contraseña de la base se le pasa a `pg_dump` por variable de entorno, nunca en la línea de comandos.

**Los respaldos nunca van al repositorio** (el repo es público). `.gitignore` ya excluye `respaldos/`,
`crimson-respaldo-*.zip`, `crimson-datos-*.zip` y `*.pgdump`.

## 4. Programarlo en Railway (pendiente de hacer a mano)

### Opción recomendada: un servicio cron aparte

Railway corre un servicio con "Cron Schedule": arranca a la hora indicada, ejecuta el comando y se apaga
(no consume mientras no corre).

1. En el proyecto de Railway: **New → GitHub Repo** con este mismo repositorio; nombre `respaldo`.
2. Settings → Root Directory: `api`. Mismo builder que el servicio de la API.
3. Variables: `DATABASE_URL` como referencia a la del Postgres (`${{Postgres.DATABASE_URL}}`), `ENV=production`,
   `JWT_SECRET` (la misma de la API; la valida la configuración) y las credenciales del almacenamiento externo
   (ver abajo). Nada más.
4. Settings → Cron Schedule: `15 9 * * *` (09:15 UTC = 03:15 hora de Costa Rica, todos los días).
5. Start command (ejemplo con rclone hacia un bucket S3 compatible; `RCLONE_CONFIG_*` en variables):

   ```bash
   python -m app.tools.backup --salida /tmp/crimson.zip --sin-pg-dump \
     && age -r "$RESPALDO_AGE_PUBLIC_KEY" -o /tmp/crimson.zip.age /tmp/crimson.zip \
     && rclone copyto /tmp/crimson.zip.age "respaldo:crimson-respaldos/diario/crimson-$(date -u +%Y%m%d-%H%M).zip.age"
   ```

   - `age` cifra con una llave **pública**: el servicio puede cifrar pero no descifrar. La llave privada
     vive solo en el gestor de contraseñas (2 personas con acceso).
   - Si el builder no trae `age`/`rclone`, agregarlos como paquetes del build (Nixpacks: `aptPkgs`) o usar
     una imagen propia. No cambia el código de la aplicación.
6. Revisar el primer día: en los logs del servicio debe aparecer `OK ... sha256=...`. Si sale con código
   distinto de 0, Railway marca la ejecución como fallida: activar la notificación de fallos del servicio.

### Alternativa: tarea del worker (Celery beat)

Si se prefiere no tener otro servicio, se puede agregar una tarea al `worker/celery_app.py` que llame a
`app.tools.backup.main([...])` y suba el archivo. Desventaja: el worker necesita las herramientas de
cifrado y subida, y un respaldo pesado compite con las tareas normales. Por eso se recomienda el cron aparte.

### Dónde guardarlo: FUERA de Railway

Un respaldo en el mismo proveedor que la base no protege de un problema con la cuenta o la región.

- **Recomendado:** bucket S3 compatible en otro proveedor (Backblaze B2, Cloudflare R2 o AWS S3) con
  **versionado** activado y, si el proveedor lo permite, **bloqueo de objetos** (object lock) en modo
  gobernanza por 30 días: ni un error ni una credencial robada pueden borrar los respaldos recientes.
- Credenciales del bucket con permiso **solo de escritura** en la carpeta de respaldos (sin listar ni borrar).
- Copia mensual adicional descargada a un medio de la empresa (disco cifrado), fuera de la nube.
- Nunca: en el repositorio, en un chat, en correo, ni en carpetas sincronizadas personales.

### Retención sugerida

| Tipo | Frecuencia | Se conserva |
|---|---|---|
| Diario | todos los días 03:15 | 14 días |
| Semanal | el del domingo | 8 semanas |
| Mensual | el del día 1 | 12 meses |
| Anual | el del 1 de enero | al menos 5 años (registros contables y tributarios; confirmar el plazo con el contador) |

Se implementa con reglas de ciclo de vida del bucket por carpeta (`diario/`, `semanal/`, `mensual/`,
`anual/`): el cron copia el mismo archivo a las carpetas que correspondan según la fecha.

Objetivo con esta pauta: se pierde como máximo un día de datos (RPO 24 h) y se recupera en menos de 2 horas
(RTO), siempre que la prueba de la sección 5 se haga de verdad.

## 5. Prueba de recuperación (mensual, paso a paso)

Responsable: quien administre la plataforma. Duración: 20–30 minutos. Se hace en una base **nueva y vacía**,
nunca sobre producción.

1. **Bajar el último respaldo** del bucket (`rclone copy respaldo:crimson-respaldos/diario/<último> .`).
2. **Descifrarlo** en una máquina de confianza: `age -d -i llave-privada.txt -o crimson.zip crimson.zip.age`.
3. **Mismo código que el respaldo:** abrir `manifest.json`, mirar `alembic_revisions` y hacer checkout de un
   commit con esa migración como cabeza (`uv run alembic heads` debe mostrarla).
4. **Restaurar en una base vacía** (cualquiera de las dos):
   - Local, rápido: `uv run python -m app.tools.restore crimson.zip --target sqlite:///./prueba-recuperacion.db`
   - Postgres de prueba (un entorno aparte en Railway o un Postgres local):
     `uv run python -m app.tools.restore crimson.zip --target postgresql://usuario:clave@host:5432/crimson_prueba`

   La herramienta: verifica el hash de cada archivo del zip (si alguien lo alteró, se detiene); **se niega**
   si la base destino tiene datos; crea el esquema si no existe; inserta todo en **una sola transacción**
   respetando las llaves foráneas; y al final relee la base y compara conteos y hash de cada tabla.
   Debe terminar con `OK restaurado y verificado`. Cualquier otro final = la prueba falló.
5. **Probarla funcionando:** levantar la API contra esa base
   (`DATABASE_URL=sqlite:///./prueba-recuperacion.db uv run uvicorn app.main:app --port 8099`), entrar con
   un usuario real y revisar: última factura emitida, último pago, una foto de un levantamiento, el reporte
   de vehículos del mes. (Si el respaldo era `--redactar`, las contraseñas no sirven: es lo esperado.)
6. **Anotar el resultado** en la bitácora de operaciones: fecha, archivo probado, su `sha256`, minutos que
   tomó, resultado y quién lo hizo.
7. **Borrar la base de prueba** y el zip descifrado (contienen datos personales).

Códigos de salida de `restore`: 0 bien · 2 no existe el archivo · 3 zip dañado o alterado ·
4 la base destino no está vacía · 5 versión de migración distinta · 6 error al insertar (no quedó nada a medias) ·
7 la verificación posterior no coincide.

## 6. Recuperación real (desastre)

1. Poner la API en mantenimiento (escalar a 0 réplicas) para que nadie escriba en una base a medias.
2. Crear un Postgres nuevo (no reutilizar el dañado; se conserva para análisis).
3. Hacer deploy del commit cuya migración coincide con la del respaldo (ver `manifest.json`).
4. Restaurar: `python -m app.tools.restore crimson.zip --target <DATABASE_URL del Postgres nuevo>`.
   Si el esquema ya lo creó `alembic upgrade head` y la migración coincide, también funciona.
5. Apuntar `DATABASE_URL` de la API y del worker al Postgres nuevo. Si el código actual es más nuevo,
   correr `alembic upgrade head` (el respaldo dejó marcada su migración).
6. Verificar como en la sección 5, punto 5. Reabrir la API.
7. Si `JWT_SECRET` cambió, volver a escribir en Ajustes los secretos de pasarelas, API y bandeja de correo.
8. Avisar a los usuarios que deben volver a iniciar sesión.

Plan B si el zip no restaura: `pg_restore --no-owner -d <url-nueva> crimson.pgdump`.

## 7. "Exportar todos mis datos" (salida de datos)

En **Ajustes → Respaldo**, el administrador de la empresa descarga un zip con **todos los datos de su
empresa** en el mismo formato (JSON por tabla + archivos + manifest con hashes). Diferencias con el respaldo
técnico:

- Solo la empresa de quien lo pide (nunca datos de otra empresa).
- **Sin** contraseñas, 2FA, sesiones ni secretos de pasarelas/API (quedan marcados como `!redactado`).
- Solo el rol administrador. Cada descarga queda en la bitácora (`export_all`) con el hash del archivo.

Sirve para entregar los datos al cliente si deja la plataforma, para auditorías o para análisis propios.
Se puede restaurar en una base vacía con la misma herramienta (los usuarios tendrán que restablecer contraseña).

## 8. Archivos (multimedia)

**Hoy:** viven en Postgres (`media.data`). El respaldo los incluye y `media_index.csv` lista cada uno con
su SHA-256, así que se puede comprobar uno por uno. La restauración verifica que cada archivo restaurado
tenga el mismo hash.

**Si se mueven a un bucket S3/R2:** activar versionado en el bucket, replicar a un segundo bucket en otro
proveedor (`rclone sync origen: destino: --backup-dir` en el mismo cron) y respaldar la tabla `media` (que
quedaría solo con la llave/URL) con `--sin-archivos`.

**Si se mueven a un volumen de Railway:** los volúmenes no se copian solos fuera de Railway. Agregar al
cron un `tar` del volumen cifrado con `age` y subido igual que el zip.

En pruebas y desarrollo **no se descargan archivos reales de producción**: se usan datos inventados.
