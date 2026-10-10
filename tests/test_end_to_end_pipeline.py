"""Prueba de integración End-to-End del pipeline completo CCUC:
1. Simulación de mensaje SQS (Capa Media)
2. Deduplicación por hashPayload en ECS Fargate
3. Normalización y Fuzzy Matching con RapidFuzz (Motor de Calidad)
4. Persistencia en Grafo con Gremlin API (Neptune)
5. Emisión de Tríada resuelta y persistencia en S3 (Lambda Egress)
"""
import json
import pytest
from motor_reglas.orchestrator import CCUCOrchestrator
from motor_reglas.neptune_client import NeptuneGremlinClient
from motor_calidad.fuzzy_engine import MotorCalidadCUPVU


def test_pipeline_completo_con_novedades_dummy():
    # Inicializar componentes
    neptune_client = NeptuneGremlinClient(use_mock_fallback=True)
    calidad_engine = MotorCalidadCUPVU()

    # Emular llamadas a Lambda Egress recolectando las tríadas enviadas
    triadas_recibidas = []

    class LambdaMock:
        def invoke(self, FunctionName, InvocationType, Payload):
            data = json.loads(Payload) if isinstance(Payload, str) else Payload
            triadas_recibidas.append(data)
            return {"StatusCode": 200, "Payload": json.dumps({"status": "ok"})}

    orchestrator = CCUCOrchestrator(
        neptune_client=neptune_client,
        calidad_engine=calidad_engine,
        lambda_client=LambdaMock()
    )

    seen_cache = set()

    # Casos de prueba reales de la Capa Media (como en scripts/simulate_sqs_messages.py)
    novedades = [
        {
            # Caso 1: Persona Natural
            "primerNombre": "Juan",
            "segundoNombre": "Carlos",
            "primerApellido": "Pérez",
            "segundoApellido": "Gómez",
            "documento": "1018273645",
            "direccion": "Calle 50 # 45-20",
            "direccion2": "Apto 301",
            "barrio": "El Poblado",
            "codigoCiudad": "5001",
            "codigoDepartamento": "5",
            "pais": "Colombia",
            "nombreNegocio": "Comercializadora J&P",
            "estadoCliente": "Activo"
        },
        {
            # Caso 2: Persona Jurídica (Razón Social)
            "primerNombre": "",
            "primerApellido": "",
            "razonSocial": "Distribuidora La Mayorista S.A.S.",
            "documento": "900123456",
            "direccion": "Cl 10 # 50-60",
            "direccion2": "Bodega 4",
            "barrio": "Zona Industrial",
            "codigoCiudad": "76001",
            "codigoDepartamento": "76",
            "pais": "Colombia",
            "nombreNegocio": "La Mayorista",
            "estadoCliente": "Activo"
        },
        {
            # Caso 3: Inactivo (no debe crear arista :INTERSECTA)
            "primerNombre": "Carlos",
            "primerApellido": "Mora",
            "documento": "88889999",
            "direccion": "AV 68 # 12-45",
            "codigoCiudad": "11001",
            "estadoCliente": "Inactivo"
        }
    ]

    # Procesar las 3 novedades
    for novedad in novedades:
        res = orchestrator.procesar_novedad(novedad, seen_cache=seen_cache)
        assert res["status"] == "procesado"

    # Verificar Tríadas generadas
    assert len(triadas_recibidas) == 3
    
    # Validar Tríada 1 (Persona Natural)
    t1 = triadas_recibidas[0]
    assert t1["codigoCu"] == "CU-1018273645"
    assert t1["datos_consolidados"]["tipoPersona"] == "NATURAL"
    assert "CL 50 # 45-20" in t1["datos_consolidados"]["direccionNormalizada"]
    assert t1["score_calidad"] >= 90.0

    # Validar Tríada 2 (Persona Jurídica)
    t2 = triadas_recibidas[1]
    assert t2["codigoCu"] == "CU-900123456"
    assert t2["datos_consolidados"]["tipoPersona"] == "JURIDICA"
    assert t2["datos_consolidados"]["nombre"] == "DISTRIBUIDORA LA MAYORISTA"

    # Validar Tríada 3 (Inactivo)
    t3 = triadas_recibidas[2]
    assert t3["datos_consolidados"]["estadoCliente"] == "INACTIVO"
    assert t3["grafo_metadata"]["arista_intersecta"] is False

    # Validar Grafo en Neptune
    vertices_cliente = [v for v in neptune_client._mock_vertices.values() if v.get("label") == "Cliente"]
    vertices_pvu = [v for v in neptune_client._mock_vertices.values() if v.get("label") == "PVU"]
    aristas_tiene_pvu = [e for e in neptune_client._mock_edges if e.get("label") == "TIENE_PVU"]

    assert len(vertices_cliente) == 3
    assert len(vertices_pvu) == 3
    assert len(aristas_tiene_pvu) == 3
