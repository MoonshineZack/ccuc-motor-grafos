"""Procesamiento de un mensaje SQS FIFO: validación, pipeline y confirmación.

Se separa del loop de ``main.py`` para poder probar la lógica de descarte a DLQ
sin levantar el consumidor ni depender de AWS.
"""
import json
import logging
from typing import Any, Dict, Optional, Set

from motor_reglas.dlq import mover_a_dlq
from motor_reglas.validacion import campos_faltantes

logger = logging.getLogger("ccuc.consumer")


def procesar_mensaje(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    sqs: Any,
    queue_url: str,
    dlq_url: Optional[str],
    mensaje: Dict[str, Any],
    orchestrator: Any,
    seen: Optional[Set[str]] = None,
) -> str:
    """Procesa un mensaje de la cola FIFO.

    Orden de decisión:
      1. JSON inválido            -> DLQ (error permanente, no reintentable).
      2. Estructura incompleta    -> DLQ (contrato de la Capa Media).
      3. Pipeline OK              -> ``delete_message`` (confirmación).
      4. Error transitorio        -> NO se borra: queda para reintento / redrive.

    Retorna uno de: ``procesado``, ``duplicado``, ``dlq`` o ``reintento``.
    """
    msg_id = mensaje.get("MessageId", "N/A")

    try:
        payload = json.loads(mensaje["Body"])
    except (KeyError, TypeError, json.JSONDecodeError) as err:
        logger.error(f"Mensaje {msg_id} con JSON inválido: {err}. Enviando a DLQ.")
        mover_a_dlq(sqs, queue_url, dlq_url, mensaje, f"JSON_INVALIDO: {err}")
        return "dlq"

    faltantes = campos_faltantes(payload)
    if faltantes:
        motivo = (
            f"ESTRUCTURA_INVALIDA: faltan {len(faltantes)} campo(s): "
            + ", ".join(faltantes)
        )
        logger.error(f"Mensaje {msg_id} rechazado ({motivo}). Enviando a DLQ.")
        mover_a_dlq(sqs, queue_url, dlq_url, mensaje, motivo)
        return "dlq"

    try:
        # Ejecutar el flujo de orquestación de 4 pasos.
        resultado = orchestrator.procesar_novedad(payload, seen_cache=seen)
        logger.info(f"Resultado procesamiento msg {msg_id}: {resultado}")

        # Solo se confirma (delete) si la tríada quedó persistida en el Data
        # Lake. Un error de egress deja el mensaje en la cola.
        if resultado.get("status") not in {"procesado", "duplicado"}:
            raise RuntimeError(f"Procesamiento no confirmado: {resultado}")

        sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=mensaje["ReceiptHandle"])
        logger.info(f"Mensaje {msg_id} eliminado exitosamente de SQS FIFO.")
        return resultado.get("status", "procesado")

    except Exception as exc:  # noqa: BLE001 - el mensaje no se borra para reintento
        logger.error(f"Error procesando novedad {msg_id}: {exc}", exc_info=True)
        # El mensaje queda invisible hasta el timeout y redrive hacia DLQ tras
        # max_receive_count.
        return "reintento"
