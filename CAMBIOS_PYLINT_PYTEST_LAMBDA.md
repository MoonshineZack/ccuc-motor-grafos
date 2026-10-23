# Cambios realizados: `pylint`, `pytest` y revisión de la Lambda

**Repositorio:** `ccuc-motor-grafos`  
**Fecha:** 2026-10-22  
**Estado:** cambios en el working tree, **sin commit ni push**.

---

## 1. Resumen ejecutivo

| Tema | Estado inicial | Estado final |
|---|---|---|
| `pytest` | Ya existía en `requirements.txt`, CI y `tests/` | Sin cambios funcionales; 33 pruebas pasan |
| `pylint` | **No existía** (ni dependencia, ni configuración, ni CI) | Dependencia, `.pylintrc`, job de CI y documentación; score **10.00/10** |
| Lambda Egress | Infraestructura CDK correcta, con brechas de idempotencia y manejo de errores | Brechas de prioridad alta implementadas y probadas |
| Infraestructura Lambda | Sin log group explícito, memoria por defecto | Log group con retención, 256 MB, descripción y `LOG_LEVEL` |
| `cdk synth` | — | Exit 0, solo advertencias no bloqueantes |

### Últimos cambios incorporados en este documento

- [x] Clave S3 determinística por **SHA-256 del payload** (idempotencia).
- [x] El handler **relanza excepciones** para que Lambda reporte `FunctionError`.
- [x] Pruebas nuevas para idempotencia y propagación de errores (`33 passed`).
- [x] Documentación actualizada (`README.md`, arquitectura e instrucciones SQS).
- [x] Validación final: `pylint` 10.00/10, `pytest` 33 passed y `cdk synth` exit 0.

> Este documento incluye tanto los cambios de tooling (`pylint`/`pytest`) y la
> revisión de la Lambda, como la implementación posterior de las dos brechas de
> prioridad alta.

---

## 2. Validación inicial

### 2.1 `pytest`

Ya estaba integrado correctamente:

- `requirements.txt`: `pytest>=8.0.0`
- `.github/workflows/ci.yml`: job `Pruebas críticas` con `pytest tests/ -v`
- `tests/conftest.py`: agrega `src/` al `PYTHONPATH`
- 29 pruebas existentes pasaban antes de los cambios.

### 2.2 `pylint`

No estaba presente en el proyecto:

- No estaba en `requirements.txt`.
- No existía `.pylintrc` ni configuración equivalente.
- No había paso de lint en CI.
- No estaba instalado en el entorno local (`No module named pylint`).

### 2.3 Revisión de la Lambda

La Lambda `ccuc-egress-orchestrator` ya tenía infraestructura y contrato de evento, pero se identificaron dos brechas de prioridad alta:

1. **Idempotencia:** la clave S3 usaba un timestamp, por lo que un reintento del mismo payload generaba objetos duplicados.
2. **Manejo de errores:** el handler capturaba las excepciones y devolvía `statusCode: 500` en el body. Para ECS funcionaba, pero para SQS/EventBridge/API Gateway la invocación podía interpretarse como exitosa.

---

## 3. Cambios de tooling: `pylint` y `pytest`

### 3.1 `requirements.txt`

Se agregó `pylint` como dependencia de desarrollo:

```diff
 boto3>=1.34.0
 pytest>=8.0.0
+pylint>=4.0.0
 rapidfuzz>=3.8.0
```

> `pylint` no se empaqueta en la Lambda: el runtime solo usa `boto3` incluido por AWS.

### 3.2 `.pylintrc` (nuevo)

Se creó una configuración acorde al proyecto:

- Python objetivo: **3.12** (runtime de la Lambda y de la imagen ECS).
- Longitud máxima de línea: **120**.
- Se ignoran artefactos: `.venv`, `cdk.out`, `node_modules`, `__pycache__`, `.pytest_cache`.
- Se desactivan solo reglas de estilo que no aportan valor en este repositorio:
  `broad-exception-caught`, `duplicate-code`, `import-outside-toplevel`,
  `logging-fstring-interpolation`, `missing-*-docstring`, `too-few-public-methods`,
  `too-many-*`.
- Los errores reales (imports inexistentes, variables no usadas, etc.) siguen activos.

### 3.3 CI: nuevo job de lint

En `.github/workflows/ci.yml` se agregó el job `Análisis estático (pylint)`:

```yaml
lint:
  name: Análisis estático (pylint)
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-python@v5
      with:
        python-version: "3.14"
    - uses: actions/setup-node@v4
      with:
        node-version: "22"
    - name: Instalar dependencias
      run: pip install -r requirements.txt -r infra/requirements.txt
    - name: pylint
      run: pylint src infra/app.py
```

El job `deploy` ahora depende de `lint`, `test` y `synth`:

```diff
- needs: [test, synth]
+ needs: [lint, test, synth]
```

### 3.4 Correcciones reales detectadas por `pylint`

| Archivo | Corrección |
|---|---|
| `src/motor_reglas/core.py` | Eliminados imports no usados `Dict`, `Any`, `Set` |
| `src/motor_reglas/main.py` | Eliminados imports no usados `hash_payload`, `process_novedad`; orden de imports y argumentos no usados |
| `infra/app.py` | Eliminado import no usado `aws_applicationautoscaling` |
| `src/motor_reglas/orchestrator.py` | Orden de imports y line endings mixtos |
| `src/motor_reglas/neptune_client.py` | Eliminado `else` después de `return` |
| Varios (`src/`, `tests/`, `scripts/`) | Espacios finales y line endings normalizados |

### 3.5 Documentación

En `README.md`:

- Sección renombrada a **“Pruebas y análisis estático”**.
- Comandos:

```bash
python -m pytest tests/ -v
python -m pylint src infra/app.py
```

- Se documentó `.pylintrc` y el check `Análisis estático (pylint)` en branch protection.
- Se agregó `.pylintrc` al árbol del repositorio.

---

## 4. Cambios en la infraestructura de la Lambda

Archivo: `infra/app.py`.

### 4.1 Antes

```python
egress_lambda = _lambda.Function(
    self,
    "EgressOrchestrator",
    function_name="ccuc-egress-orchestrator",
    runtime=_lambda.Runtime.PYTHON_3_12,
    handler="lambda_function.lambda_handler",
    code=_lambda.Code.from_asset(os.path.join(REPO_ROOT, "src", "lambda_egress")),
    timeout=Duration.seconds(60),
    environment={
        "DATALAKE_BUCKET": landing_bucket.bucket_name,
    },
)
```

### 4.2 Después

```python
egress_logs = logs.LogGroup(
    self,
    "EgressOrchestratorLogs",
    log_group_name="/aws/lambda/ccuc-egress-orchestrator",
    retention=logs.RetentionDays.ONE_WEEK,
    removal_policy=RemovalPolicy.DESTROY,
)
egress_lambda = _lambda.Function(
    self,
    "EgressOrchestrator",
    function_name="ccuc-egress-orchestrator",
    description="Paso 4: persiste el Payload Tríada en el Data Lake (S3).",
    runtime=_lambda.Runtime.PYTHON_3_12,
    handler="lambda_function.lambda_handler",
    code=_lambda.Code.from_asset(os.path.join(REPO_ROOT, "src", "lambda_egress")),
    timeout=Duration.seconds(60),
    memory_size=256,
    log_group=egress_logs,
    environment={
        "DATALAKE_BUCKET": landing_bucket.bucket_name,
        "LOG_LEVEL": "INFO",
    },
)
landing_bucket.grant_write(egress_lambda)
```

### 4.3 Configuración que ya estaba correcta

| Aspecto | Implementación |
|---|---|
| Nombre | `ccuc-egress-orchestrator` |
| Runtime / handler | `PYTHON_3_12` / `lambda_function.lambda_handler` |
| Código | Asset `src/lambda_egress` |
| Timeout | 60 s |
| Permiso S3 | `landing_bucket.grant_write(egress_lambda)` |
| Permiso de invocación | `egress_lambda.grant_invoke(task_def.task_role)` |
| Variable en ECS | `LAMBDA_EGRESS_NAME` |
| Tipo de invocación | `RequestResponse` desde ECS (Paso 4) |
| Contrato de evento | Directo, API Gateway (`body`), EventBridge (`detail`), SQS (`Records`) |

La Lambda **no** tiene event source mapping de SQS de forma intencional: ECS es el único consumidor de la cola y evita consumidores competidores.

---

## 5. Brechas de prioridad alta implementadas

Archivo principal: `src/lambda_egress/lambda_function.py`.

### 5.1 Idempotencia de la clave S3

#### Antes

```python
def _build_s3_key(codigo_cu: Any) -> str:
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S%f")
    return f"{PREFIX}/{_safe_cu(codigo_cu)}/{timestamp}_payload.json"
```

Un reintento del mismo payload generaba una clave distinta y, por tanto, un objeto duplicado.

#### Después

```python
def _payload_hash(payload: Dict[str, Any]) -> str:
    """Hash determinístico del payload (clave de idempotencia en S3)."""
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _build_s3_key(codigo_cu: Any, payload: Dict[str, Any]) -> str:
    """Clave S3 determinística: reintentos del mismo payload se sobrescriben."""
    return f"{PREFIX}/{_safe_cu(codigo_cu)}/{_payload_hash(payload)}_payload.json"
```

Nueva ruta:

```text
s3://ccuc-landing/triadas-resueltas/{codigoCu}/{sha256(payload)}_payload.json
```

Ventajas:

- El mismo contenido produce siempre la misma clave, sin importar el orden de las claves JSON.
- Un reintento sobrescribe el mismo objeto; no duplica archivos.
- No requiere estado adicional ni una tabla de idempotencia.

#### Pruebas agregadas

En `tests/test_lambda_egress.py`:

- `test_clave_s3_es_deterministica_por_hash`
- `test_reintento_con_mismo_payload_no_duplica_objeto`
- `test_payloads_distintos_generan_claves_distintas`

### 5.2 Propagación de errores del handler

#### Antes

```python
except Exception as exc:
    logger.exception("Error procesando la tríada en Lambda Egress: %s", exc)
    return {
        "statusCode": 500,
        "body": json.dumps({"error": str(exc)}, ensure_ascii=False),
    }
```

Problema: Lambda devolvía una invocación exitosa (HTTP 200) con un `statusCode: 500` en el body. ECS lo validaba, pero SQS/EventBridge/API Gateway podían interpretar el evento como procesado correctamente.

#### Después

```python
except Exception as exc:  # noqa: BLE001 - se relanza para que Lambda marque FunctionError
    logger.exception("Error procesando la tríada en Lambda Egress: %s", exc)
    raise
```

Consecuencias:

- Lambda reporta `FunctionError`.
- CloudWatch registra el error y la métrica `Errors`.
- ECS detecta `FunctionError` en `_leer_respuesta_lambda`, no confirma el mensaje SQS y permite reintento/DLQ.
- El fallback local del orquestador también recibe la excepción y la convierte en `TriadaEgressError`.

#### Pruebas agregadas/actualizadas

En `tests/test_lambda_egress.py`:

- `test_sin_codigocu_propaga_error` (antes esperaba un `statusCode: 500` en el body).

En `tests/test_orchestrator.py`:

- `test_lambda_egress_fallida_propaga_error_y_no_confirma` ahora simula un handler que lanza excepción.
- `test_respuesta_lambda_con_function_error_se_rechaza` valida que `FunctionError` se rechace aunque el HTTP sea 200.

### 5.3 Efecto sobre el flujo ECS → Lambda

```text
ECS invoca Lambda (RequestResponse)
  ├─ Handler OK        → ECS valida statusCode 200 → elimina mensaje SQS
  └─ Handler falla     → Lambda FunctionError
                         → ECS lanza TriadaEgressError
                         → NO elimina mensaje SQS
                         → reintento / DLQ
```

---

## 6. Documentación actualizada

| Archivo | Cambio |
|---|---|
| `README.md` | Comandos de `pytest`/`pylint`, job de CI, branch protection, clave S3 idempotente y `FunctionError` |
| `ARQUITECTURA_Y_EJECUCION_ECS_FARGATE.md` | Ruta S3 con `sha256(payload)`, idempotencia y propagación de errores |
| `Intrucciones_sqs.md` | Archivo particionado por hash, no por timestamp; error reportado como `FunctionError` |
| `.github/workflows/ci.yml` | Job `lint` y dependencia del deploy |
| `.pylintrc` | Configuración nueva de análisis estático |

---

## 7. Validaciones ejecutadas

```text
python -m pylint src infra/app.py
→ Your code has been rated at 10.00/10

python -m pytest tests/ -q
→ 33 passed in 22.28s

npx aws-cdk@2.1128.1 synth
→ EXIT=0
```

### Advertencias de `cdk synth`

No bloquean el despliegue:

1. `ServerlessScalingConfiguration.MinCapacity: 0.5` está por debajo del mínimo validado por CloudFormation para Neptune.
2. `minHealthyPercent` de ECS usa el valor por defecto del 50 %.

---

## 8. Archivos modificados

### Tooling y documentación

- `requirements.txt`
- `.pylintrc` *(nuevo)*
- `.github/workflows/ci.yml`
- `README.md`
- `ARQUITECTURA_Y_EJECUCION_ECS_FARGATE.md`
- `Intrucciones_sqs.md`
- `CAMBIOS_PYLINT_PYTEST_LAMBDA.md` *(este documento)*

### Infraestructura

- `infra/app.py`

### Lambda y motor

- `src/lambda_egress/lambda_function.py`
- `src/motor_reglas/core.py`
- `src/motor_reglas/main.py`
- `src/motor_reglas/neptune_client.py`
- `src/motor_reglas/orchestrator.py`

### Pruebas y limpieza

- `tests/test_lambda_egress.py`
- `tests/test_orchestrator.py`
- `tests/test_end_to_end_pipeline.py` *(espacios finales)*
- `tests/test_neptune_client.py` *(espacios finales)*
- `src/motor_calidad/fuzzy_engine.py` *(espacios finales)*
- `src/motor_calidad/normalizer.py` *(espacios finales)*
- `scripts/simulate_sqs_messages*.py` *(espacios finales)*

---

## 9. Recomendaciones pendientes (prioridad media/baja)

| # | Prioridad | Recomendación |
|---|---|---|
| 1 | Media | Publicar `Version` + `Alias` y apuntar ECS al alias en lugar de `$LATEST`. |
| 2 | Media | No loguear el payload completo en INFO (contiene datos personales); loguear `codigoCu`, `s3_path` y `requestId`. |
| 3 | Media | Agregar DLQ/`on_failure` y reintentos si se incorporan triggers asíncronos (EventBridge/SQS). |
| 4 | Baja | Agregar alarmas de `Errors`/`Throttles` y tracing X-Ray. |
| 5 | Baja | Validar el contrato de la tríada con JSON Schema o Pydantic. |
| 6 | Baja | Evaluar cifrado y versionado del bucket `ccuc-landing`. |
| 7 | Baja | Si el bucket se restringe a VPC, mover la Lambda a VPC o usar VPC endpoint para S3. |

---

## 10. Cómo reproducir las validaciones

```bash
# Dependencias
pip install -r requirements.txt -r infra/requirements.txt

# Análisis estático
python -m pylint src infra/app.py

# Pruebas
python -m pytest tests/ -v

# Infraestructura
cd infra
npx aws-cdk@2.1128.1 synth
```

---

## 11. Notas

- Todos los cambios están **sin commit**.
- El laboratorio LocalStack no está levantado en este momento.
- La prueba end-to-end contra LocalStack no se repitió en esta iteración; la validación se hizo con `pytest`, `pylint` y `cdk synth`.
- Para probar el flujo real: `docker compose up -d`, enviar un mensaje SQS y verificar la ruta S3 con hash.
