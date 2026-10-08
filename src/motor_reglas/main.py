"""Consumidor de novedades desde la cola SQS FIFO."""
import json
import os
import sys

import boto3

sys.path.insert(0, os.path.dirname(__file__))
from core import process_novedad  # noqa: E402


def main():
    endpoint = os.environ.get("ENDPOINT_URL") or None
    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    sqs = boto3.client("sqs", endpoint_url=endpoint, region_name=region)

    queue_url = os.environ.get("QUEUE_URL")
    if not queue_url:
        queue_url = sqs.get_queue_url(
            QueueName=os.environ.get("QUEUE_NAME", "ccuc-novedades-prod.fifo")
        )["QueueUrl"]

    # ponytail: dedup en memoria, aguanta 1 tarea; si sube a >1 tarea, persistir seen en DynamoDB
    seen: set = set()
    print(f"[motor-reglas] escuchando {queue_url}", flush=True)

    while True:
        resp = sqs.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=10,
            WaitTimeSeconds=10,
            VisibilityTimeout=30,
        )
        for msg in resp.get("Messages", []):
            payload = json.loads(msg["Body"])
            resultado = process_novedad(
                payload, seen, touch=lambda p: print(f"[motor-reglas] procesando {p}", flush=True)
            )
            print(f"[motor-reglas] {resultado}", flush=True)
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=msg["ReceiptHandle"])


if __name__ == "__main__":
    main()
