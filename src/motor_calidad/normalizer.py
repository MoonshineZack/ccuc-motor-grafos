"""Módulo de normalización y estandarización para CU y PVU."""
import re
import unicodedata
from typing import Optional, Tuple, Any


def strip_accents(text: str) -> str:
    """Elimina tildes y caracteres diacríticos."""
    if not text:
        return ""
    nfkd_form = unicodedata.normalize('NFKD', text)
    return "".join([c for c in nfkd_form if not unicodedata.combining(c)])


def clean_text(text: Optional[str]) -> str:
    """Normaliza texto básico: mayúsculas, sin tildes, sin espacios duplicados."""
    if not text:
        return ""
    text = strip_accents(str(text).strip().upper())
    # Reemplazar múltiples espacios o tabulaciones
    return re.sub(r'\s+', ' ', text)


def normalize_document(doc: Optional[Any]) -> Tuple[str, bool]:
    """
    Normaliza el número de identificación (cédula, NIT, pasaporte).
    Retorna (documento_limpio, es_valido).
    """
    if doc is None:
        return "", False
    
    doc_str = clean_text(str(doc))
    # Quitar puntos, guiones, comas, espacios
    cleaned = re.sub(r'[^0-9A-Z]', '', doc_str)
    
    # Validar que no sea vacío ni secuencias inválidas típicas
    if not cleaned or cleaned in {"0", "00", "000", "0000", "NULL", "NONE", "UNKNOWN"}:
        return cleaned, False
    
    # Si tiene al menos 5 caracteres numéricos/alfanuméricos se considera preliminarmente válido
    return cleaned, len(cleaned) >= 5


def normalize_razon_social(razon: Optional[str]) -> str:
    """Limpia razón social retirando acrónimos societarios comunes."""
    text = clean_text(razon)
    if not text:
        return ""
    
    # Patrones societarios colombianos
    suffixes = [
        r'\bS\.?A\.?S\.?\b',
        r'\bS\.?A\.?\b',
        r'\bLTDA\.?\b',
        r'\bLIMITADA\b',
        r'\bE\.?U\.?\b',
        r'\bS\.? EN C\.?\b',
        r'\bCIA\.?\b',
        r'\bCOMPANIA\b',
        r'\bSUCURSAL COLOMBIA\b',
    ]
    for pattern in suffixes:
        text = re.sub(pattern, '', text)
    
    # Limpiar puntuaciones residuales
    text = re.sub(r'[^0-9A-Z\s]', '', text)
    return re.sub(r'\s+', ' ', text).strip()


def normalize_person_name(
    primer_nombre: Optional[str],
    segundo_nombre: Optional[str],
    primer_apellido: Optional[str],
    segundo_apellido: Optional[str]
) -> str:
    """Construye y normaliza el nombre completo de una persona natural."""
    partes = [
        clean_text(primer_nombre),
        clean_text(segundo_nombre),
        clean_text(primer_apellido),
        clean_text(segundo_apellido),
    ]
    nombre_completo = " ".join([p for p in partes if p])
    # Limpiar puntuaciones raras
    nombre_completo = re.sub(r'[^A-Z\s]', '', nombre_completo)
    return re.sub(r'\s+', ' ', nombre_completo).strip()


def normalize_address(
    direccion: Optional[str],
    direccion2: Optional[str] = "",
    direccion3: Optional[str] = "",
    direccion4: Optional[str] = "",
    direccion5: Optional[str] = "",
) -> str:
    """
    Estandariza direcciones colombianas para PVU.
    Mapea vías (CALLE -> CL, CARRERA -> CR, etc.) y unifica nomenclatura.
    """
    complements = [direccion, direccion2, direccion3, direccion4, direccion5]
    full_addr = " ".join([clean_text(c) for c in complements if clean_text(c)])
    if not full_addr:
        return ""

    # Reemplazo de tipos de vía estándar
    replacements = [
        (r'\b(CALLE|CLL|CL\.)\b', 'CL'),
        (r'\b(CARRERA|CRA|KRA|KR|CR\.)\b', 'CR'),
        (r'\b(AVENIDA|AVDA|AV\.)\b', 'AV'),
        (r'\b(TRANSVERSAL|TRANSV|TRANS|TV\.)\b', 'TV'),
        (r'\b(DIAGONAL|DIAG|DG\.)\b', 'DG'),
        (r'\b(AUTOPISTA|AUTOP)\b', 'AUTOP'),
        (r'\b(CIRCULAR|CIRC|CQ)\b', 'CQ'),
        (r'\b(MANZANA|MZ\.)\b', 'MZ'),
        (r'\b(APARTAMENTO|APTO|APT)\b', 'APTO'),
        (r'\b(INTERIOR|INT\.)\b', 'INT'),
        (r'\b(BODEGA|BDG|BD)\b', 'BODEGA'),
        (r'\b(NUMERO|NRO|NO\.|NO)\b', '#'),
        (r'N°', '#'),
    ]

    for pat, rep in replacements:
        full_addr = re.sub(pat, rep, full_addr)

    # Limpiar caracteres superfluos excepto letras, números, '#' y '-'
    full_addr = re.sub(r'[^0-9A-Z#\-\s]', ' ', full_addr)
    # Estandarizar espacios alrededor de '#'
    full_addr = re.sub(r'\s*#\s*', ' # ', full_addr)
    # Estandarizar espacios alrededor de '-'
    full_addr = re.sub(r'\s*-\s*', '-', full_addr)

    return re.sub(r'\s+', ' ', full_addr).strip()


def calculate_h3_index(
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    resolution: int = 9,
    fallback_key: Optional[str] = None
) -> Optional[str]:
    """
    Calcula la celda H3 (resolución 9) si se suministran coordenadas geográficas.
    Si la biblioteca 'h3' no está instalada o faltan coordenadas, retorna un fallback derivado.
    """
    if lat is not None and lon is not None:
        try:
            import h3
            return h3.geo_to_h3(lat, lon, resolution)
        except (ImportError, Exception):
            pass
    
    if fallback_key:
        import hashlib
        return "h3_cell_" + hashlib.md5(fallback_key.encode()).hexdigest()[:10]
    return None
