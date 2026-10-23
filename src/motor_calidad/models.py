"""Modelos de datos para el Motor de Calidad CU/PVU y Tríadas CCUC."""
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any


@dataclass
class CUDepurado:
    """Representación limpia y normalizada de un Cliente Único (CU)."""
    codigo_cu: str
    documento_original: Optional[str]
    documento_normalizado: str
    nombre_completo: str
    tipo_persona: str  # "NATURAL" o "JURIDICA"
    razon_social_normalizada: str
    estado_cliente: str
    es_valido: bool
    errores: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PVUDepurado:
    """Representación limpia y normalizada de un Punto de Venta Único (PVU)."""
    codigo_pvu: str
    direccion_original: str
    direccion_normalizada: str
    barrio_normalizado: str
    codigo_ciudad: str
    codigo_departamento: str
    pais: str
    nombre_negocio: str
    h3_index: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ResultadoCalidad:
    """Resultado del proceso de depuración y fuzzy matching."""
    cu: CUDepurado
    pvu: PVUDepurado
    score_calidad: float
    score_similitud_candidato: float
    es_nuevo_cliente: bool
    matches_encontrados: bool
    candidato_asociado_id: Optional[str] = None
    bloqueo_keys: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cu": self.cu.to_dict(),
            "pvu": self.pvu.to_dict(),
            "score_calidad": self.score_calidad,
            "score_similitud_candidato": self.score_similitud_candidato,
            "es_nuevo_cliente": self.es_nuevo_cliente,
            "matches_encontrados": self.matches_encontrados,
            "candidato_asociado_id": self.candidato_asociado_id,
            "bloqueo_keys": self.bloqueo_keys,
        }


@dataclass
class PayloadTriada:
    """Estructura de la Tríada resuelta para enviar a Lambda Egress Orchestrator."""
    codigo_cu: str
    codigo_pvu: str
    golden_record_id: str
    matches_found: bool
    score_calidad: float
    datos_consolidados: Dict[str, Any]
    grafo_metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "codigoCu": self.codigo_cu,
            "codigoPvu": self.codigo_pvu,
            "golden_record_id": self.golden_record_id,
            "matches_found": self.matches_found,
            "score_calidad": self.score_calidad,
            "datos_consolidados": self.datos_consolidados,
            "grafo_metadata": self.grafo_metadata,
            "timestamp": self.timestamp,
        }
