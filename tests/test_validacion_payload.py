"""Pruebas del contrato de estructura del payload de novedades (SQS FIFO)."""
from motor_reglas.validacion import (
    CAMPOS_OBLIGATORIOS,
    CAMPOS_OPCIONALES,
    campos_faltantes,
    es_estructura_valida,
)


def payload_completo() -> dict:
    """Payload tal como lo publica la Capa Media (todos los campos, incluso vacíos)."""
    return {
        "CodigoTransaccional": "STEP-001",
        "primerNombre": "JUAN",
        "segundoNombre": "",
        "primerApellido": "PEREZ",
        "segundoApellido": "",
        "razonSocial": "",
        "documento": "1018273645",
        "direccion": "CALLE 50 # 45-20",
        "direccion2": "",
        "direccion3": "",
        "direccion4": "",
        "direccion5": "",
        "barrio": "CENTRO",
        "codigoCiudad": "5001",
        "codigoDepartamento": "5",
        "pais": "Colombia",
        "nombreNegocio": "TIENDA LA ESQUINA",
        "estadoCliente": "Activo",
        "peticionDeBorrado": "No",
        "peticionDeBorradoIndividual": "No",
        "situacionCliente": "Creado",
    }


def payload_solo_obligatorios() -> dict:
    return {campo: "x" for campo in CAMPOS_OBLIGATORIOS}


def test_campos_obligatorios_son_los_10_campos_criticos():
    esperados = {
        "CodigoTransaccional",
        "razonSocial",
        "documento",
        "pais",
        "direccion",
        "barrio",
        "codigoCiudad",
        "codigoDepartamento",
        "estadoCliente",
        "situacionCliente",
    }
    assert set(CAMPOS_OBLIGATORIOS) == esperados
    assert len(CAMPOS_OBLIGATORIOS) == 10


def test_contrato_completo_suma_21_campos():
    esperados = {
        "CodigoTransaccional",
        "primerNombre",
        "segundoNombre",
        "primerApellido",
        "segundoApellido",
        "razonSocial",
        "documento",
        "direccion",
        "direccion2",
        "direccion3",
        "direccion4",
        "direccion5",
        "barrio",
        "codigoCiudad",
        "codigoDepartamento",
        "pais",
        "nombreNegocio",
        "estadoCliente",
        "peticionDeBorrado",
        "peticionDeBorradoIndividual",
        "situacionCliente",
    }
    assert set(CAMPOS_OBLIGATORIOS) | set(CAMPOS_OPCIONALES) == esperados
    assert len(CAMPOS_OBLIGATORIOS) + len(CAMPOS_OPCIONALES) == 21
    assert not set(CAMPOS_OBLIGATORIOS) & set(CAMPOS_OPCIONALES)


def test_payload_completo_es_valido():
    payload = payload_completo()

    assert campos_faltantes(payload) == []
    assert es_estructura_valida(payload) is True


def test_payload_solo_con_los_10_obligatorios_es_valido():
    """Los campos complementarios son opcionales: no deben bloquear el mensaje."""
    payload = payload_solo_obligatorios()

    assert campos_faltantes(payload) == []
    assert es_estructura_valida(payload) is True


def test_claves_extra_no_invalidan_el_payload():
    payload = payload_solo_obligatorios()
    payload["campoNuevoDeLaCapaMedia"] = "ignorado"

    assert es_estructura_valida(payload) is True


def test_detecta_una_clave_obligatoria_faltante():
    payload = payload_completo()
    del payload["documento"]

    assert campos_faltantes(payload) == ["documento"]
    assert es_estructura_valida(payload) is False


def test_detecta_varias_claves_obligatorias_faltantes_en_orden_del_contrato():
    payload = payload_completo()
    for campo in ("documento", "codigoCiudad", "situacionCliente"):
        del payload[campo]

    assert campos_faltantes(payload) == ["documento", "codigoCiudad", "situacionCliente"]


def test_faltante_de_campo_opcional_no_invalida_el_payload():
    payload = payload_completo()
    for campo in CAMPOS_OPCIONALES:
        del payload[campo]

    assert campos_faltantes(payload) == []
    assert es_estructura_valida(payload) is True


def test_valores_vacios_o_nulos_cuentan_como_clave_presente():
    """La regla valida presencia de claves; razonSocial o barrio pueden ir vacíos."""
    payload = payload_completo()
    payload["razonSocial"] = ""
    payload["barrio"] = None
    payload["segundoNombre"] = None
    payload["direccion2"] = ""

    assert campos_faltantes(payload) == []


def test_payload_que_no_es_objeto_carece_de_todos_los_campos_obligatorios():
    for payload in ([], "texto plano", 123, None):
        assert campos_faltantes(payload) == list(CAMPOS_OBLIGATORIOS)
        assert es_estructura_valida(payload) is False
