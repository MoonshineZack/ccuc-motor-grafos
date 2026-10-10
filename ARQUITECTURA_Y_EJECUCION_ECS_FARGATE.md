# Arquitectura y Guía de Ejecución: Motor de Reglas y Orquestación en Amazon ECS Fargate

| Proyecto | Motor de Grafos CCUC (Código Cliente Único Comercial) |
|---|---|
| **Componente Central** | Amazon ECS (Fargate) — Motor de Reglas, Calidad y Orquestación |
| **Versiones Base** | Python **3.12.9** \| AWS CDK **2.1128.1** |
| **Región AWS** | us-east-1 |

---

## 1. Visión General del Flujo de Datos

El componente en **Amazon ECS Fargate** actúa como el núcleo orquestador del proyecto CCUC. Procesa las novedades enviadas desde los canales digitales y sistemas MDM a través de la **Capa Media**, aplicando reglas de negocio, calidad difusa (*Fuzzy Matching*) y modelado de grafos en Amazon Neptune antes de emitir la tríada resuelta hacia el Data Lake.

```
       Capa Media (Middleware)
       ├── Copia 1:1 en paralelo ──────────────► Amazon S3 (Evidencia de ingesta)
       └── 1 mensaje por novedad ──────────────► Amazon SQS FIFO (MessageGroupId = codigoCu)
                                                         │
                                                         │ 1. Long polling (ReceiveMessage)
                                                         ▼
                                             ┌────────────────────────────────────────┐
                                             │       Amazon ECS Fargate               │
                                             │   (Motor de Reglas & Orquestación)     │
                                             └───────┬────────────────────────▲───────┘
                                                     │ 2. Invoca              │ Retorna
                                                     │    Fuzzy Matching      │ CU/PVU Depurado
                                                     ▼                        │
                                             ┌────────────────────────────────┴───────┐
                                             │ Motor de Calidad CU/PVU (Python/RapidFuzz)
                                             │ • Blocking: búsqueda exacta documento  │
                                             │ • Normalización direcciones y H3       │
                                             └────────────────────────────────────────┘
                                                     │
                                                     │ 3. Gremlin API (upsert vertices/aristas)
                                                     ▼
                                             ┌────────────────────────────────────────┐
                                             │    Amazon Neptune Serverless           │
                                             │      (Motor de Grafos CCUC)            │
                                             └───────────────────┬────────────────────┘
                                                                 │ 4. Payload Tríada
                                                                 ▼
                                             ┌────────────────────────────────────────┐
                                             │ AWS Lambda (Egress Orchestrator)       │
                                             │ ──► Amazon S3 Data Lake (Tríada resuelta)
                                             └────────────────────────────────────────┘
```

---

## 2. Lo que se Desarrolló en ECS Fargate

### Paso 1: Ingesta e Idempotencia Estricta (`src/motor_reglas/main.py` y `core.py`)
* **Long Polling SQS:** Consumo continuo sobre la cola `ccuc-novedades-prod.fifo` utilizando `WaitTimeSeconds=20` y `VisibilityTimeout=300`.
* **Deduplicación por `hashPayload`:** Antes de consultar servicios de red, se calcula una huella SHA-256 canónica del JSON recibido. Si el mensaje ya fue procesado, se descarta inmediatamente, evitando costos y operaciones innecesarias.
* **Control de Errores y DLQ:** Los mensajes con errores estructurales (ej. JSON malformado o documentos inválidos) son manejados sin bloquear el hilo principal, permitiendo que tras los reintentos pasen a la cola de mensajes no procesados (DLQ).

### Paso 2: Motor de Calidad CU/PVU con RapidFuzz (`src/motor_calidad/`)
* **Normalización de Identificación:** Limpieza de cédulas y NITs eliminando caracteres especiales, espacios y guiones (`normalize_document`).
* **Personas Naturales vs. Jurídicas:**
  * Si viene `razonSocial`, limpia sufijos societarios colombianos (`S.A.S.`, `S.A.`, `LTDA`, etc.).
  * Si viene persona natural, unifica y limpia `primerNombre`, `segundoNombre`, `primerApellido` y `segundoApellido`.
* **Normalización de Direcciones:** Estandarización de vías urbanas colombianas (`CALLE` $\rightarrow$ `CL`, `CARRERA` $\rightarrow$ `CR`, `AVENIDA` $\rightarrow$ `AV`, `#`, `APTO`, `BODEGA`).
* **Georreferenciación H3:** Asignación de celda H3 (resolución 9) para agrupación espacial de PVUs.
* **Fuzzy Matching y Bloqueo (*Blocking*):**
  * **Regla CU-RE:** *Documento distinto $\rightarrow$ cliente distinto*. Bloqueo estricto por `documentoNormalizado`.
  * **RapidFuzz:** Compara únicamente contra candidatos bloqueados usando `rapidfuzz.fuzz.token_set_ratio` y `ratio` para determinar score de calidad (0 a 100%).
  * Genera el dataset `CUDepurado` y `PVUDepurado`.

### Paso 3: Operaciones de Grafo con Gremlin API (`src/motor_reglas/neptune_client.py`)
> **Nota técnica:** En el diagrama arquitectónico se utiliza la **Gremlin API** (Apache TinkerPop sobre WebSocket) para interactuar con Amazon Neptune Serverless.
* **Vértice Cliente:** Crea o actualiza el nodo `Cliente` con `codigoCu`, `documentoNormalizado`, `nombreCompleto`, `tipoPersona` y `estado`.
* **Vértice PVU:** Crea o actualiza el nodo `PVU` con `codigoPvu`, `direccionNormalizada`, `codigoCiudad` y `h3Index`.
* **Arista `:TIENE_PVU`:** Vincula el cliente con su punto de venta.
* **Tabla de Verdad Crítica (`:INTERSECTA`):**
  * Si el cliente está **Activo**, el motor evalúa relaciones y crea la arista `:INTERSECTA`.
  * Si el cliente está **Inactivo (`"I"` o `"Inactivo"`)**, la regla de negocio **bloquea** la creación de la arista `:INTERSECTA`.

### Paso 4: Emisión de Tríada Resuelta (`src/motor_reglas/orchestrator.py` y `src/lambda_egress/`)
* **Construcción del Payload Tríada:** Consolida los identificadores resueltos (`codigoCu`, `codigoPvu`, `golden_record_id`), atributos limpios, score de calidad y metadatos del grafo.
* **Invocación a Lambda Egress:** Envía la tríada a la Lambda `ccuc-egress-orchestrator`, la cual escribe el archivo en el bucket S3:
  ```
  s3://ccuc-landing/triadas-resueltas/{codigoCu}/{timestamp}_payload.json
  ```
* **Confirmación SQS (`delete_message`):** Una vez confirmada la persistencia, se elimina el mensaje de la cola FIFO para garantizar la entrega única.

---

## 3. Infraestructura como Código (AWS CDK) y Reglas IAM (`infra/app.py`)

Se actualizaron los recursos de CDK v2 (versión `2.1128.1`) y las políticas de seguridad:

1. **IAM Task Role de ECS (`MotorReglasTask`):**
   * `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:GetQueueAttributes` sobre la cola FIFO y DLQ.
   * `neptune-db:connect`, `neptune-db:ReadDataViaQuery`, `neptune-db:WriteDataViaQuery` para consultas Gremlin sobre Neptune.
   * `lambda:InvokeFunction` para invocar la función `ccuc-egress-orchestrator`.
   * `cloudwatch:PutMetricData` sobre el espacio de nombres `CCUC/MotorReglas` para publicar la métrica `BacklogPerTask`.
2. **Application Auto Scaling en Fargate:**
   * Target Tracking sobre la métrica personalizada `BacklogPerTask` (profundidad de cola vs tareas activas).
   * **Mínimo:** 2 tareas \| **Máximo:** 20 tareas en capacidad Fargate Spot.
3. **Data Lake S3:**
   * Bucket de aterrizaje `ccuc-landing` con permisos concedidos a la Lambda de Egress.

---

## 4. Guía de Ejecución y Pruebas Locales (Paso a Paso)

### Requisitos Previos
1. Tener Docker Desktop encendido.
2. Levantar el laboratorio local desde la raíz del proyecto:
   ```powershell
   docker compose up -d
   ```
   *(Esto levanta LocalStack en el puerto 4566, Gremlin Server en el 8182 y OpenSearch en el 9200).*

---

### Paso A: Iniciar el Consumidor ECS Fargate (Terminal 1)

Ejecuta el consumidor dentro del contenedor Docker para que escuche la cola y aplique las reglas en caliente:

```powershell
docker exec -it dev-motor-reglas python /app/motor_reglas/main.py
```

*Verás en la consola:*
```text
[INFO] [ECS-Fargate] Iniciando consumidor ECS Fargate sobre cola: http://dev-localstack:4566/000000000000/ccuc-novedades-prod.fifo
```

---

### Paso B: Enviar Novedades de Prueba desde la Capa Media (Terminal 2)

Abre una **segunda terminal** en la raíz del proyecto, configura las variables de entorno de AWS para apuntar a LocalStack y ejecuta el script generador de datos:

```powershell
# 1. Configurar variables de entorno en PowerShell
$env:ENDPOINT_URL="http://localhost:4566"
$env:AWS_ACCESS_KEY_ID="test"
$env:AWS_SECRET_ACCESS_KEY="test"
$env:AWS_DEFAULT_REGION="us-east-1"

# 2. Enviar los mensajes simulados a la cola SQS FIFO
python scripts/simulate_sqs_messages.py
```

*Salida esperada en la Terminal 2:*
```text
Verificando/Creando la cola: ccuc-novedades-prod.fifo...
Cola lista.
Enviando mensajes a la cola: http://localhost:4566/000000000000/ccuc-novedades-prod.fifo
[*] Mensaje enviado exitosamente - documento: 1018273645 | MessageId: 7930e8fe...
[*] Mensaje enviado exitosamente - documento: 987654321  | MessageId: 6dfd62a0...
[*] Mensaje enviado exitosamente - documento: 900123456  | MessageId: dc2b43c7...
[*] Mensaje enviado exitosamente - documento: SIN_DOCUMENTO | MessageId: eb2a01ba...
```

---

### Paso C: Observar el Procesamiento en la Terminal 1 (Consumidor ECS)

Inmediatamente verás cómo el Motor de Reglas en ECS Fargate ejecuta los 4 pasos:

1. **`[Paso 1]`:** Recepción del mensaje y validación de hash de idempotencia (`hashPayload`).
2. **`[Paso 2]`:** Fuzzy Matching con RapidFuzz: normaliza documentos, vías colombianas y calcula el score de calidad (ej. `CU: CU-1018273645 | Calidad: 90.0%`).
3. **`[Paso 3]`:** Neptune Gremlin: crea vértices `Cliente`, `PVU`, relación `:TIENE_PVU` y evalúa la arista `:INTERSECTA` según el estado del cliente.
4. **`[Paso 4]`:** Genera el `Payload Tríada` y lo envía a la Lambda Egress para guardarlo en Amazon S3 (`ccuc-landing`).
5. **Confirmación:** Elimina el mensaje de SQS FIFO para completar la transacción.

---

### Paso D: Ejecutar las Pruebas Automatizadas (Pytest)

Para comprobar la integridad de todo el pipeline y las reglas de negocio críticas:

```powershell
python -m pytest tests/ -v
```

**Resultado:** 21 pruebas unitarias y de integración pasando al 100%:
* `test_end_to_end_pipeline.py`: Flujo completo desde ingesta hasta S3.
* `test_idempotency.py`: Descarte de duplicados por `hashPayload` (100%).
* `test_truth_table.py`: Cliente inactivo nunca genera la arista `:INTERSECTA`.
* `test_motor_calidad.py`: Normalizadores de nombres, direcciones y RapidFuzz.
* `test_neptune_client.py`: Consultas y mutaciones Gremlin.
* `test_orchestrator.py`: Orquestador de 4 pasos de ECS Fargate.
