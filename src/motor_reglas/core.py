"""Núcleo mínimo del motor de reglas CCUC.

El equipo de desarrollo reemplaza estas reglas por la lógica real;
DevOps garantiza que el entorno las ejecuta y que los tests críticos corren.
"""
import hashlib
import json


def hash_payload(payload: dict) -> str:
    """Huella digital del contenido del mensaje (spec: hashPayload)."""
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def can_intersect(estado: str) -> bool:
    """Tabla de verdad: un cliente Inactivo ("I") nunca genera la arista :INTERSECTA."""
    return estado != "I"


def process_novedad(payload: dict, seen: set, *, touch=None, write_edge=None) -> str:
    """Procesa una novedad descartando duplicados ANTES de tocar OpenSearch/Neptune.

    touch: representa la consulta a OpenSearch/Neptune.
    write_edge: representa la creación de la arista :INTERSECTA.
    Retorna "nuevo", "duplicado" o "inactivo".
    """
    huella = hash_payload(payload)
    if huella in seen:
        return "duplicado"
    seen.add(huella)
    if touch is not None:
        touch(payload)
    activo = can_intersect(payload.get("estado", ""))
    if write_edge is not None and activo:
        write_edge(payload)
    return "nuevo" if activo else "inactivo"
