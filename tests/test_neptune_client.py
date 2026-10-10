"""Pruebas unitarias para el cliente de Neptune Gremlin y operaciones de grafo."""
import pytest
from motor_reglas.neptune_client import NeptuneGremlinClient


def test_neptune_upsert_cliente_y_pvu():
    client = NeptuneGremlinClient(use_mock_fallback=True)
    
    # 1. Upsert Cliente
    cu_id = client.upsert_cliente(
        codigo_cu="CU-1018273645",
        documento_normalizado="1018273645",
        nombre_completo="JUAN CARLOS PEREZ",
        tipo_persona="NATURAL",
        estado="ACTIVO"
    )
    assert cu_id == "CU-1018273645"

    # 2. Upsert PVU
    pvu_id = client.upsert_pvu(
        codigo_pvu="PVU-5001-ABC123",
        direccion_normalizada="CL 50 # 45-20",
        codigo_ciudad="5001",
        nombre_negocio="TIENDA JP",
        h3_index="h3_cell_123"
    )
    assert pvu_id == "PVU-5001-ABC123"

    # 3. Vincular Cliente y PVU
    client.vincular_cliente_pvu(cu_id, pvu_id)
    edges = [e for e in client._mock_edges if e["label"] == "TIENE_PVU"]
    assert len(edges) == 1
    assert edges[0]["from"] == cu_id
    assert edges[0]["to"] == pvu_id


def test_neptune_regla_intersecta_activo_vs_inactivo():
    client = NeptuneGremlinClient(use_mock_fallback=True)
    
    # Si activo=False, regla de negocio impide crear arista :INTERSECTA
    creada_inactivo = client.crear_relacion_intersecta("CU-001", "CU-002", activo=False)
    assert creada_inactivo is False
    assert len([e for e in client._mock_edges if e["label"] == "INTERSECTA"]) == 0

    # Si activo=True, crea la arista
    creada_activo = client.crear_relacion_intersecta("CU-001", "CU-002", activo=True)
    assert creada_activo is True
    assert len([e for e in client._mock_edges if e["label"] == "INTERSECTA"]) == 1
