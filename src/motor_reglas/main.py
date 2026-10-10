"""Servicio ECS Fargate: Consumidor de novedades SQS FIFO y Orquestador de Reglas CCUC."""
import json
import logging
import os
import signal
import sys
import time

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from motor_reglas.core import hash_payload, process_novedad  # noqa: E402
from motor_reglas.orchestrator import CCUCOrchestrator  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [ECS-Fargate] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("ccuc.main")

running = True


def handle_shutdown(signum, frame):
    """Manejo de señales del SO (SIGTERM/SIGINT) para apagado graceful en ECS Fargate."""
    global running
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
                receipt_handle = msg["ReceiptHandle"]
                msg_id = msg.get("MessageId", "N/A")
                
                try:
                    payload = json.loads(msg["Body"])
                except json.JSONDecodeError as err:
                    logger.error(f"Mensaje {msg_id} con JSON inválido: {err}. Descartando a DLQ.")
                    continue

                try:
                    # Ejecutar el flujo de orquestación de 4 pasos
                    resultado = orchestrator.procesar_novedad(payload, seen_cache=seen)
                    logger.info(f"Resultado procesamiento msg {msg_id}: {resultado}")

                    # Eliminar de la cola una vez procesado exitosamente
                    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
                    logger.info(f"Mensaje {msg_id} eliminado exitosamente de SQS FIFO.")

                except Exception as e:
                    logger.error(f"Error procesando novedad {msg_id}: {e}", exc_info=True)
                    # El mensaje queda invisible hasta timeout y redrive hacia DLQ tras max_receive_count

        except ClientError as e:
            logger.error(f"Error de cliente AWS SQS: {e}")
            time.sleep(2)
        except Exception as e:
            logger.error(f"Error inesperado en loop principal: {e}")
            time.sleep(2)

    logger.info("Consumidor ECS Fargate detenido correctamente.")


if __name__ == "__main__":
    main()
