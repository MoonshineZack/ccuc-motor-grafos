"""AWS Lambda Egress Orchestrator CCUC (Paso 4 de la arquitectura).

Recibe el *Payload Tríada* que envía el Motor de Reglas (ECS Fargate) y lo
persiste en el Data Lake (Amazon S3).

Contrato del evento
-------------------
La función acepta, de forma transparente, los formatos habituales con los que
puede llegar el "evento del envío":

* Invocación directa desde ECS: ``{"codigoCu": "...", ...}``
* API Gateway / proxy: ``{"body": "<json>"}``
* EventBridge: ``{"detail": {...}}``
* SQS: ``{"Records": [{"body": "<json>"}, ...]}``

El cliente de S3 se crea de forma perezosa y **solo** usa ``endpoint_url`` y
credenciales estáticas cuando se configura explícitamente ``ENDPOINT_URL``
(LocalStack). En AWS se usa el rol de ejecución de la Lambda (IAM), que es lo
correcto para producción.

Idempotencia
------------
La clave S3 se deriva del hash SHA-256 del payload (claves ordenadas), no de un
timestamp. Un reintento con el mismo contenido sobrescribe el mismo objeto en
lugar de duplicarlo.

Manejo de errores
-----------------
Si el procesamiento falla, la excepción se relanza para que Lambda reporte
``FunctionError`` y el origen del evento (ECS, SQS, EventBridge o API Gateway)
pueda reintentar o derivar a la DLQ.
"""
import hashlib
import json
import logging
import os
import re
from typing import Any, Dict, Iterator, List

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger("ccuc.lambda_egress")
logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())

TARGET_BUCKET = os.getenv("DATALAKE_BUCKET", "ccuc-landing")
PREFIX = "triadas-resueltas"

_s3_client = None  # pylint: disable=invalid-name


def _endpoint_url() -> str | None:
    """Endpoint emulado (LocalStack) o ``None`` para usar el endpoint real de AWS."""
    endpoint = os.getenv("ENDPOINT_URL") or os.getenv("AWS_ENDPOINT_URL") or ""
    return endpoint.strip() or None


def get_s3_client():
    """Crea (una sola vez) el cliente S3 según el entorno de ejecución."""
    global _s3_client  # pylint: disable=global-statement
    if _s3_client is not None:
        return _s3_client

    endpoint = _endpoint_url()
    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
    kwargs: Dict[str, Any] = {"region_name": region}

    if endpoint:
        # Solo en local/LocalStack se fuerza endpoint y credenciales de prueba.
        kwargs["endpoint_url"] = endpoint
        access_key = os.getenv("AWS_ACCESS_KEY_ID")
        secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
        if access_key and secret_key:
            kwargs["aws_access_key_id"] = access_key
            kwargs["aws_secret_access_key"] = secret_key

    _s3_client = boto3.client("s3", **kwargs)
    return _s3_client


def _decode_payload(value: Any) -> Dict[str, Any]:
    """Convierte el cuerpo del mensaje (str/bytes/dict) en un diccionario."""
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError("El payload de la tríada debe ser un objeto JSON")
    return value


def _iter_payloads(event: Any) -> Iterator[Dict[str, Any]]:
    """Normaliza el evento de entrada a uno o más payloads de tríada."""
    if isinstance(event, list):
        for item in event:
            yield from _iter_payloads(item)
        return

    if not isinstance(event, dict):
        raise ValueError("Evento inválido: se esperaba un objeto JSON")

    if "Records" in event:
        for record in event["Records"]:
            if not isinstance(record, dict):
                continue
            body = record.get("body")
            if body is None and "Sns" in record:
                body = record["Sns"].get("Message")
            if body is None:
                body = record
            yield _decode_payload(body)
        return

    if isinstance(event.get("detail"), dict):
        yield event["detail"]
        return

    if "body" in event:
        yield _decode_payload(event["body"])
        return

    yield event


def _safe_cu(codigo_cu: Any) -> str:
    """Sanitiza el codigoCu para que sea seguro usarlo como prefijo S3."""
    valor = str(codigo_cu or "UNKNOWN_CU").strip()
    return re.sub(r"[^A-Za-z0-9._-]", "_", valor) or "UNKNOWN_CU"


def _payload_hash(payload: Dict[str, Any]) -> str:
    """Hash determinístico del payload (clave de idempotencia en S3).

    Se serializa con claves ordenadas y sin espacios para que el mismo
    contenido produzca siempre la misma huella, sin importar el orden de las
    claves en el JSON de entrada.
    """
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _build_s3_key(codigo_cu: Any, payload: Dict[str, Any]) -> str:
    """Clave S3 determinística: reintentos del mismo payload se sobrescriben."""
    return f"{PREFIX}/{_safe_cu(codigo_cu)}/{_payload_hash(payload)}_payload.json"


def _put_object(s3, key: str, payload: Dict[str, Any]) -> None:
    body = json.dumps(payload, indent=2, ensure_ascii=False)
    try:
        s3.put_object(
            Bucket=TARGET_BUCKET,
            Key=key,
            Body=body.encode("utf-8"),
            ContentType="application/json",
        )
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        # Auto-recuperación únicamente contra el S3 emulado (LocalStack).
        if error_code in {"NoSuchBucket", "404"} and _endpoint_url():
            logger.warning("Bucket %s no existe en local; creándolo.", TARGET_BUCKET)
            s3.create_bucket(Bucket=TARGET_BUCKET)
            s3.put_object(
                Bucket=TARGET_BUCKET,
                Key=key,
                Body=body.encode("utf-8"),
                ContentType="application/json",
            )
        else:
            raise


def _persistir(payload: Dict[str, Any]) -> Dict[str, Any]:
    codigo_cu = payload.get("codigoCu") or payload.get("codigo_cu")
    if not codigo_cu:
        raise ValueError("El payload de la tríada no contiene 'codigoCu'")

    key = _build_s3_key(codigo_cu, payload)
    _put_object(get_s3_client(), key, payload)
    logger.info("Tríada escrita en s3://%s/%s", TARGET_BUCKET, key)
    return {
        "codigoCu": codigo_cu,
        "s3_bucket": TARGET_BUCKET,
        "s3_path": key,
    }


def lambda_handler(event, _context):
    """Punto de entrada de la Lambda Egress Orchestrator."""
    try:
        logger.info("Lambda Egress Orchestrator invocada con evento: %s", event)

        resultados: List[Dict[str, Any]] = [
            _persistir(payload) for payload in _iter_payloads(event)
        ]
        if not resultados:
            raise ValueError("El evento no contiene ningún payload de tríada")

        body: Dict[str, Any] = (
            resultados[0] if len(resultados) == 1 else {"procesados": resultados}
        )
        return {
            "statusCode": 200,
            "body": json.dumps(
                {"message": "Tríada guardada exitosamente", **body},
                ensure_ascii=False,
            ),
        }
    except Exception as exc:  # noqa: BLE001 - se relanza para que Lambda marque FunctionError
        logger.exception("Error procesando la tríada en Lambda Egress: %s", exc)
        # No se devuelve un 500 en el body: se relanza la excepción para que
        # Lambda reporte FunctionError. Así SQS/EventBridge/API Gateway y el
        # invocador síncrono (ECS) pueden reintentar o enviar a DLQ.
        raise


# Código auxiliar para probar la Lambda de manera local
if __name__ == "__main__":
    # Por defecto el modo standalone apunta a LocalStack. En AWS no se ejecuta
    # este bloque: se usa el rol IAM y el endpoint real de S3.
    os.environ.setdefault("ENDPOINT_URL", "http://localhost:4566")
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
    os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

    test_event = {
        "codigoCu": "CU-1001",
        "golden_record_id": "GR-9999",
        "matches_found": True,
        "score_calidad": 98.5,
        "datos_consolidados": {
            "nombre": "Juan Perez",
            "documentoNormalizado": "123456789",
        },
    }

    print("Probando Lambda localmente...")
    resultado = lambda_handler(test_event, None)
    print("Resultado:", resultado)
