"""Pruebas unitarias para el Motor de Calidad CU/PVU y RapidFuzz."""
import pytest
from motor_calidad.normalizer import (
    clean_text,
    normalize_document,
    normalize_person_name,
    normalize_razon_social,
    normalize_address,
)
from motor_calidad.fuzzy_engine import MotorCalidadCUPVU


def test_normalizacion_documento_valido_e_invalido():
    # Documentos válidos con formatos diversos
    doc, valido = normalize_document("1.018.273.645")
    assert doc == "1018273645"
    assert valido is True

    doc, valido = normalize_document(" 900-123-456 ")
    assert doc == "900123456"
    assert valido is True

    # Documentos nulos o inválidos
    doc, valido = normalize_document(None)
    assert doc == ""
    assert valido is False

    doc, valido = normalize_document("000")
    assert valido is False


def test_normalizacion_razon_social():
    # Retiro de acrónimos societarios colombianos
    assert normalize_razon_social("Distribuidora La Mayorista S.A.S.") == "DISTRIBUIDORA LA MAYORISTA"
    assert normalize_razon_social("Comercial Nutresa S.A.") == "COMERCIAL NUTRESA"
    assert normalize_razon_social("Inversiones El Éxito LTDA") == "INVERSIONES EL EXITO"


def test_normalizacion_persona_natural():
    nombre = normalize_person_name("Juan", "Carlos", "Pérez", "Gómez")
    assert nombre == "JUAN CARLOS PEREZ GOMEZ"


def test_normalizacion_direccion_colombiana():
    # Estandarización de vías y nomenclatura
    dir1 = normalize_address("Calle 50 # 45-20", "Apto 301")
    assert "CL 50 # 45-20" in dir1
    assert "APTO 301" in dir1

    dir2 = normalize_address("Carrera 45 No. 10-20")
    assert "CR 45 # 10-20" in dir2

    dir3 = normalize_address("Avenida 68 N° 12-45 Bodega 4")
    assert "AV 68 # 12-45" in dir3
    assert "BODEGA 4" in dir3


def test_motor_calidad_persona_natural_completa():
    engine = MotorCalidadCUPVU()
    novedad = {
        "primerNombre": "Juan",
        "segundoNombre": "Carlos",
        "primerApellido": "Pérez",
        "segundoApellido": "Gómez",
        "documento": "1018273645",
        "direccion": "Calle 50 # 45-20",
        "codigoCiudad": "5001",
        "codigoDepartamento": "5",
        "nombreNegocio": "Comercializadora J&P",
        "estadoCliente": "Activo"
    }
    resultado = engine.depurar_novedad(novedad)
    assert resultado.cu.es_valido is True
    assert resultado.cu.codigo_cu == "CU-1018273645"
    assert resultado.cu.tipo_persona == "NATURAL"
    assert resultado.score_calidad >= 90.0
    assert "CL 50 # 45-20" in resultado.pvu.direccion_normalizada


def test_motor_calidad_blocking_cu_re_documento_distinto():
    """Regla: CU-RE documento distinto -> cliente distinto."""
    engine = MotorCalidadCUPVU()
    novedad = {
        "primerNombre": "Juan",
        "primerApellido": "Pérez",
        "documento": "1018273645",
        "direccion": "Calle 50 # 45-20",
    }
    candidatos = [
        {
            "codigoCu": "CU-999999",
            "documentoNormalizado": "88888888",  # Documento distinto
            "nombreCompleto": "JUAN PEREZ",
        }
    ]
    resultado = engine.depurar_novedad(novedad, candidatos_existentes=candidatos)
    # Al tener documento distinto, NO debe asociarse a ese candidato
    assert resultado.matches_encontrados is False
    assert resultado.es_nuevo_cliente is True


def test_motor_calidad_rapidfuzz_matching():
    """Validación de fuzzy matching con RapidFuzz para variaciones de nombres."""
    engine = MotorCalidadCUPVU(umbral_similitud=80.0)
    novedad = {
        "primerNombre": "Maria",
        "segundoNombre": "Fernanda",
        "primerApellido": "Lopez",
        "documento": "987654321",
        "direccion": "Carrera 45 # 10-20",
    }
    candidatos = [
        {
            "codigoCu": "CU-987654321",
            "documentoNormalizado": "987654321",
            "nombreCompleto": "MARIA F LOPEZ",  # Variación en el nombre
        }
    ]
    resultado = engine.depurar_novedad(novedad, candidatos_existentes=candidatos)
    assert resultado.matches_encontrados is True
    assert resultado.candidato_asociado_id == "CU-987654321"
    assert resultado.score_similitud_candidato >= 80.0
