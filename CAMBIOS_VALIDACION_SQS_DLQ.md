# Revisión y cambios: validación de estructura y descarte a DLQ (SQS FIFO)

**Fecha:** 22/10/2026
**Alcance:** consumidor SQS FIFO del Motor de Reglas (`src/motor_reglas/`) e infraestructura asociada.
**Mensaje afectado:** novedad de un cliente en JSON plano (un mensaje = un cliente), publicado por la Capa Media.

---

## 1. Diagnóstico inicial

| Punto revisado | Estado encontrado | Riesgo |
|---|---|---|
| Validación de claves del JSON | **No existía**. `main.py` solo validaba que el `Body` fuera JSON decodificable. | Un mensaje incompleto se procesaba como si fuera válido. |
| Uso de los campos en el motor | `fuzzy_engine.py` y `orchestrator.py` usan `payload.get(...)` con defaults (`""`, `"0000"`, `"COLOMBIA"`, etc.). | Se generaban CU/PVU parciales sin error visible. |
| Descarte a DLQ por JSON inválido | El log decía “Descartando a DLQ”, pero **no se enviaba nada a la DLQ**; solo se hacía `continue`. | El mensaje esperaba 5 recepciones (`maxReceiveCount`) antes del redrive. |
| Confirmación del mensaje | Correcta: solo se borraba si el estado era `procesado` o `duplicado`. | Sin cambios; se conservó la semántica. |
| Mensajes envenenados en FIFO | Un mensaje estructuralmente inválido bloqueaba su `MessageGroupId` durante los reintentos. | Bloqueo temporal del grupo en la cola FIFO. |

**Conclusión:** el pipeline no tenía un contrato de entrada explícito y el descarte a DLQ era implícito (solo por redrive). Se implementó validación en el borde (consumidor) y descarte inmediato a la DLQ para errores permanentes.

---

## 2. Contrato de entrada

La validación exige la presencia de los **10 campos críticos** del payload:

`CodigoTransaccional`, `razonSocial`, `documento`, `pais`, `direccion`, `barrio`,
`codigoCiudad`, `codigoDepartamento`, `estadoCliente`, `situacionCliente`.

La validación es de **presencia de claves**, no de contenido: `razonSocial` o `barrio` pueden llegar como `""` o `null` y el mensaje se considera válido (caso típico de persona natural sin razón social). Además, los 11 campos complementarios del contrato son opcionales y su ausencia **no** descarta el mensaje.

| Grupo | Campos | ¿Obligatorio? |
|---|---|---|
| Críticos (10) | `CodigoTransaccional`, `razonSocial`, `documento`, `pais`, `direccion`, `barrio`, `codigoCiudad`, `codigoDepartamento`, `estadoCliente`, `situacionCliente` | **Sí** (debe existir la clave) |
| Complementarios (11) | `primerNombre`, `segundoNombre`, `primerApellido`, `segundoApellido`, `direccion2`, `direccion3`, `direccion4`, `direccion5`, `nombreNegocio`, `peticionDeBorrado`, `peticionDeBorradoIndividual` | No |

Contrato completo que publica la Capa Media (los 21 campos siguen documentados; solo los 10 críticos se exigen):

```json
{
  "CodigoTransaccional": "...",
  "primerNombre": "...",
  "segundoNombre": "",
  "primerApellido": "",
  "segundoApellido": "",
  "razonSocial": "...",
  "documento": "...",
  "direccion": "...",
  "direccion2": "",
  "direccion3": "",
  "direccion4": "",
  "direccion5": "",
  "barrio": "...",
  "codigoCiudad": "...",
  "codigoDepartamento": "...",
  "pais": "Colombia",
  "nombreNegocio": "...",
  "estadoCliente": "Activo",
  "peticionDeBorrado": "No",
  "peticionDeBorradoIndividual": "No",
  "situacionCliente": "Creado"
}
```

> **Decisión de diseño:** se valida la estructura (presencia de los 10 campos críticos), no las reglas de negocio de cada valor. Por ejemplo, `documento` puede llegar `null` y el mensaje pasa la validación estructural; las reglas de calidad del Motor CU/PVU siguen siendo las que califican el dato.

---

## 3. Flujo implementado

```
SQS FIFO (ccuc-novedades-prod.fifo)
        │
        ▼
receive_message (long polling)
        │
        ▼
┌─────────────────────────────────────────────┐
│ procesar_mensaje()                          │
│                                             │
│ 1. ¿JSON válido?                            │
│      NO → mover_a_dlq("JSON_INVALIDO")      │
│                                             │
│ 2. ¿Están los 10 campos críticos?           │
│      NO → mover_a_dlq("ESTRUCTURA_INVALIDA")│
│                                             │
│ 3. orchestrator.procesar_novedad()          │
│      OK (procesado/duplicado)               │
│          → delete_message                   │
│      ERROR transitorio                      │
│          → NO se borra (reintento/redrive)  │
└─────────────────────────────────────────────┘
        │
        ▼
ccuc-novedades-dlq.fifo  (solo errores permanentes)
```

### Reglas de descarte

| Situación | Acción | ¿Se borra de la cola principal? |
|---|---|---|
| JSON malformado (`{no es json`) | Envío inmediato a DLQ con `motivoDlq=JSON_INVALIDO` | Sí |
| JSON válido pero no es objeto (`[]`, `null`, `"texto"`) | Envío inmediato a DLQ con los 10 campos faltantes | Sí |
| Falta uno o más de los 10 campos críticos | Envío inmediato a DLQ con `motivoDlq=ESTRUCTURA_INVALIDA` | Sí |
| Faltan campos complementarios (`primerNombre`, `direccion2`, etc.) | Se procesa normalmente | Sí (si el pipeline confirma) |
| Error de Neptune / Lambda Egress / excepción del pipeline | No se envía a DLQ | No (reintento; luego redrive) |
| Pipeline devuelve estado no confirmado | No se envía a DLQ | No (reintento) |
| Envío a DLQ falla | El mensaje permanece en la cola | No (redrive policy) |
| No hay `DLQ_URL`/`DLQ_NAME` resoluble | Se registra error | No (redrive policy) |

### Atributos del mensaje en DLQ

Cada mensaje reenviado incluye:

| Atributo | Contenido |
|---|---|
| `motivoDlq` | Motivo legible, p. ej. `ESTRUCTURA_INVALIDA: faltan 2 campo(s): documento, codigoCiudad` (máx. 1024 caracteres). |
| `messageIdOrigen` | `MessageId` del mensaje original en la cola principal. |

Además se conservan `MessageGroupId` y `MessageDeduplicationId` (obligatorios en colas FIFO), y el `Body` original sin modificar.

---

## 4. Archivos modificados y nuevos

### Nuevos

| Archivo | Propósito |
|---|---|
| `src/motor_reglas/validacion.py` | Contrato de los 10 campos críticos, catálogo de los 11 opcionales y función `campos_faltantes(payload)`. |
| `src/motor_reglas/dlq.py` | Construcción y envío del mensaje a la DLQ; borrado de la cola principal. |
| `src/motor_reglas/consumer.py` | Orquesta por mensaje: validación → pipeline → confirmación/DLQ. |
| `tests/test_validacion_payload.py` | Pruebas del contrato y de claves faltantes. |
| `tests/test_consumer_dlq.py` | Pruebas de descarte a DLQ y confirmación/reintento. |
| `CAMBIOS_VALIDACION_SQS_DLQ.md` | Este documento. |

### Modificados

| Archivo | Cambio |
|---|---|
| `src/motor_reglas/main.py` | Resuelve `DLQ_URL`/`DLQ_NAME` y delega cada mensaje en `procesar_mensaje(...)`. Se eliminó el `continue` que no descartaba realmente a DLQ. |
| `infra/app.py` | Variable `DLQ_URL` en el contenedor y permiso `sqs:SendMessage` sobre la DLQ (`dlq.grant_send_messages`). |
| `docker-compose.yml` | Variables `DLQ_URL` y `DLQ_NAME` para LocalStack. |
| `scripts/simulate_sqs_messages.py` | Se agregó `CodigoTransaccional` a los 4 mensajes dummy para cumplir el contrato. |
| `scripts/simulate_sqs_messages_copy.py` | Ahora mapea el CSV al contrato completo de 21 campos en vez de publicar la fila cruda. |
| `README.md` | Sección “Validación de estructura y descarte a DLQ” y nuevas pruebas en la tabla. |
| `ARQUITECTURA_Y_EJECUCION_ECS_FARGATE.md` | Descripción actualizada del control de errores/DLQ y del permiso IAM. |

---

## 5. Detalle de la implementación

### `validacion.py`

```python
# Campos que SÍ se exigen (presencia de la clave).
CAMPOS_OBLIGATORIOS: Tuple[str, ...] = (
    "CodigoTransaccional",
    "razonSocial",
    "documento",
    "pais",
    "direccion",
    "barrio",
    "codigoCiudad",
    "codigoDepartamento",
    "estadoCliente",
    "situacionCliente",
)

# Campos del contrato que NO bloquean el mensaje si faltan.
CAMPOS_OPCIONALES: Tuple[str, ...] = (
    "primerNombre", "segundoNombre", "primerApellido", "segundoApellido",
    "direccion2", "direccion3", "direccion4", "direccion5",
    "nombreNegocio", "peticionDeBorrado", "peticionDeBorradoIndividual",
)

def campos_faltantes(payload) -> List[str]:
    if not isinstance(payload, dict):
        return list(CAMPOS_OBLIGATORIOS)
    return [campo for campo in CAMPOS_OBLIGATORIOS if campo not in payload]
```

### `dlq.py`

- `construir_mensaje_dlq(mensaje, motivo)` arma los kwargs de `send_message` conservando grupo/dedup.
- `enviar_a_dlq(...)` captura cualquier fallo y retorna `False`.
- `mover_a_dlq(...)` envía a la DLQ y, solo si el envío fue exitoso, elimina el mensaje original.

### `consumer.py`

- `procesar_mensaje(sqs, queue_url, dlq_url, mensaje, orchestrator, seen)` retorna:
  - `procesado` / `duplicado`: pipeline confirmado y mensaje borrado.
  - `dlq`: mensaje inválido derivado a la DLQ.
  - `reintento`: error transitorio; el mensaje no se borra.
- Se mantiene la regla existente: solo se confirma si el estado es `procesado` o `duplicado`.

### `main.py`

Antes:

```python
except json.JSONDecodeError as err:
    logger.error(f"Mensaje {msg_id} con JSON inválido: {err}. Descartando a DLQ.")
    continue   # ← no enviaba nada a la DLQ
```

Ahora:

```python
for msg in messages:
    procesar_mensaje(sqs, queue_url, dlq_url, msg, orchestrator, seen)
```

Resolución de la DLQ:

```python
dlq_url = os.environ.get("DLQ_URL")
if not dlq_url:
    dlq_name = os.environ.get("DLQ_NAME", "ccuc-novedades-dlq.fifo")
    try:
        dlq_url = sqs.get_queue_url(QueueName=dlq_name)["QueueUrl"]
    except ClientError:
        dlq_url = None   # se usará el redrive policy
```

---

## 6. Configuración e infraestructura

### Variables de entorno

| Variable | Obligatoria | Default | Uso |
|---|---|---|---|
| `QUEUE_URL` / `QUEUE_NAME` | Sí (una de las dos) | `ccuc-novedades-prod.fifo` | Cola principal. |
| `DLQ_URL` | Recomendada | — | URL explícita de la DLQ. |
| `DLQ_NAME` | Alternativa a `DLQ_URL` | `ccuc-novedades-dlq.fifo` | Resolución por nombre si no hay URL. |

### IAM (AWS)

El task role de ECS ahora requiere, además de consumir la cola principal:

```python
dlq.grant_send_messages(task_def.task_role)   # sqs:SendMessage sobre la DLQ
```

### LocalStack

`scripts/init-aws.sh` ya creaba la DLQ; no requirió cambios. `docker-compose.yml` añade:

```yaml
- DLQ_URL=http://dev-localstack:4566/000000000000/ccuc-novedades-dlq.fifo
- DLQ_NAME=ccuc-novedades-dlq.fifo
```

---

## 7. Pruebas agregadas

### `tests/test_validacion_payload.py`

- Los campos obligatorios son exactamente los 10 críticos.
- El contrato completo (10 obligatorios + 11 opcionales) suma 21 y no hay solapamiento.
- Payload completo → válido.
- Payload solo con los 10 obligatorios → válido.
- Claves extra → no invalidan.
- Una o varias claves obligatorias faltantes → se reportan en orden del contrato.
- Falta de cualquier campo opcional → **no** invalida el mensaje.
- Valores `""`/`null` en campos críticos cuentan como clave presente.
- Payload no-objeto (`[]`, `null`, texto, número) → carece de todos los campos obligatorios.

### `tests/test_consumer_dlq.py`

- Mensaje válido → pipeline + `delete_message`.
- Estado `duplicado` → también se confirma.
- Estructura incompleta (falta un campo crítico) → DLQ, **sin** llamar al orquestador.
- Campo opcional ausente → se procesa normalmente (no va a DLQ).
- Se conservan `MessageGroupId`, `MessageDeduplicationId` y `Body` original.
- JSON inválido y JSON no-objeto → DLQ.
- Excepción del pipeline → `reintento`, sin DLQ y sin borrar.
- Estado no confirmado → `reintento`, sin borrar.
- Sin DLQ configurada → no se borra el mensaje.
- Fallo al enviar a DLQ → no se borra el mensaje original.
- Sin `MessageGroupId` → se usa `SIN_GRUPO`.

---

## 8. Verificación ejecutada

```text
python -m pytest tests/ -q
54 passed in 26.64s

python -m pylint src
Your code has been rated at 10.00/10
```

También se validó la sintaxis de `main.py`, `consumer.py`, `dlq.py`, `validacion.py`, `infra/app.py` y de los scripts de simulación con `py_compile`.

---

## 9. Impacto operativo y consideraciones

1. **Mensajes históricos:** los mensajes encolados antes de este cambio que no incluyan los 10 campos críticos irán a la DLQ. Deben reenviarse con el contrato completo.
2. **Diagnóstico:** revisar el atributo `motivoDlq` de los mensajes en `ccuc-novedades-dlq.fifo` para saber exactamente qué campos críticos faltaron.
3. **Reproceso:** al corregir el mensaje se puede reenviar a la cola principal. La DLQ usa deduplicación por contenido y retención de 14 días.
4. **Errores transitorios:** siguen el flujo normal de reintento; no se descartan para no perder novedades por fallos de Neptune o de la Lambda Egress.
5. **Escalabilidad:** la validación es O(10) por mensaje y no agrega llamadas de red.
6. **Compatibilidad:** el contrato se valida en el consumidor; invocaciones directas al orquestador (pruebas o código interno) no se ven afectadas.

---

## 10. Validación manual en consola: ¿los errores llegan a la DLQ?

Esta sección permite comprobar de punta a punta que el código descarta correctamente los mensajes inválidos en `ccuc-novedades-dlq.fifo`. Los comandos usan `awslocal` dentro del contenedor de LocalStack y están escritos para **PowerShell o bash** (cada comando en una sola línea). En CMD, reemplazar las comillas simples del `--message-body` por comillas dobles escapadas.

### 10.1 Preparar el entorno

```powershell
# 1. Levantar LocalStack + contenedor del motor (arrastra gremlin/opensearch por depends_on)
docker compose up -d dev-localstack dev-motor-reglas

# 2. Confirmar que el entorno está saludable y que ambas colas existen
docker compose ps
docker exec dev-localstack awslocal sqs list-queues

# 3. Arrancar el consumidor en primer plano (dejar esta terminal abierta)
docker exec -it dev-motor-reglas python /app/motor_reglas/main.py
```

> `docker-compose.yml` sobrescribe el `CMD` del contenedor con `sleep infinity`, por eso el consumidor se inicia manualmente. Los logs de descarte aparecerán en esa misma terminal.

En una **segunda terminal**, verificar el estado inicial (debe ser `0` en ambas colas):

```powershell
docker exec dev-localstack awslocal sqs get-queue-attributes --queue-url http://localhost:4566/000000000000/ccuc-novedades-prod.fifo --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible

docker exec dev-localstack awslocal sqs get-queue-attributes --queue-url http://localhost:4566/000000000000/ccuc-novedades-dlq.fifo --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible
```

### 10.2 Caso A: estructura incompleta → debe ir a la DLQ

Enviar un JSON válido pero **sin los 10 campos obligatorios** (solo trae `CodigoTransaccional` y `documento`):

```powershell
docker exec dev-localstack awslocal sqs send-message --queue-url http://localhost:4566/000000000000/ccuc-novedades-prod.fifo --message-body '{"CodigoTransaccional":"TX-DLQ-01","documento":"1018273645"}' --message-group-id grupo-dlq --message-deduplication-id prueba-dlq-estructura-001
```

En los logs del consumidor debe aparecer algo como:

```text
Mensaje ... rechazado (ESTRUCTURA_INVALIDA: faltan 8 campo(s): razonSocial, pais, direccion, barrio, codigoCiudad, codigoDepartamento, estadoCliente, situacionCliente). Enviando a DLQ.
Mensaje ... descartado a DLQ: ESTRUCTURA_INVALIDA: faltan 8 campo(s): ...
```

### 10.3 Caso B: JSON malformado → debe ir a la DLQ

```powershell
docker exec dev-localstack awslocal sqs send-message --queue-url http://localhost:4566/000000000000/ccuc-novedades-prod.fifo --message-body '{esto no es un json}' --message-group-id grupo-dlq --message-deduplication-id prueba-dlq-json-001
```

En los logs debe aparecer:

```text
Mensaje ... con JSON inválido: ... Enviando a DLQ.
Mensaje ... descartado a DLQ: JSON_INVALIDO: ...
```

### 10.4 Verificar los mensajes en la DLQ

```powershell
docker exec dev-localstack awslocal sqs receive-message --queue-url http://localhost:4566/000000000000/ccuc-novedades-dlq.fifo --max-number-of-messages 10 --attribute-names All --message-attribute-names All --wait-time-seconds 5 --visibility-timeout 0
```

Se espera una salida con dos mensajes y, en cada uno:

| Campo de la salida | Valor esperado |
|---|---|
| `Body` | El JSON original enviado (sin modificar). |
| `Attributes.MessageGroupId` | `grupo-dlq`. |
| `Attributes.MessageDeduplicationId` | El `--message-deduplication-id` usado. |
| `MessageAttributes.motivoDlq.StringValue` | `ESTRUCTURA_INVALIDA: faltan ...` o `JSON_INVALIDO: ...`. |
| `MessageAttributes.messageIdOrigen.StringValue` | El `MessageId` original de la cola principal. |

> `--visibility-timeout 0` deja los mensajes visibles para poder repetir la consulta sin consumirlos.

Confirmar el conteo y que la cola principal quedó vacía:

```powershell
docker exec dev-localstack awslocal sqs get-queue-attributes --queue-url http://localhost:4566/000000000000/ccuc-novedades-dlq.fifo --attribute-names ApproximateNumberOfMessages

docker exec dev-localstack awslocal sqs get-queue-attributes --queue-url http://localhost:4566/000000000000/ccuc-novedades-prod.fifo --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible
```

Resultado esperado: `ApproximateNumberOfMessages = 2` en la DLQ y `0` en la cola principal.

### 10.5 Caso C: mensaje válido → NO debe ir a la DLQ

Enviar el payload completo (incluye los 10 campos obligatorios y los 11 complementarios):

```powershell
docker exec dev-localstack awslocal sqs send-message --queue-url http://localhost:4566/000000000000/ccuc-novedades-prod.fifo --message-body '{"CodigoTransaccional":"TX-OK-01","primerNombre":"Juan","segundoNombre":"Carlos","primerApellido":"Perez","segundoApellido":"Gomez","razonSocial":"","documento":"1018273645","direccion":"Calle 50 # 45-20","direccion2":"","direccion3":"","direccion4":"","direccion5":"","barrio":"El Poblado","codigoCiudad":"5001","codigoDepartamento":"5","pais":"Colombia","nombreNegocio":"Tienda La Esquina","estadoCliente":"Activo","peticionDeBorrado":"No","peticionDeBorradoIndividual":"No","situacionCliente":"Creado"}' --message-group-id doc-1018273645 --message-deduplication-id prueba-ok-001
```

En los logs debe aparecer `Resultado procesamiento msg ...` y `Mensaje ... eliminado exitosamente de SQS FIFO`. La DLQ debe seguir con **2** mensajes (los de los casos A y B).

**Variante válida: solo los 10 campos obligatorios** (sin `primerNombre`, `nombreNegocio`, etc.). Debe procesarse y **no** ir a la DLQ:

```powershell
docker exec dev-localstack awslocal sqs send-message --queue-url http://localhost:4566/000000000000/ccuc-novedades-prod.fifo --message-body '{"CodigoTransaccional":"TX-OK-02","razonSocial":"","documento":"987654321","pais":"Colombia","direccion":"Carrera 45 # 10-20","barrio":"Chapinero","codigoCiudad":"11001","codigoDepartamento":"11","estadoCliente":"Activo","situacionCliente":"Creado"}' --message-group-id doc-987654321 --message-deduplication-id prueba-ok-002
```

> Si Neptune, OpenSearch o la Lambda Egress no están disponibles, el pipeline puede quedar en reintento (el mensaje no se borra). Aun así, **no debe aparecer en la DLQ** por este motivo.

### 10.6 Limpiar después de la prueba

```powershell
docker exec dev-localstack awslocal sqs purge-queue --queue-url http://localhost:4566/000000000000/ccuc-novedades-dlq.fifo
docker exec dev-localstack awslocal sqs purge-queue --queue-url http://localhost:4566/000000000000/ccuc-novedades-prod.fifo
```

> SQS tarda hasta 60 segundos en reflejar el purge. Detener el consumidor con `Ctrl+C` en su terminal.

### 10.7 Equivalente en AWS real (Dev/QA)

```powershell
# Obtener las URLs (o usar el output ColaDlqUrl del stack CDK)
aws sqs get-queue-url --queue-name ccuc-novedades-prod.fifo
aws sqs get-queue-url --queue-name ccuc-novedades-dlq.fifo

# Conteo
aws sqs get-queue-attributes --queue-url <DLQ_URL> --attribute-names ApproximateNumberOfMessages

# Inspeccionar sin consumir
aws sqs receive-message --queue-url <DLQ_URL> --max-number-of-messages 10 --attribute-names All --message-attribute-names All --visibility-timeout 0
```

### 10.8 Diagnóstico rápido

| Síntoma | Causa probable | Acción |
|---|---|---|
| El mensaje inválido sigue en la cola principal | El consumidor no está corriendo o `DLQ_URL`/`DLQ_NAME` no resuelven. | Ver logs y `docker compose config`; revisar que `DLQ_URL` apunte a `ccuc-novedades-dlq.fifo`. |
| El mensaje no está ni en la cola principal ni en la DLQ | Fue procesado y borrado, o la deduplicación lo descartó. | Revisar logs y usar un `--message-deduplication-id` nuevo. |
| El segundo envío de prueba no aparece | Deduplicación FIFO (ventana de 5 minutos) por reutilizar el mismo `MessageDeduplicationId`. | Cambiar el ID de deduplicación en cada prueba. |
| `ApproximateNumberOfMessages` no cambia | Las métricas de SQS son aproximadas y pueden tardar unos segundos. | Esperar y repetir la consulta. |
| Error `The specified queue does not exist` | LocalStack aún no terminó el `init-aws.sh`. | Esperar el healthcheck y repetir `list-queues`. |
| Error al enviar a la DLQ | Falta permiso `sqs:SendMessage` sobre la DLQ (AWS). | Verificar `dlq.grant_send_messages(task_def.task_role)` en `infra/app.py`. |

