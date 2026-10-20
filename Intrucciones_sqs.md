# Simulación CCUC: SQS y Lambda (Egress Orchestrator)

Este proyecto cuenta con dos scripts para simular el inicio y el final del flujo marcado en la arquitectura:

## 1. Simulación de Ingreso (Capa Media -> SQS)
En el script `scripts/simulate_sqs_messages.py` he agregado una amplia variedad de casos:
- **Casos de éxito y Fuzzy Matching:** Mensajes con atributos de calidad (`email`, `direccion`, `telefono`) que tu motor RapidFuzz puede utilizar.
- **Casos de error intencionales:** Mensajes con documentos nulos, datos faltantes y tipos de datos erróneos. Estos son vitales para probar que tu Motor de Reglas (`Amazon ECS`) o tu manejador de errores funcionen, y que los mensajes caigan a la DLQ (Dead Letter Queue) si no pueden procesarse.

### ¿Cómo probarlo?
En una terminal (con tu entorno virtual activado):
```bash
python scripts/simulate_sqs_messages.py
```

## 2. Simulación de Salida (Motor de Reglas -> Lambda -> S3 Data Lake)
El handler de la AWS Lambda está en `src/lambda_egress/lambda_function.py` y se
despliega automáticamente en LocalStack como `ccuc-egress-orchestrator`.

Según la arquitectura, el **Egress Orchestrator** recibe el `Payload Triada` en el
**Paso 4** y se encarga de escribir la *triada resuelta* (< 10 min) hacia el bucket
S3 (Zona de salida - Data Lake).

### Características de la Lambda:
- En local se conecta a LocalStack (`ENDPOINT_URL=http://dev-localstack:4566`);
  en AWS usa el rol IAM y el endpoint real de S3.
- Recibe el evento de envío de la tríada desde el Motor de Reglas (ECS) y también
  soporta eventos de API Gateway, EventBridge y SQS.
- Genera un archivo `.json` particionado por el `codigoCu` y un `timestamp`.
- Lo almacena en el bucket S3 (para este ejercicio, `ccuc-landing`).
- Si la escritura falla devuelve `statusCode 500`; el Motor de Reglas entonces
  **no** elimina el mensaje de SQS para que se reintente o caiga a la DLQ.

### ¿Cómo probar la Lambda localmente?

**Opción A (recomendada): flujo real por SQS y ECS.** Levanta el laboratorio,
arranca el consumidor y envía una novedad como se describe en
`ARQUITECTURA_Y_EJECUCION_ECS_FARGATE.md`. El Paso 4 invocará la Lambda real de
LocalStack y esta escribirá en S3.

**Opción B: invocación directa del handler** (con LocalStack encendido; el bloque
`__main__` apunta a `http://localhost:4566`):

```bash
python src/lambda_egress/lambda_function.py
```

**Opción C: invocar la Lambda desplegada en LocalStack** con el evento de la tríada:

```bash
docker exec -it dev-localstack awslocal lambda invoke \
  --function-name ccuc-egress-orchestrator \
  --payload '{"codigoCu":"CU-1001","codigoPvu":"PVU-1","golden_record_id":"GR-1","matches_found":true,"score_calidad":98.5,"datos_consolidados":{"nombre":"Juan Perez"}}' \
  /tmp/egress-response.json
```

> **Verificar en LocalStack:** Si tienes instalado el CLI local de AWS (`awslocal`), puedes validar que el archivo quedó en S3 usando:
> `awslocal s3 ls s3://ccuc-landing/triadas-resueltas/ --recursive`
o
> `docker exec -it dev-localstack awslocal s3 ls s3://ccuc-landing/triadas-resueltas/ --recursive`
Para vel el contenido: (En la parte del CU-101 para adelante lo que le sale de la consola copian y pegan)
> `docker exec -it dev-localstack awslocal s3 cp s3://ccuc-landing/triadas-resueltas/CU-1001/20261008130046_payload.json -`