"""Prueba crítica (spec 5.3): los duplicados se descartan por hashPayload
antes de llamar a OpenSearch o Neptune."""
from motor_reglas.core import hash_payload, process_novedad


def test_mismo_payload_misma_huella():
    payload = {"cliente_a": "C-001", "cliente_b": "C-002"}
    assert hash_payload(payload) == hash_payload(dict(payload))


def test_payloads_distintos_tienen_huella_distinta():
    assert hash_payload({"a": 1}) != hash_payload({"a": 2})


def test_duplicados_descartados_100pct_antes_de_tocar_clientes():
    payload = {"cliente_a": "C-001", "cliente_b": "C-002", "estado": "A"}
    seen: set = set()
    toques: list = []

    assert process_novedad(payload, seen, touch=toques.append) == "nuevo"

    for _ in range(100):
        assert process_novedad(payload, seen, touch=toques.append) == "duplicado"

    # 100 duplicados => 0 llamadas adicionales a OpenSearch/Neptune
    assert len(toques) == 1


def test_novedad_distinta_si_se_procesa():
    seen: set = set()
    toques: list = []
    process_novedad({"cliente_a": "C-001"}, seen, touch=toques.append)
    process_novedad({"cliente_a": "C-002"}, seen, touch=toques.append)
    assert len(toques) == 2
