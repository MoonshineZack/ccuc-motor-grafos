"""Validación del contrato estructural de las novedades recibidas por SQS FIFO.

La Capa Media publica un JSON plano (un cliente por mensaje). El consumidor
exige la presencia de un conjunto de **campos críticos** para poder construir
un CU/PVU confiable. El Motor de Calidad usa ``dict.get()`` con valores por
defecto, por lo que un campo crítico ausente generaría un registro parcial sin
error visible.

Regla de negocio: si falta al menos uno de los ``CAMPOS_OBLIGATORIOS``, el
mensaje se envía a la DLQ y NO se ejecuta el pipeline (Neptune / Lambda Egress).

Nota: la validación es de **presencia de claves**, no de contenido. El resto de
campos del contrato (``CAMPOS_OPCIONALES``) son opcionales por diseño y pueden
llegar como "" o incluso no venir; ``razonSocial`` o ``barrio`` pueden estar
vacíos en personas naturales y aun así considerarse presentes.
"""
from typing import Any, List, Tuple

#: Campos críticos que todo mensaje de novedad debe traer, aunque su valor sea "".
CAMPOS_OBLIGATORIOS: Tuple[str, ...] = (
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
)

#: Campos complementarios del contrato. Su ausencia NO descarta el mensaje.
CAMPOS_OPCIONALES: Tuple[str, ...] = (
    "primerNombre",
    "segundoNombre",
    "primerApellido",
    "segundoApellido",
    "direccion2",
    "direccion3",
    "direccion4",
    "direccion5",
    "nombreNegocio",
    "peticionDeBorrado",
    "peticionDeBorradoIndividual",
)


def campos_faltantes(payload: Any) -> List[str]:
    """Devuelve las claves obligatorias ausentes del payload.

    - Un payload que no sea un objeto JSON (dict) carece de todas las claves.
    - Las claves presentes con valor ``None`` o ``""`` se consideran presentes:
      la regla solo exige que los 10 campos críticos estén en la estructura.
    """
    if not isinstance(payload, dict):
        return list(CAMPOS_OBLIGATORIOS)
    return [campo for campo in CAMPOS_OBLIGATORIOS if campo not in payload]


def es_estructura_valida(payload: Any) -> bool:
    """``True`` si el payload contiene todas las claves obligatorias."""
    return not campos_faltantes(payload)
