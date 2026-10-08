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
He creado el handler de la AWS Lambda en `src/lambda_egress/lambda_function.py`.

Según la arquitectura, el **Egress Orchestrator** recibe el `Payload Triada` y se encarga de escribir la *triada resuelta* (< 10 min) hacia el bucket S3 (Zona de salida - Data Lake).

### Características de la Lambda simulada:
- Se conecta a LocalStack (`http://localhost:4566`).
- Obtiene el payload resultante (simulado).
- Genera un archivo `.json` particionado por el `codigoCu` y un `timestamp`.
- Lo almacena en el bucket S3 (que, para fines de este ejercicio, llamamos `ccuc-landing`).

### ¿Cómo probar la Lambda localmente?
No necesitas invocarla mediante HTTP o subcontratar un framework serverless por ahora. El propio archivo tiene una sección `__main__` con un test unitario incorporado que le envía un "Payload Triada" falso y lo escribe en el S3 de LocalStack.

En tu terminal ejecuta:
```bash
python src/lambda_egress/lambda_function.py
```

> **Verificar en LocalStack:** Si tienes instalado el CLI local de AWS (`awslocal`), puedes validar que el archivo quedó en S3 usando:
> `awslocal s3 ls s3://ccuc-landing/triadas-resueltas/ --recursive`
o
> `docker exec -it dev-localstack awslocal s3 ls s3://ccuc-landing/triadas-resueltas/ --recursive`
Para vel el contenido: (En la parte del CU-101 para adelante lo que le sale de la consola copian y pegan)
> `docker exec -it dev-localstack awslocal s3 cp s3://ccuc-landing/triadas-resueltas/CU-1001/20261008130046_payload.json -`