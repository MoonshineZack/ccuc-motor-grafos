"""Motor de Fuzzy Matching y Calidad de Datos con RapidFuzz para CU/PVU."""
import hashlib
import uuid
from typing import Dict, Any, List, Optional

try:
    from rapidfuzz import fuzz
except ImportError:
    # Fallback básico si rapidfuzz no estuviera disponible
    class MockFuzz:
        @staticmethod
        def token_set_ratio(s1: str, s2: str) -> float:
            if s1 == s2:
                return 100.0
            words1, words2 = set(s1.split()), set(s2.split())
            if not words1 or not words2:
                return 0.0
            return 100.0 * len(words1 & words2) / max(len(words1), len(words2))

        @staticmethod
        def ratio(s1: str, s2: str) -> float:
            return 100.0 if s1 == s2 else 0.0

    fuzz = MockFuzz()

from motor_calidad.models import CUDepurado, PVUDepurado, ResultadoCalidad
from motor_calidad.normalizer import (
    clean_text,
    normalize_document,
    normalize_person_name,
    normalize_razon_social,
    normalize_address,
    calculate_h3_index,
)


class MotorCalidadCUPVU:
    """Motor encargado de normalizar, validar y aplicar fuzzy matching sobre clientes y PVU."""

    def __init__(self, umbral_similitud: float = 85.0):
        self.umbral_similitud = umbral_similitud

    def depurar_novedad(
        self,
        novedad: Dict[str, Any],
        candidatos_existentes: Optional[List[Dict[str, Any]]] = None
    ) -> ResultadoCalidad:
        """
        Recibe el payload en bruto de la novedad y genera el CU y PVU depurados
        aplicando reglas de calidad y comparación con candidatos.
        """
        candidatos_existentes = candidatos_existentes or []
        errores: List[str] = []

        # 1. Normalización de Identificación (CU)
        doc_raw = novedad.get("documento")
        doc_norm, doc_valido = normalize_document(doc_raw)
        if not doc_valido:
            errores.append("Documento ausente o inválido")

        # 2. Identificación de Tipo de Persona y Nombre
        razon_social_raw = novedad.get("razonSocial")
        razon_social_norm = normalize_razon_social(razon_social_raw)

        primer_nombre = novedad.get("primerNombre")
        segundo_nombre = novedad.get("segundoNombre")
        primer_apellido = novedad.get("primerApellido")
        segundo_apellido = novedad.get("segundoApellido")
        nombre_persona_norm = normalize_person_name(
            primer_nombre, segundo_nombre, primer_apellido, segundo_apellido
        )

        if razon_social_norm:
            tipo_persona = "JURIDICA"
            nombre_completo = razon_social_norm
        else:
            tipo_persona = "NATURAL"
            nombre_completo = nombre_persona_norm

        if not nombre_completo:
            errores.append("Nombre o razón social vacío")

        # Asignación de código CU (regla: CU-RE documento distinto -> cliente distinto)
        if doc_norm:
            codigo_cu = f"CU-{doc_norm}"
        else:
            codigo_cu = f"CU-ERR-{uuid.uuid4().hex[:8].upper()}"

        estado_cliente = clean_text(novedad.get("estadoCliente", "ACTIVO"))
        es_valido = len(errores) == 0

        cu_depurado = CUDepurado(
            codigo_cu=codigo_cu,
            documento_original=str(doc_raw) if doc_raw is not None else None,
            documento_normalizado=doc_norm,
            nombre_completo=nombre_completo,
            tipo_persona=tipo_persona,
            razon_social_normalizada=razon_social_norm,
            estado_cliente=estado_cliente,
            es_valido=es_valido,
            errores=errores,
        )

        # 3. Normalización de PVU (Dirección, Ciudad, Coordenadas H3)
        dir_norm = normalize_address(
            novedad.get("direccion"),
            novedad.get("direccion2"),
            novedad.get("direccion3"),
            novedad.get("direccion4"),
            novedad.get("direccion5"),
        )
        barrio_norm = clean_text(novedad.get("barrio"))
        ciudad_norm = clean_text(novedad.get("codigoCiudad", "0000"))
        depto_norm = clean_text(novedad.get("codigoDepartamento", "00"))
        pais_norm = clean_text(novedad.get("pais", "COLOMBIA"))
        negocio_norm = clean_text(novedad.get("nombreNegocio"))

        h3_cell = calculate_h3_index(
            lat=novedad.get("latitud"),
            lon=novedad.get("longitud"),
            fallback_key=f"{ciudad_norm}_{dir_norm}" if dir_norm else None,
        )

        # Generar código PVU basado en ciudad y dirección normalizada
        pvu_seed = f"{ciudad_norm}:{dir_norm}"
        pvu_hash = hashlib.sha256(pvu_seed.encode()).hexdigest()[:10].upper()
        codigo_pvu = f"PVU-{ciudad_norm}-{pvu_hash}"

        pvu_depurado = PVUDepurado(
            codigo_pvu=codigo_pvu,
            direccion_original=str(novedad.get("direccion", "")),
            direccion_normalizada=dir_norm,
            barrio_normalizado=barrio_norm,
            codigo_ciudad=ciudad_norm,
            codigo_departamento=depto_norm,
            pais=pais_norm,
            nombre_negocio=negocio_norm,
            h3_index=h3_cell,
        )

        # 4. Cálculo de Score de Calidad intrínseca (0-100)
        score_calidad = 0.0
        if doc_valido:
            score_calidad += 35.0
        if nombre_completo:
            score_calidad += 25.0
        if dir_norm:
            score_calidad += 20.0
        if ciudad_norm and ciudad_norm != "0000":
            score_calidad += 10.0
        if negocio_norm:
            score_calidad += 10.0

        # 5. Fuzzy Matching contra candidatos (Blocking)
        mejor_score_similitud = 0.0
        candidato_elegido = None
        matches_encontrados = False

        for cand in candidatos_existentes:
            cand_doc = cand.get("documentoNormalizado") or cand.get("documento_normalizado")
            # CU-RE: documento distinto -> cliente distinto (bloqueo estricto por documento)
            if doc_norm and cand_doc:
                if doc_norm == cand_doc:
                    # Coincidencia exacta de documento
                    score_nombre = fuzz.token_set_ratio(
                        nombre_completo,
                        cand.get("nombreCompleto") or cand.get("nombre_completo", "")
                    )
                    mejor_score_similitud = max(mejor_score_similitud, float(score_nombre))
                    candidato_elegido = cand.get("codigoCu") or cand.get("codigo_cu")
                    matches_encontrados = True
                continue

            # Si no hay documento en alguno de los dos, se compara nombre y dirección con RapidFuzz
            cand_nom = cand.get("nombreCompleto") or cand.get("nombre_completo", "")
            cand_dir = cand.get("direccionNormalizada") or cand.get("direccion_normalizada", "")
            
            sim_nom = fuzz.token_set_ratio(nombre_completo, cand_nom) if cand_nom else 0.0
            sim_dir = fuzz.token_set_ratio(dir_norm, cand_dir) if cand_dir else 0.0
            score_compuesto = (sim_nom * 0.6) + (sim_dir * 0.4)

            if score_compuesto >= self.umbral_similitud and score_compuesto > mejor_score_similitud:
                mejor_score_similitud = score_compuesto
                candidato_elegido = cand.get("codigoCu") or cand.get("codigo_cu")
                matches_encontrados = True

        es_nuevo = not matches_encontrados

        return ResultadoCalidad(
            cu=cu_depurado,
            pvu=pvu_depurado,
            score_calidad=score_calidad,
            score_similitud_candidato=mejor_score_similitud,
            es_nuevo_cliente=es_nuevo,
            matches_encontrados=matches_encontrados,
            candidato_asociado_id=candidato_elegido,
            bloqueo_keys={
                "cu_doc_key": doc_norm,
                "pvu_geo_key": f"{ciudad_norm}_{h3_cell or dir_norm}",
            },
        )
