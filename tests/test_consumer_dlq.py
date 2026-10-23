"""Pruebas del consumidor: descarte a DLQ y confirmación de mensajes SQS FIFO."""
import json

from motor_reglas.consumer import procesar_mensaje
from motor_reglas.dlq import construir_mensaje_dlq, mover_a_dlq
from motor_reglas.validacion import CAMPOS_OBLIGATORIOS

QUEUE_URL = "http://localstack/000000000000/ccuc-novedades-prod.fifo"
DLQ_URL = "http://localstack/000000000000/ccuc-novedades-dlq.fifo"


def payload_valido() -> dict:
    return {campo: "x" for campo in CAMPOS_OBLIGATORIOS}


def mensaje(body, msg_id="msg-1", group_id="doc-1018", dedup_id="dedup-1"):
    return {
        "MessageId": msg_id,
        "ReceiptHandle": f"rh-{msg_id}",
        "Body": body if isinstance(body, str) else json.dumps(body),
        "Attributes": {
            "MessageGroupId": group_id,
            "MessageDeduplicationId": dedup_id,
        },
    }


class FakeSQS:
    def __init__(self, error_al_enviar=None):
        self.enviados = []
        self.eliminados = []
        self.error_al_enviar = error_al_enviar

    def send_message(self, **kwargs):
        if self.error_al_enviar is not None:
            raise self.error_al_enviar
        self.enviados.append(kwargs)
        return {"MessageId": "dlq-1"}

    def delete_message(self, **kwargs):
        self.eliminados.append(kwargs)
        return {}


class FakeOrchestrator:
    def __init__(self, resultado=None, error=None):
        self.resultado = resultado or {"status": "procesado", "codigoCu": "CU-1018"}
        self.error = error
        self.llamadas = []

    def procesar_novedad(self, payload, seen_cache=None):
        self.llamadas.append({"payload": payload, "seen_cache": seen_cache})
        if self.error is not None:
            raise self.error
        return self.resultado


def test_mensaje_valido_se_procesa_y_confirma():
    sqs = FakeSQS()
    orchestrator = FakeOrchestrator()
    seen = set()
    msg = mensaje(payload_valido())

    estado = procesar_mensaje(sqs, QUEUE_URL, DLQ_URL, msg, orchestrator, seen)

    assert estado == "procesado"
    assert len(orchestrator.llamadas) == 1
    assert orchestrator.llamadas[0]["payload"]["documento"] == "x"
    assert orchestrator.llamadas[0]["seen_cache"] is seen
    assert sqs.enviados == []
    assert sqs.eliminados == [{"QueueUrl": QUEUE_URL, "ReceiptHandle": "rh-msg-1"}]


def test_duplicado_tambien_se_confirma():
    sqs = FakeSQS()
    orchestrator = FakeOrchestrator(resultado={"status": "duplicado"})

    estado = procesar_mensaje(sqs, QUEUE_URL, DLQ_URL, mensaje(payload_valido()), orchestrator)

    assert estado == "duplicado"
    assert len(sqs.eliminados) == 1


def test_estructura_incompleta_va_a_dlq_sin_ejecutar_pipeline():
    sqs = FakeSQS()
    orchestrator = FakeOrchestrator()
    payload = payload_valido()
    del payload["documento"]
    del payload["codigoDepartamento"]

    estado = procesar_mensaje(sqs, QUEUE_URL, DLQ_URL, mensaje(payload), orchestrator)

    assert estado == "dlq"
    assert orchestrator.llamadas == []
    assert len(sqs.enviados) == 1

    enviado = sqs.enviados[0]
    assert enviado["QueueUrl"] == DLQ_URL
    assert enviado["MessageGroupId"] == "doc-1018"
    assert enviado["MessageDeduplicationId"] == "dedup-1"
    assert "documento" not in json.loads(enviado["MessageBody"])
    motivo = enviado["MessageAttributes"]["motivoDlq"]["StringValue"]
    assert "ESTRUCTURA_INVALIDA" in motivo
    assert "documento" in motivo
    assert "codigoDepartamento" in motivo
    # El mensaje original se elimina de la cola principal.
    assert sqs.eliminados == [{"QueueUrl": QUEUE_URL, "ReceiptHandle": "rh-msg-1"}]


def test_campo_opcional_ausente_no_va_a_dlq():
    """Solo los 10 campos críticos son obligatorios."""
    sqs = FakeSQS()
    orchestrator = FakeOrchestrator()
    payload = payload_valido()
    payload.pop("primerNombre", None)
    payload.pop("nombreNegocio", None)

    estado = procesar_mensaje(sqs, QUEUE_URL, DLQ_URL, mensaje(payload), orchestrator)

    assert estado == "procesado"
    assert len(orchestrator.llamadas) == 1
    assert sqs.enviados == []
    assert len(sqs.eliminados) == 1


def test_json_invalido_va_a_dlq():
    sqs = FakeSQS()
    orchestrator = FakeOrchestrator()

    estado = procesar_mensaje(sqs, QUEUE_URL, DLQ_URL, mensaje("{no es json"), orchestrator)

    assert estado == "dlq"
    assert orchestrator.llamadas == []
    assert len(sqs.enviados) == 1
    assert "JSON_INVALIDO" in sqs.enviados[0]["MessageAttributes"]["motivoDlq"]["StringValue"]
    assert len(sqs.eliminados) == 1


def test_json_que_no_es_objeto_va_a_dlq():
    sqs = FakeSQS()
    orchestrator = FakeOrchestrator()

    estado = procesar_mensaje(sqs, QUEUE_URL, DLQ_URL, mensaje("[]"), orchestrator)

    assert estado == "dlq"
    assert orchestrator.llamadas == []
    assert len(sqs.enviados) == 1


def test_error_del_pipeline_no_envia_a_dlq_y_deja_reintento():
    sqs = FakeSQS()
    orchestrator = FakeOrchestrator(error=RuntimeError("Neptune no disponible"))

    estado = procesar_mensaje(sqs, QUEUE_URL, DLQ_URL, mensaje(payload_valido()), orchestrator)

    assert estado == "reintento"
    assert sqs.enviados == []
    assert sqs.eliminados == []


def test_pipeline_no_confirmado_no_se_borra():
    sqs = FakeSQS()
    orchestrator = FakeOrchestrator(resultado={"status": "fallido", "egress_status": "error"})

    estado = procesar_mensaje(sqs, QUEUE_URL, DLQ_URL, mensaje(payload_valido()), orchestrator)

    assert estado == "reintento"
    assert sqs.enviados == []
    assert sqs.eliminados == []


def test_sin_dlq_configurada_el_mensaje_no_se_borra():
    sqs = FakeSQS()
    orchestrator = FakeOrchestrator()

    estado = procesar_mensaje(sqs, QUEUE_URL, None, mensaje("{}"), orchestrator)

    assert estado == "dlq"
    assert orchestrator.llamadas == []
    assert sqs.enviados == []
    assert sqs.eliminados == []


def test_fallo_al_enviar_a_dlq_no_borra_el_mensaje_original():
    sqs = FakeSQS(error_al_enviar=RuntimeError("SQS DLQ caída"))
    orchestrator = FakeOrchestrator()

    descartado = mover_a_dlq(sqs, QUEUE_URL, DLQ_URL, mensaje(payload_valido()), "motivo")

    assert descartado is False
    assert sqs.eliminados == []


def test_construir_mensaje_dlq_usa_grupo_por_defecto_si_falta():
    kwargs = construir_mensaje_dlq({"MessageId": "m-1", "Body": "{}"}, "motivo")

    assert kwargs["MessageGroupId"] == "SIN_GRUPO"
    assert "MessageDeduplicationId" not in kwargs
    assert kwargs["MessageAttributes"]["messageIdOrigen"]["StringValue"] == "m-1"
