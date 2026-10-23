"""Servicio ECS Fargate: Consumidor de novedades SQS FIFO y Orquestador de Reglas CCUC."""
import logging
import os
import signal
import sys
import time

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# pylint: disable=wrong-import-position
from motor_reglas.consumer import procesar_mensaje  # noqa: E402
from motor_reglas.orchestrator import CCUCOrchestrator  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [ECS-Fargate] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("ccuc.main")

running = True  # pylint: disable=invalid-name


def handle_shutdown(signum, _frame):
    """Manejo de señales del SO (SIGTERM/SIGINT) para apagado graceful en ECS Fargate."""
    global running  # pylint: disable=global-statement
    logger.info(f"Señal de apagado recibida ({signum}). Finalizando tareas en curso...")
    running = False


signal.signal(signal.SIGINT, handle_shutdown)
signal.signal(signal.SIGTERM, handle_shutdown)


def main():
    endpoint = os.environ.get("ENDPOINT_URL") or None
    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")

    sqs = boto3.client("sqs", endpoint_url=endpoint, region_name=region)

    queue_url = os.environ.get("QUEUE_URL")
    if not queue_url:
        queue_name = os.environ.get("QUEUE_NAME", "ccuc-novedades-prod.fifo")
        try:
            queue_url = sqs.get_queue_url(QueueName=queue_name)["QueueUrl"]
        except ClientError as e:
            logger.error(f"Error obteniendo URL de cola {queue_name}: {e}")
            return

    logger.info(f"Iniciando consumidor ECS Fargate sobre cola: {queue_url}")

    # DLQ explícita: los mensajes con estructura inválida se descartan de
    # inmediato en lugar de esperar maxReceiveCount reintentos.
    dlq_url = os.environ.get("DLQ_URL")
    if not dlq_url:
        dlq_name = os.environ.get("DLQ_NAME", "ccuc-novedades-dlq.fifo")
        try:
            dlq_url = sqs.get_queue_url(QueueName=dlq_name)["QueueUrl"]
        except ClientError as e:
            logger.warning(
                f"No se pudo resolver la DLQ {dlq_name}: {e}. "
                "Los mensajes inválidos se moverán por redrive policy."
            )
            dlq_url = None
    if dlq_url:
        logger.info(f"DLQ configurada: {dlq_url}")

    # Inicializar orquestador central
    orchestrator = CCUCOrchestrator()

    # Deduplicación local en memoria (en escalado > 1 tarea, complementado con ElastiCache / DynamoDB)
    seen: set = set()

    wait_time = int(os.environ.get("SQS_WAIT_TIME_SECONDS", "20"))
    max_messages = int(os.environ.get("SQS_MAX_MESSAGES", "10"))
    visibility_timeout = int(os.environ.get("SQS_VISIBILITY_TIMEOUT", "300"))

    while running:
        try:
            # Paso 1: Long Polling desde SQS FIFO
            response = sqs.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=max_messages,
                WaitTimeSeconds=wait_time,
                VisibilityTimeout=visibility_timeout,
                AttributeNames=["All"],
                MessageAttributeNames=["All"]
            )

            messages = response.get("Messages", [])
            if not messages:
                continue

            for msg in messages:
                # Validación de estructura + pipeline + confirmación/DLQ.
                # Los mensajes con claves faltantes nunca llegan al orquestador.
                procesar_mensaje(sqs, queue_url, dlq_url, msg, orchestrator, seen)

        except ClientError as e:
            logger.error(f"Error de cliente AWS SQS: {e}")
            time.sleep(2)
        except Exception as e:
            logger.error(f"Error inesperado en loop principal: {e}")
            time.sleep(2)

    logger.info("Consumidor ECS Fargate detenido correctamente.")


if __name__ == "__main__":
    main()
