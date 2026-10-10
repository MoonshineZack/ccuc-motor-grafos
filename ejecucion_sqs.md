### Simulación de SQS Local para CCUC
He creado un script en Python llamado **scripts/simulate_sqs_messages.py** que te permitirá enviar datos dummy a la cola de LocalStack.

Según la arquitectura que compartiste:

    1. Tienes una cola Amazon SQS FIFO.
    2. El agrupador lógico es **MessageGroupId = codigoCu**.
    3. Recibe un payload en formato JSON desde la Capa Media.

### Cómo ejecutar la simulación
1. Iniciar los servicios locales (Docker)
Debes levantar el entorno que incluye LocalStack (que emula AWS SQS, S3, etc.) y tus servicios (Motor de reglas, Gremlin, Opensearch). Abre una terminal en la raíz del proyecto y ejecuta:

```
docker-compose up -d
```

## NOTE

Asegúrate de tener Docker Desktop corriendo en tu equipo. Al levantar **localstack**, este ejecutará automáticamente el script **scripts/init-aws.sh** que se encarga de crear la cola **ccuc-novedades-prod.fifo** (tal cual requiere la arquitectura).

2. Ejecutar el script generador de datos
Una vez los contenedores estén arriba, puedes enviar mensajes abriendo una nueva terminal en el repositorio:

```
# 1. Crear entorno virtual (si no lo has hecho)
python -m venv venv

# 2. Activar entorno virtual
# En Windows (Powershell):
.\venv\Scripts\Activate.ps1
# En Linux/Mac o Git Bash:
# source venv/bin/activate

# 3. Instalar dependencias necesarias
pip install boto3

# 4. Ejecutar el script
python scripts/simulate_sqs_messages.py
```

## ¿Qué hace el script?
El script apuntará directamente a **http://localhost:4566** (LocalStack). Toma una lista de diccionarios (datos dummy de clientes) y los envía a la cola FIFO usando **codigoCu** como el **MessageGroupId** y generando un ID único de deduplicación.

3. Verificar que los mensajes se están procesando
Si observas los logs del servicio del Motor de Reglas **(dev-motor-reglas)**, deberías ver que este detecta los mensajes enviados a la cola:

```
docker-compose logs -f dev-motor-reglas
```