"""Enrutamiento explícito de mensajes inválidos a la Dead Letter Queue FIFO.

El redrive policy de la cola principal mueve un mensaje a la DLQ recién después
de ``maxReceiveCount`` intentos. Para errores permanentes (JSON inválido o
estructura incompleta) eso es indeseable: bloquea el ``MessageGroupId`` de una
cola FIFO y gasta reintentos. Por eso el consumidor los mueve de inmediato.
"""
import logging
from typing import Any, Dict, Optional

from botocore.exceptions import ClientError

logger = logging.getLogger("ccuc.dlq")

MOTIVO_MAX_LEN = 1024
GRUPO_POR_DEFECTO = "SIN_GRUPO"


def construir_mensaje_dlq(mensaje: Dict[str, Any], motivo: str) -> Dict[str, Any]:
    """Construye los kwargs de ``send_message`` para reenviar un mensaje a la DLQ.

    En colas FIFO el ``MessageGroupId`` es obligatorio, por eso se conserva el
    del mensaje original. También se conserva el ``MessageDeduplicationId``
    para que un reintento del consumidor no duplique el registro en la DLQ.
    """
    atributos = mensaje.get("Attributes") or {}
    kwargs: Dict[str, Any] = {
        "MessageBody": mensaje.get("Body", ""),
        "MessageGroupId": atributos.get("MessageGroupId") or GRUPO_POR_DEFECTO,
        "MessageAttributes": {
            "motivoDlq": {
                "DataType": "String",
                "StringValue": str(motivo)[:MOTIVO_MAX_LEN],
            },
            "messageIdOrigen": {
                "DataType": "String",
                "StringValue": str(mensaje.get("MessageId", "N/A")),
            },
        },
    }
    dedup_id = atributos.get("MessageDeduplicationId")
    if dedup_id:
        kwargs["MessageDeduplicationId"] = dedup_id
    return kwargs


def enviar_a_dlq(
    sqs: Any,
    dlq_url: Optional[str],
    mensaje: Dict[str, Any],
    motivo: str,
) -> bool:
    """Reenvía ``mensaje`` a la DLQ. Retorna ``True`` solo si el envío fue exitoso."""
    msg_id = mensaje.get("MessageId", "N/A")
    if not dlq_url:
        logger.error(
            f"DLQ no configurada (DLQ_URL/DLQ_NAME): el mensaje {msg_id} no se puede descartar."
        )
        return False
    try:
        sqs.send_message(QueueUrl=dlq_url, **construir_mensaje_dlq(mensaje, motivo))
        return True
    except Exception as exc:  # noqa: BLE001 - cualquier fallo deja el mensaje para redrive
        logger.error(f"No se pudo enviar el mensaje {msg_id} a la DLQ: {exc}")
        return False


def mover_a_dlq(
    sqs: Any,
    queue_url: str,
    dlq_url: Optional[str],
    mensaje: Dict[str, Any],
    motivo: str,
) -> bool:
    """Envía el mensaje a la DLQ y lo elimina de la cola principal.

    Si el envío a la DLQ falla, el mensaje permanece en la cola principal y el
    redrive policy lo moverá tras ``maxReceiveCount``.
    """
    msg_id = mensaje.get("MessageId", "N/A")
    if not enviar_a_dlq(sqs, dlq_url, mensaje, motivo):
        logger.error(f"Mensaje {msg_id} permanece en la cola para redrive a DLQ ({motivo}).")
        return False

    receipt_handle = mensaje.get("ReceiptHandle")
    if not receipt_handle:
        logger.error(f"Mensaje {msg_id} sin ReceiptHandle; no se puede confirmar su borrado.")
        return False

    try:
        sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
    except ClientError as exc:
        logger.error(f"Mensaje {msg_id} enviado a DLQ pero no se pudo borrar de la cola: {exc}")
        return False

    logger.warning(f"Mensaje {msg_id} descartado a DLQ: {motivo}")
    return True
