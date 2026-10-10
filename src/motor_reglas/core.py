"""Núcleo del motor de reglas CCUC y lógica de negocio.

Garantiza idempotencia (hashPayload), tabla de verdad (estado inactivo)
y coordina los puntos de control del procesamiento.
"""
import hashlib
import json
from typing import Dict, Any, Set, Optional, Callable


def hash_payload(payload: dict) -> str:
    """Huella digital del contenido del mensaje (spec: hashPayload)."""
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def can_intersect(estado: str) -> bool:
    """
    Tabla de verdad: un cliente Inactivo ("I" o "Inactivo") nunca genera la arista :INTERSECTA.
    """
    if not estado:
        return True
    estado_str = str(estado).strip().upper()
    return estado_str not in {"I", "INACTIVO"}


def process_novedad(
    payload: dict,
    seen: set,
    *,
    touch: Optional[Callable[[dict], None]] = None,
    write_edge: Optional[Callable[[dict], None]] = None
) -> str:
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
        
    # Validar tanto 'estado' como 'estadoCliente'
    estado = payload.get("estado")
    if estado is None:
        estado = payload.get("estadoCliente", "")
        
    activo = can_intersect(estado)
    if write_edge is not None and activo:
        write_edge(payload)
        
    return "nuevo" if activo else "inactivo"
