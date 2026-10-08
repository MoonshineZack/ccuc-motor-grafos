"""Prueba crítica (spec 5.3): si el cliente está Inactivo ("I"),
el algoritmo NO crea la arista :INTERSECTA."""
import pytest

from motor_reglas.core import process_novedad


@pytest.mark.parametrize(
    "estado,crea_arista",
    [("I", False), ("A", True), ("S", True), ("", True)],
)
def test_tabla_verdad_intersecta(estado, crea_arista):
    payload = {"cliente_a": "C-001", "cliente_b": "C-002", "estado": estado}
    aristas: list = []
    process_novedad(payload, set(), write_edge=aristas.append)
    assert bool(aristas) is crea_arista


def test_inactivo_sin_arista_y_no_se_reintenta_como_activo():
    payload = {"cliente_a": "C-001", "cliente_b": "C-002", "estado": "I"}
    aristas: list = []
    process_novedad(payload, set(), write_edge=aristas.append)
    # repetir la misma novedad no debe crear la arista tampoco
    process_novedad(payload, set(), write_edge=aristas.append)
    assert aristas == []
