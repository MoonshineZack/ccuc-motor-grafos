"""Motor de Calidad CU/PVU con RapidFuzz para el proyecto CCUC."""
from motor_calidad.models import CUDepurado, PVUDepurado, ResultadoCalidad, PayloadTriada
from motor_calidad.normalizer import (
    normalize_document,
    normalize_person_name,
    normalize_razon_social,
    normalize_address,
    calculate_h3_index,
)
from motor_calidad.fuzzy_engine import MotorCalidadCUPVU

__all__ = [
    "CUDepurado",
    "PVUDepurado",
    "ResultadoCalidad",
    "PayloadTriada",
    "MotorCalidadCUPVU",
    "normalize_document",
    "normalize_person_name",
    "normalize_razon_social",
    "normalize_address",
    "calculate_h3_index",
]
