# Respaldo, recuperación y salida de datos

Cómo se respalda la plataforma Crimson, dónde se guarda, cuánto se conserva y cómo se **prueba** que el
respaldo sirve. Un respaldo que nunca se restauró no es un respaldo: es una esperanza.

> El código del respaldo diario y de la prueba de restauración ya está en el repo (sección 4). Crear el servicio
> en Railway, el bucket y las llaves lo hace una persona con acceso, a mano, una vez.

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

## 4. Respaldo diario automático (servicio cron `respaldo`)

Todos los días a las **03:15 hora de Costa Rica** Railway arranca el servicio `respaldo` (imagen
`infra/respaldo/Dockerfile`), que corre `python -m app.tools.respaldo_diario` y se apaga:

1. `app.tools.backup` de toda la base (con archivos) a un zip temporal.
2. **Verificación:** restaura ese zip en una SQLite desechable y compara conteos y hashes de cada tabla
   (`app.tools.restore`). Si falla, el cifrado igual se sube (puede servir en Postgres), pero cuenta como falla y avisa.
3. **Cifrado** con `age` para las llaves **públicas** de los custodios: el servicio cifra pero no puede descifrar.
   Antes de subir comprueba que lo que va a subir es un archivo de `age`: **nunca sube nada en claro**. La llave de la
   prueba mensual (4.2 y 4.4) solo abre la copia de `diario/`: los días que también van a `semanal/`, `mensual/` o
   `anual/`, esas carpetas reciben un segundo `.age` cifrado **solo** para los custodios (mismo zip, mismo nombre, su
   propio resumen con su hash).
4. **Subida** con `rclone` a `diario/` (y a `semanal/` los domingos, `mensual/` el día 1 y `anual/` el 1 de enero),
   junto a un resumen `.json` **sin datos personales** (hashes, tamaños, filas, migración y commit). El resumen se sube
   después del `.age`: si el resumen existe, el cifrado está completo.
5. **Avisos:** latido al monitor si todo salió bien; si algo falló, latido de falla y correo (sección 4.5).
6. **Registro:** la corrida queda en `task_run` (tarea `respaldo.diario`: inicio, fin, ok, hash, tamaño, filas,
   verificación o el error, sin datos personales). Va después de los avisos y nunca tumba el respaldo: si la base no
   responde o la tabla todavía no existe, solo queda en el log. **Pendiente:** `task_run`, el correo de Salud y
   `/health/deep` llegan con el ítem 1.8; hasta integrarlo, el estado del respaldo se ve en el log, el monitor y el
   bucket (4.5).

Los temporales (tienen datos personales) se borran siempre. El log termina con
`OK respaldo diario crimson-AAAAMMDD-HHMMSS.zip.age sha256=… filas=… verificacion=restaurado` o con
`FALLO <paso>: …` y un código distinto de 0: 1 configuración · 2 respaldo · 3 verificación · 4 cifrado · 5 subida.
Los nombres de archivo llevan la fecha y hora **UTC**.

Se eligió un servicio cron y no una tarea del worker: el worker no necesita las herramientas de cifrado ni las
credenciales del bucket, un respaldo pesado no compite con las tareas normales y el cron no consume mientras no corre.

### 4.1 Almacenamiento externo: FUERA de Railway

Un respaldo en el mismo proveedor que la base no protege de un problema con la cuenta o la región.

- **Recomendado:** bucket S3 compatible en otro proveedor (Backblaze B2, Cloudflare R2 o AWS S3) con
  **versionado** y, si el proveedor lo permite, **bloqueo de objetos** (object lock / retención) por 14 días: ni un
  error ni una credencial robada pueden borrar los respaldos recientes.
- **Credencial de escritura** para el servicio `respaldo`: solo subir objetos (sin listar, leer ni borrar) cuando el
  proveedor lo permite (B2: capacidad `writeFiles`; AWS: política con `s3:PutObject` y `s3:AbortMultipartUpload`).
  Si el proveedor no tiene credenciales de solo escritura, el bloqueo de objetos es lo que impide borrar.
  Con esa credencial rclone **no puede** leer lo que sube: el respaldo sube con `--no-check-dest` (no mira si el
  destino existe) y `--s3-no-head` (no hace el `HEAD` de verificación después del `PUT`, que daría 403 y dejaría la
  subida por fallida; la integridad la confirma el MD5 que devuelve el `PUT`). No quitar esos dos flags del código.
- **Credencial de lectura**, aparte, solo para la prueba de restauración (4.4), y lo más angosta que permita el
  proveedor: **solo listar y leer** (nada de escribir ni borrar) y **solo el prefijo `diario/`** (`<prefijo>/diario/`
  si `RESPALDO_DESTINO` lleva prefijo), que es lo único que la prueba baja:
  - B2: llave de aplicación limitada al bucket, con `namePrefix` = `diario/` y solo `listFiles` y `readFiles`.
  - AWS S3: `s3:ListBucket` sobre el bucket con la condición `StringLike` `s3:prefix` = `diario/*`, y `s3:GetObject`
    sobre `arn:aws:s3:::<bucket>/diario/*`. Nada más.
  - Cloudflare R2 no limita por prefijo: la credencial queda de solo lectura sobre ese bucket. Lo que protege
    `semanal/`, `mensual/` y `anual/` es que la llave de prueba no los abre (4.2).
- Copia mensual adicional descargada a un medio de la empresa (disco cifrado), fuera de la nube.
- Nunca: en el repositorio, en un chat, en correo, ni en carpetas sincronizadas personales (OneDrive, Drive).

### 4.2 Llaves de cifrado (age)

- Cada custodio (una persona del responsable técnico y una de Crimson) genera su par **en una máquina de confianza**:
  `age-keygen -o custodio.txt`. La línea `# public key: age1…` es la pública. El archivo completo (la privada) va al
  gestor de contraseñas y a ningún otro lado.
- `RESPALDO_AGE_RECIPIENTS` = las públicas **de los custodios**, separadas por coma. Si alguien pega ahí una privada
  (`AGE-SECRET-KEY-…`), el respaldo se niega a correr y avisa: hay que rotar ese par.
- Para la prueba automática (4.4) se genera un **tercer par**, solo para eso, y su pública va en
  `RESPALDO_AGE_RECIPIENTS_PRUEBA`, **nunca** en `RESPALDO_AGE_RECIPIENTS`: así solo abre la copia de `diario/` (14 días
  de retención) y no los respaldos que se guardan meses o años. Si la misma llave está en las dos variables, el
  respaldo se niega a correr y avisa. Su privada vive en GitHub (4.4), por eso:
  - **Se rota cada 12 meses** y cada vez que alguien con acceso a la configuración del repositorio o de Railway deja
    el equipo: (1) generar el par nuevo en una máquina de confianza; (2) en Railway, poner la pública nueva en
    `RESPALDO_AGE_RECIPIENTS_PRUEBA` (en lugar de la vieja); (3) en GitHub, reemplazar `RESPALDO_AGE_IDENTITY` por la
    privada nueva **después** del siguiente respaldo diario (la prueba baja siempre el último); (4) correr la prueba a
    mano ("Run workflow") y verla en verde; (5) **destruir** la privada vieja (de la máquina y del gestor de
    contraseñas). Los `.age` viejos de `diario/` vencen solos en 14 días.
  - Si se sospecha que se filtró: rotarla ya (mismos pasos) junto con la credencial de lectura del bucket, y tratarlo
    como incidente (4.4, "Qué expone").
- Si un custodio deja de serlo: se quita su pública y se agrega la del reemplazo. Los respaldos anteriores siguen
  cifrados también para él hasta que vencen por retención.

### 4.3 Crear el servicio en Railway (una vez)

1. **New → GitHub Repo** (este repositorio), nombre `respaldo`.
2. **Settings → Source:** Root Directory vacío (la raíz del repo) y la variable
   `RAILWAY_DOCKERFILE_PATH=infra/respaldo/Dockerfile`. Watch Paths `/api/**` y `/infra/respaldo/**`; "Wait for CI"
   encendido (ver `infra/DESPLIEGUE.md`).
3. **Settings → Deploy:** Cron Schedule `15 9 * * *` (09:15 UTC = 03:15 CR). Restart Policy: *Never* (el fallo ya
   avisó; reintentar en bucle no lo arregla).
4. **Rol de solo lectura en Postgres** (una vez, con el usuario dueño de las tablas, desde `railway connect Postgres`).
   El respaldo solo lee; si su contenedor se compromete, no puede escribir en producción. La clave se genera en el
   gestor de contraseñas y no pasa por ningún chat:

   ```sql
   CREATE ROLE respaldo LOGIN PASSWORD '<clave del gestor de contraseñas>';
   GRANT CONNECT ON DATABASE railway TO respaldo;      -- el nombre de la base de Railway
   GRANT pg_read_all_data TO respaldo;                 -- Postgres 14+: SELECT en todas las tablas, también las futuras
   -- cuando exista task_run (ítem 1.8), para que la corrida quede registrada:
   GRANT INSERT ON task_run TO respaldo;
   GRANT USAGE ON SEQUENCE task_run_id_seq TO respaldo;
   ```

   Sin `pg_read_all_data` (Postgres anterior a 14): `GRANT USAGE ON SCHEMA public TO respaldo; GRANT SELECT ON ALL
   TABLES IN SCHEMA public TO respaldo; ALTER DEFAULT PRIVILEGES FOR ROLE <dueño> IN SCHEMA public GRANT SELECT ON
   TABLES TO respaldo;`. Sin el `INSERT` en `task_run` el respaldo funciona igual (avisa en el log que no registró).
5. **Variables** (ningún valor real va al repositorio):

   | Variable | Valor |
   |---|---|
   | `DATABASE_URL` | la del rol `respaldo` (paso 4): `postgresql://respaldo:<clave>@${{Postgres.PGHOST}}:${{Postgres.PGPORT}}/${{Postgres.PGDATABASE}}`. **No** `${{Postgres.DATABASE_URL}}` (es el superusuario) |
   | `ENV` | `production` |
   | `JWT_SECRET` | **una propia**, aleatoria y de 32 caracteres o más, distinta de la del api (`python -c "import secrets; print(secrets.token_urlsafe(48))"`). Solo existe para que la configuración de la app valide. La del api **nunca** va a este servicio: deriva la llave que cifra los secretos de pasarelas y buzones que viajan en el respaldo, y firma las sesiones |
   | `RESPALDO_DESTINO` | `respaldo:<bucket>` (remoto `respaldo` de rclone + bucket, opcionalmente con un prefijo) |
   | `RESPALDO_AGE_RECIPIENTS` | llaves públicas `age1…` de los **custodios**, separadas por coma |
   | `RESPALDO_AGE_RECIPIENTS_PRUEBA` | la pública `age1…` de la llave de **prueba** (4.2), solo si la prueba automática está activa (4.4). Abre solo la copia de `diario/` |
   | `RCLONE_CONFIG_RESPALDO_TYPE` | `s3` |
   | `RCLONE_CONFIG_RESPALDO_PROVIDER` | el del proveedor (ver la documentación de rclone para S3) |
   | `RCLONE_CONFIG_RESPALDO_ENDPOINT` | URL S3 del proveedor |
   | `RCLONE_CONFIG_RESPALDO_ACCESS_KEY_ID`, `RCLONE_CONFIG_RESPALDO_SECRET_ACCESS_KEY` | la credencial de **escritura** |
   | `RCLONE_CONFIG_RESPALDO_NO_CHECK_BUCKET` | `true` (esa credencial no puede revisar ni crear el bucket) |
   | `RCLONE_CONFIG_RESPALDO_NO_HEAD` | `true` (no leer el objeto después de subirlo; el código ya pasa `--s3-no-head`, esto lo deja también en el remoto) |
   | `RESPALDO_PING_URL` | URL del latido del monitor (4.5) |
   | `RESPALDO_AVISO_A` | correos que reciben la falla, separados por coma |
   | `RESEND_API_KEY`, `MAIL_FROM` | las variables compartidas del correo (las mismas del api y el worker) |
   | `RESPALDO_VERIFICAR` | vacía (= `restaurar`); `hashes` solo si la restauración de prueba diera falsas alarmas |

   Nada más: **no** `FISCAL_KEYS` ni ninguna `HACIENDA_*` (el respaldo lleva el texto cifrado de las credenciales de
   Hacienda; la llave y el respaldo nunca están en el mismo servicio). El respaldo no descifra nada: el zip lleva los
   secretos tal como están en la base, cifrados.
6. **Revisar la configuración** sin tocar la base: Custom Start Command
   `python -m app.tools.respaldo_diario --revisar`, una corrida (`railway cron run` o "Run now"), y borrar el comando.
   Debe decir `OK configuracion`. Revisa las variables del respaldo, `age` y `rclone`, y que la configuración de la app
   cargue con estas variables (si falta `JWT_SECRET` o está mal, lo dice nombrando la variable, nunca su valor).
7. **Primera corrida real:** `railway cron run` y buscar `OK respaldo diario` en el log y los dos archivos en
   `diario/` del bucket.
8. **Probar el aviso** una vez, con el monitor todavía en pausa: `RESPALDO_VERIFICAR=x`, correr el cron, confirmar que
   llega el correo "falló el respaldo diario (configuracion)" y volver a dejar la variable vacía. Solo el correo:
   `python -m app.tools.respaldo_diario --probar-aviso`.

Listo cuando: 7 respaldos diarios seguidos en `diario/` y una prueba de restauración automática en verde (4.4).

### 4.4 Prueba de restauración automática (mensual)

`.github/workflows/restore-test.yml` corre el día 2 de cada mes a las 06:30 CR (y a mano con "Run workflow"):

1. `python -m app.tools.prueba_restauracion`: baja el resumen más reciente de `diario/` y su `.age`, exige que el
   respaldo tenga **menos de 36 h** (si no, el cron dejó de correr), compara el hash del cifrado con el resumen, lo
   descifra con la llave de **prueba** y compara el hash del zip.
   Las llaves existen en el disco del runner solo durante ese paso: se borran antes de cambiar de commit.
2. Vuelve al **commit** del respaldo (lo anota el resumen) para restaurar con el mismo esquema, **solo si ese commit
   está en `main`** (código revisado). Si no, restaura con `main`.
3. `python -m app.tools.restore` en una **Postgres vacía**: inserta todo y compara conteos y hashes de cada tabla.
4. Borra lo descifrado del runner, pase lo que pase.
5. Latido al monitor mensual: `<url>` si salió bien, `<url>/fail` si falló.

**El repositorio es público y cualquiera ve los logs de esta corrida.** Por eso nada derivado de los datos sale al
log: `restore` nombra tablas, columnas y conteos, nunca valores (sus errores dan el tipo, el SQLSTATE y la tabla o
restricción; el error completo solo con `--detalle-error`, en una máquina de confianza); su salida completa queda en
el disco temporal del runner; y el destino del bucket es un secreto (GitHub lo enmascara). Si se necesita ver un error
completo, se repite la prueba a mano (sección 5).

**Avisos:** el latido va a un monitor tipo heartbeat con período de **31 días** y 1 día de gracia, con las alertas a
los mismos destinatarios del respaldo diario. Así avisa si la prueba falla **y** si deja de correr (apagada, Actions
deshabilitado). GitHub, aparte, solo le escribe a quien tocó por última vez el cron del workflow: no cuenta como aviso.

**Qué expone.** La prueba descifra la base **completa** en un runner de GitHub (una máquina virtual efímera de un
tercero): datos personales de clientes y contactos, hashes de contraseñas, **semillas de 2FA en claro**
(`user.totp_secret`), hashes de invitaciones y de credenciales de API, y el texto cifrado de pasarelas y Hacienda.
Quien tenga la llave de prueba y la credencial de lectura puede leer los respaldos de `diario/` (hasta 14 días). Si se
filtran: rotar las dos (4.2), restablecer el 2FA de todos los usuarios, pedir cambio de contraseña y tratarlo como
incidente de datos personales. **Crimson acepta ese riesgo por escrito antes de activarla** (quién y cuándo, en la
bitácora de operaciones); si no lo acepta, la llave de prueba no se crea, `RESPALDO_PRUEBA_ACTIVA` queda apagada y la
prueba se hace a mano (sección 5).

**Requisitos para activarla (no son recomendaciones):**

1. **Environment `respaldo-prueba` con revisión obligatoria.** GitHub → Settings → Environments → `respaldo-prueba`:
   - *Deployment branches and tags*: solo `main`.
   - *Required reviewers*: los custodios (al menos uno), con *Allow administrators to bypass configured protection
     rules* **apagado**.
   - Por qué: limitar el environment a `main` **no** basta. Cualquiera con permiso de escritura en el repo (o un token
     de un colaborador que se filtre) puede subir a `main` un workflow propio que use `environment: respaldo-prueba` e
     imprima los secretos. Con revisión obligatoria, **todo** trabajo que pida ese environment (el de este workflow o
     uno nuevo) espera a que un custodio lo apruebe: si no lo reconoce, lo rechaza. Cada mes GitHub le escribe al
     revisor cuando la prueba queda esperando (espera hasta 30 días); si nadie aprueba, el latido no llega y el monitor
     mensual avisa.
   - Secretos del environment (nunca de repositorio): `RESPALDO_DESTINO` (igual que en Railway),
     `RESPALDO_AGE_IDENTITY` (el contenido del archivo de la llave de **prueba**), `RESPALDO_RCLONE_CONF` (un
     `rclone.conf` con un remoto `[respaldo]` y la credencial de **lectura** de 4.1) y `RESPALDO_PRUEBA_PING_URL` (la
     URL del latido del monitor mensual).
2. **Credencial de lectura** solo para listar y leer `diario/` (4.1), distinta de la de escritura.
3. **Llave de prueba** solo en `RESPALDO_AGE_RECIPIENTS_PRUEBA` (4.2), con su rotación anotada en la bitácora.

Además, recomendado: un *ruleset* en `main` (Settings → Rules) sin *force push* ni borrado, y con PR obligatorio
cuando todo el equipo trabaje por PR (hoy la landing se publica con commits directos a `main`). No reemplaza el
punto 1: con un solo mantenedor no se puede exigir la aprobación de otra persona (GitHub no deja aprobar el PR
propio), y quien llegue a `main` por cualquier camino igual tendría que pasar por la revisión del environment.

Y entonces:

1. Variable de repositorio `RESPALDO_PRUEBA_ACTIVA=true`. Mientras no esté, cada mes queda una advertencia "prueba
   APAGADA" en Actions (y el monitor, si ya existe, avisa).
2. Actions → "Prueba de restauracion" → Run workflow, aprobarla como revisor, y verla en verde (y el latido en el
   monitor).

### 4.5 Avisos

- **Latido (heartbeat):** un monitor tipo heartbeat (Better Stack, healthchecks.io o similar) con período de 24 h y
  2 h de gracia; su URL va en `RESPALDO_PING_URL`. El respaldo la llama al terminar bien y llama `<url>/fail` si
  falla. Si un día no llega nada (el cron ni arrancó), el monitor avisa igual. Alertas a los mismos destinatarios
  del monitor de la tienda.
- **Correo de falla:** a `RESPALDO_AVISO_A`, directo por Resend (no depende de la base, que puede ser lo que falló),
  con el paso que falló y el detalle. Los mensajes nombran variables, nunca sus valores, y quitan `usuario:clave@` de
  cualquier URL.
- **Railway:** activar las notificaciones de fallos del servicio.
- **Correo de Salud (07:00) y `/health/deep`: PENDIENTE de integrar con el ítem 1.8** (este código no los trae
  todavía). Lo que ya está listo en `app.services.respaldo_estado`:
  - `revision(db, now)`: la línea "Respaldo diario" del correo de Salud (interno): el último respaldo bueno, hace
    cuántas horas, filas, tamaño cifrado, el inicio del SHA-256 y el error de la última corrida si falló. Queda en
    **AVISO** (nunca en falla de la plataforma) si la última corrida falló o si pasaron más de 26 h sin un respaldo bueno.
  - `revision_publica(db, now)`: lo único que puede ir en `/health/deep` (lo consulta un monitor externo): `ok`,
    `critico`, `vencido` y las horas desde el último bueno. **Nunca** `revision()` ahí: el error trae hosts, usuarios
    o el bucket.

  Al fusionar 1.8: `"respaldo": respaldo_estado.revision(db, now)` en las revisiones de `app/tasks/salud.py`,
  `revision_publica` en `/health/deep`, una prueba en `test_salud.py` que verifique la línea, y correr
  `tests/test_infra_respaldo_diario.py` (la prueba `test_task_run_del_modelo_recibe_la_corrida` se activa sola con el
  modelo `TaskRun` y falla si sus columnas no calzan). Mientras tanto, el estado del respaldo está en el log del
  servicio, en el monitor de latido y en los archivos de `diario/`.

| Mensaje en el log o el correo | Qué revisar |
|---|---|
| `configuracion: faltan o estan mal: …` | la variable que nombra (ausente o mal escrita) |
| `respaldo: OperationalError …` | la base no responde o cambió `DATABASE_URL` |
| `verificacion: … codigo 5` | el servicio `respaldo` quedó con código viejo: redesplegarlo con la última migración |
| `cifrado: …` | `age` rechazó una llave pública |
| `subida: …` | credencial vencida, bucket inexistente o un bloqueo de objetos que exige Content-MD5 (actualizar rclone) |
| `subida: … 403 Forbidden` | la credencial no tiene `PutObject` en ese bucket o prefijo, o alguien quitó `--s3-no-head` del código (4.1) |
| `configuracion: configuracion de la app invalida: JWT_SECRET: …` | `JWT_SECRET` del servicio `respaldo` ausente, de desarrollo o de menos de 32 caracteres (4.3, paso 5) |

### Retención sugerida

| Tipo | Frecuencia | Se conserva |
|---|---|---|
| Diario | todos los días 03:15 | 14 días |
| Semanal | el del domingo | 8 semanas |
| Mensual | el del día 1 | 12 meses |
| Anual | el del 1 de enero | al menos 5 años (registros contables y tributarios; confirmar el plazo con el contador) |

Se implementa con reglas de ciclo de vida del bucket por carpeta (`diario/`, `semanal/`, `mensual/`,
`anual/`): el cron sube el mismo archivo a las carpetas que correspondan según la fecha de Costa Rica.

Objetivo con esta pauta: se pierde como máximo un día de datos (RPO 24 h) y se recupera en menos de 2 horas
(RTO), siempre que la prueba de restauración se haga de verdad (4.4 o sección 5).

## 5. Prueba de recuperación a mano (paso a paso)

La automática (4.4) corre cada mes. Esta, completa y con la API levantada, se hace al menos cada 3 meses, cuando la
automática no está activa o cuando falla.

Responsable: quien administre la plataforma. Duración: 20–30 minutos. Se hace en una base **nueva y vacía**,
nunca sobre producción.

Todo en una carpeta de trabajo **fuera del repositorio** (por ejemplo `~/recuperacion`), nunca dentro de `api/`: lo
descifrado no puede terminar en un commit ni en una imagen de Docker construida desde el repo.

1. **Bajar el último respaldo** del bucket a esa carpeta
   (`rclone copy respaldo:crimson-respaldos/diario/<último> ~/recuperacion/`).
2. **Descifrarlo** en una máquina de confianza:
   `age -d -i ~/recuperacion/llave-privada.txt -o ~/recuperacion/crimson.zip ~/recuperacion/crimson.zip.age`
   (la llave sale del gestor de contraseñas solo para esto y se borra al terminar).
3. **Mismo código que el respaldo:** abrir `manifest.json`, mirar `alembic_revisions` y hacer checkout de un
   commit con esa migración como cabeza (`uv run alembic heads` debe mostrarla).
4. **Restaurar en una base vacía** (cualquiera de las dos):
   - Local, rápido (desde `api/`):
     `uv run python -m app.tools.restore ~/recuperacion/crimson.zip --target sqlite:///$HOME/recuperacion/prueba.db`
   - Postgres de prueba (un entorno aparte en Railway o un Postgres local):
     `uv run python -m app.tools.restore ~/recuperacion/crimson.zip --target postgresql://usuario:clave@host:5432/crimson_prueba`

   La herramienta: verifica el hash de cada archivo del zip (si alguien lo alteró, se detiene); **se niega**
   si la base destino tiene datos; crea el esquema si no existe; inserta todo en **una sola transacción**
   respetando las llaves foráneas; y al final relee la base y compara conteos y hash de cada tabla.
   Debe terminar con `OK restaurado y verificado`. Cualquier otro final = la prueba falló.
5. **Probarla funcionando:** levantar la API contra esa base
   (`DATABASE_URL=sqlite:///$HOME/recuperacion/prueba.db uv run uvicorn app.main:app --port 8099`), entrar con
   un usuario real y revisar: última factura emitida, último pago, una foto de un levantamiento, el reporte
   de vehículos del mes. (Si el respaldo era `--redactar`, las contraseñas no sirven: es lo esperado.)
6. **Anotar el resultado** en la bitácora de operaciones: fecha, archivo probado, su `sha256`, minutos que
   tomó, resultado y quién lo hizo.
7. **Borrar la carpeta de trabajo** completa: la base de prueba, el zip descifrado y la llave (datos personales).

Códigos de salida de `restore`: 0 bien · 2 no existe el archivo · 3 zip dañado o alterado ·
4 la base destino no está vacía · 5 versión de migración distinta · 6 error al insertar (no quedó nada a medias; el
mensaje da el tipo, el SQLSTATE y la tabla o restricción, nunca los valores: `--detalle-error` muestra el error completo,
solo en una máquina de confianza) · 7 la verificación posterior no coincide.

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
