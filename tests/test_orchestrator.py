"""Pruebas unitarias para el Orquestador CCUC en ECS Fargate."""
import io
import json

import pytest

from motor_reglas.core import hash_payload
from motor_reglas.orchestrator import CCUCOrchestrator, TriadaEgressError
from motor_reglas.neptune_client import NeptuneGremlinClient
from motor_calidad.fuzzy_engine import MotorCalidadCUPVU


class MockLambdaClient:
    def __init__(self):
        self.invocations = []

    def invoke(self, FunctionName, InvocationType, Payload):
        self.invocations.append({
            "function": FunctionName,
            "type": InvocationType,
            "payload": Payload,
        })
        return {"StatusCode": 200}


class LambdaClienteConError:
    """Simula una Lambda que responde 200 pero cuyo handler falló (500)."""

    def invoke(self, FunctionName, InvocationType, Payload):
        cuerpo = json.dumps({"statusCode": 500, "body": json.dumps({"error": "S3 caído"})})
        return {"StatusCode": 200, "Payload": io.BytesIO(cuerpo.encode("utf-8"))}


NOVEDAD = {
    "primerNombre": "Juan",
    "segundoNombre": "Carlos",
    "primerApellido": "Pérez",
    "segundoApellido": "Gómez",
    "documento": "1018273645",
    "direccion": "Calle 50 # 45-20",
    "codigoCiudad": "5001",
    "codigoDepartamento": "5",
    "pais": "Colombia",
    "nombreNegocio": "Comercializadora J&P",
    "estadoCliente": "Activo",
}


def test_orquestador_flujo_completo_4_pasos():
    mock_lambda = MockLambdaClient()
    neptune_client = NeptuneGremlinClient(use_mock_fallback=True)
    calidad_engine = MotorCalidadCUPVU()
    
    orchestrator = CCUCOrchestrator(
        neptune_client=neptune_client,
        calidad_engine=calidad_engine,
        lambda_client=mock_lambda,
        lambda_egress_name="ccuc-egress-orchestrator"
    )

    novedad = {
        "primerNombre": "Juan",
        "segundoNombre": "Carlos",
        "primerApellido": "Pérez",
        "segundoApellido": "Gómez",
        "documento": "1018273645",
        "direccion": "Calle 50 # 45-20",
        "codigoCiudad": "5001",
        "codigoDepartamento": "5",
        "pais": "Colombia",
        "nombreNegocio": "Comercializadora J&P",
        "estadoCliente": "Activo"
    }

    seen_cache = set()

    # 1. Primera ejecución: procesa y ejecuta los 4 pasos
    res1 = orchestrator.procesar_novedad(novedad, seen_cache=seen_cache)
    assert res1["status"] == "procesado"
    assert res1["codigoCu"] == "CU-1018273645"
    assert res1["egress_status"] == "invocado_lambda"
    assert len(mock_lambda.invocations) == 1

    # 2. Segunda ejecución (mismo payload): deduplicación descarta en Paso 1
    res2 = orchestrator.procesar_novedad(novedad, seen_cache=seen_cache)
    assert res2["status"] == "duplicado"
    # No debe haber llamado a la Lambda nuevamente
    assert len(mock_lambda.invocations) == 1


def test_orquestador_cliente_inactivo_no_intersecta():
    mock_lambda = MockLambdaClient()
    neptune_client = NeptuneGremlinClient(use_mock_fallback=True)
    calidad_engine = MotorCalidadCUPVU()

    # Pre-cargar un cliente existente en Neptune
    neptune_client.upsert_cliente(
        codigo_cu="CU-999",
        documento_normalizado="987654321",
        nombre_completo="MARIA LOPEZ",
        tipo_persona="NATURAL",
        estado="ACTIVO"
    )

    orchestrator = CCUCOrchestrator(
        neptune_client=neptune_client,
        calidad_engine=calidad_engine,
        lambda_client=mock_lambda
    )

    # Novedad con estado Inactivo
    novedad_inactiva = {
        "primerNombre": "Maria",
        "primerApellido": "Lopez",
        "documento": "987654321",
        "direccion": "Cra 10 # 20-30",
        "estadoCliente": "Inactivo"
    }

    res = orchestrator.procesar_novedad(novedad_inactiva, seen_cache=set())
    assert res["status"] == "procesado"

    # Verificar que NO se haya creado la arista :INTERSECTA
    intersecta_edges = [e for e in neptune_client._mock_edges if e["label"] == "INTERSECTA"]
    assert len(intersecta_edges) == 0


def test_lambda_egress_fallida_propaga_error_y_no_confirma(monkeypatch):
    """Si la Lambda del Paso 4 falla, la novedad NO debe darse por procesada."""
    import lambda_egress.lambda_function as egress

    monkeypatch.setattr(
        egress,
        "lambda_handler",
        lambda event, context: {"statusCode": 500, "body": json.dumps({"error": "S3 caído"})},
    )

    orchestrator = CCUCOrchestrator(
        neptune_client=NeptuneGremlinClient(use_mock_fallback=True),
        calidad_engine=MotorCalidadCUPVU(),
        lambda_client=LambdaClienteConError(),
    )

    with pytest.raises(TriadaEgressError):
        orchestrator.procesar_novedad(NOVEDAD, seen_cache=set())


def test_fallo_de_egress_libera_hash_para_reintento():
    """Un fallo en el envío no debe marcar la novedad como duplicada."""
    orchestrator = CCUCOrchestrator(
        neptune_client=NeptuneGremlinClient(use_mock_fallback=True),
        calidad_engine=MotorCalidadCUPVU(),
        lambda_client=MockLambdaClient(),
    )

    llamadas = {"n": 0}

    def egress_inestable(payload):
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            raise TriadaEgressError("fallo transitorio")
        return {"status": "invocado_lambda", "statusCode": 200}

    orchestrator._enviar_a_lambda_egress = egress_inestable
    seen: set = set()

    with pytest.raises(TriadaEgressError):
        orchestrator.procesar_novedad(NOVEDAD, seen_cache=seen)

    assert hash_payload(NOVEDAD) not in seen

    resultado = orchestrator.procesar_novedad(NOVEDAD, seen_cache=seen)
    assert resultado["status"] == "procesado"
    assert llamadas["n"] == 2


def test_evento_enviado_a_lambda_contiene_la_triada():
    """El evento del Paso 4 debe llevar la tríada completa a la Lambda."""
    mock_lambda = MockLambdaClient()
    orchestrator = CCUCOrchestrator(
        neptune_client=NeptuneGremlinClient(use_mock_fallback=True),
        calidad_engine=MotorCalidadCUPVU(),
        lambda_client=mock_lambda,
    )

    orchestrator.procesar_novedad(NOVEDAD, seen_cache=set())

    assert len(mock_lambda.invocations) == 1
    evento = json.loads(mock_lambda.invocations[0]["payload"])
    assert mock_lambda.invocations[0]["type"] == "RequestResponse"
    assert evento["codigoCu"] == "CU-1018273645"
    assert evento["codigoPvu"].startswith("PVU-")
    assert "datos_consolidados" in evento
    assert "grafo_metadata" in evento
