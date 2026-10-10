"""Orquestador de ECS Fargate: Motor de Reglas y Pipeline CCUC.

Ejecuta el flujo de 4 pasos de la arquitectura:
1. Recepción y deduplicación (Long Polling SQS / hashPayload).
2. Invocación a Motor de Calidad CU/PVU (RapidFuzz).
3. Comunicación vía Gremlin API a Neptune Serverless (Motor de Grafos).
4. Envío de Payload Tríada a AWS Lambda (Egress Orchestrator).
"""
import datetime
import json
import logging
import os
import boto3
from typing import Dict, Any, Optional

from motor_calidad.fuzzy_engine import MotorCalidadCUPVU
from motor_calidad.models import ResultadoCalidad, PayloadTriada
from motor_reglas.core import hash_payload, can_intersect
from motor_reglas.neptune_client import NeptuneGremlinClient

logger = logging.getLogger("ccuc.orchestrator")


class CCUCOrchestrator:
    """Motor de orquestación y reglas ejecutado en ECS Fargate."""

    def __init__(
        self,
        neptune_client: Optional[NeptuneGremlinClient] = None,
        calidad_engine: Optional[MotorCalidadCUPVU] = None,
        lambda_client: Optional[Any] = None,
        lambda_egress_name: Optional[str] = None
    ):
        self.neptune = neptune_client or NeptuneGremlinClient()
        self.calidad_engine = calidad_engine or MotorCalidadCUPVU()
        self.lambda_egress_name = lambda_egress_name or os.environ.get(
            "LAMBDA_EGRESS_NAME", "ccuc-egress-orchestrator"
        )
        self.lambda_client = lambda_client or self._init_lambda_client()

    def _init_lambda_client(self):
        """Inicializa cliente boto3 para invocar la Lambda Egress Orchestrator."""
        endpoint = os.environ.get("ENDPOINT_URL") or None
        region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
        try:
            return boto3.client("lambda", endpoint_url=endpoint, region_name=region)
        except Exception as e:
            logger.warning(f"No se pudo inicializar boto3 lambda: {e}")
            return None

    def procesar_novedad(
        self,
        payload: Dict[str, Any],
        seen_cache: Optional[set] = None
    ) -> Dict[str, Any]:
        """
        Ejecuta el pipeline completo de procesamiento para una novedad.
        Retorna un resumen con el estado y los identificadores de la tríada resuelta.
        """
        # =====================================================================
        # PASO 1: Control de Idempotencia y Deduplicación por hashPayload
        # =====================================================================
        huella = hash_payload(payload)
        if seen_cache is not None:
            if huella in seen_cache:
                logger.info(f"[Paso 1] Duplicado detectado (hash: {huella[:8]}). Descartando.")
                return {"status": "duplicado", "hash": huella}
            seen_cache.add(huella)

        logger.info(f"[Paso 1] Mensaje válido recibido (hash: {huella[:8]}).")

        # =====================================================================
        # PASO 2: Invocación al Motor de Calidad CU/PVU (RapidFuzz)
        # =====================================================================
        # Consultar candidatos bloqueados en Neptune para RapidFuzz
        doc_raw = payload.get("documento")
        candidatos = []
        if doc_raw:
            doc_limpio = str(doc_raw).replace(".", "").replace("-", "").strip()
            candidatos = self.neptune.buscar_candidatos_cu(doc_limpio)

        resultado_calidad: ResultadoCalidad = self.calidad_engine.depurar_novedad(
            novedad=payload,
            candidatos_existentes=candidatos
        )
        cu = resultado_calidad.cu
        pvu = resultado_calidad.pvu

        logger.info(
            f"[Paso 2] Fuzzy Matching completado - CU: {cu.codigo_cu} | PVU: {pvu.codigo_pvu} "
            f"| Calidad: {resultado_calidad.score_calidad}% | Similitud: {resultado_calidad.score_similitud_candidato}%"
        )

        # =====================================================================
        # PASO 3: Gremlin API -> Amazon Neptune Serverless (Motor de Grafos)
        # =====================================================================
        # 3.1 Upsert Vértice Cliente
        cu_id = self.neptune.upsert_cliente(
            codigo_cu=cu.codigo_cu,
            documento_normalizado=cu.documento_normalizado,
            nombre_completo=cu.nombre_completo,
            tipo_persona=cu.tipo_persona,
            estado=cu.estado_cliente,
        )

        # 3.2 Upsert Vértice PVU
        pvu_id = self.neptune.upsert_pvu(
            codigo_pvu=pvu.codigo_pvu,
            direccion_normalizada=pvu.direccion_normalizada,
            codigo_ciudad=pvu.codigo_ciudad,
            nombre_negocio=pvu.nombre_negocio,
            h3_index=pvu.h3_index,
        )

        # 3.3 Relacionar Cliente con PVU (:TIENE_PVU)
        self.neptune.vincular_cliente_pvu(cu_id, pvu_id)

        # 3.4 Regla de Intersección (:INTERSECTA)
        # Tabla de verdad: estado "I" (Inactivo) NO crea arista :INTERSECTA
        es_activo = can_intersect(payload.get("estado", "")) and can_intersect(payload.get("estadoCliente", ""))
        
        arista_creada = False
        if resultado_calidad.candidato_asociado_id and es_activo:
            arista_creada = self.neptune.crear_relacion_intersecta(
                codigo_cu_origen=cu.codigo_cu,
                codigo_cu_destino=resultado_calidad.candidato_asociado_id,
                activo=True
            )

        logger.info(
            f"[Paso 3] Neptune Gremlin actualizado - Vértices [CU: {cu_id}, PVU: {pvu_id}] "
            f"| Intersecta: {arista_creada} (Activo: {es_activo})"
        )

        # =====================================================================
        # PASO 4: Construcción y Envío de Payload Tríada -> AWS Lambda Egress
        # =====================================================================
        timestamp_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        golden_record_id = cu.codigo_cu  # Derivado según regla de negocio

        triada = PayloadTriada(
            codigo_cu=cu.codigo_cu,
            codigo_pvu=pvu.codigo_pvu,
            golden_record_id=golden_record_id,
            matches_found=resultado_calidad.matches_encontrados,
            score_calidad=resultado_calidad.score_calidad,
            datos_consolidados={
                "nombre": cu.nombre_completo,
                "documentoNormalizado": cu.documento_normalizado,
                "tipoPersona": cu.tipo_persona,
                "direccionNormalizada": pvu.direccion_normalizada,
                "codigoCiudad": pvu.codigo_ciudad,
                "h3Index": pvu.h3_index,
                "estadoCliente": cu.estado_cliente,
            },
            grafo_metadata={
                "cu_vertex": cu_id,
                "pvu_vertex": pvu_id,
                "arista_intersecta": arista_creada,
                "hash_origen": huella,
            },
            timestamp=timestamp_iso,
        )

        egress_resp = self._enviar_a_lambda_egress(triada.to_dict())
        logger.info(f"[Paso 4] Tríada resuelta enviada a Lambda Egress: {egress_resp}")

        return {
            "status": "procesado",
            "codigoCu": cu.codigo_cu,
            "codigoPvu": pvu.codigo_pvu,
            "score_calidad": resultado_calidad.score_calidad,
            "egress_status": egress_resp.get("status"),
        }

    def _enviar_a_lambda_egress(self, payload_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Invoca la Lambda Egress Orchestrator para persistir en S3 Data Lake."""
        # 1. Intento por boto3 Lambda
        if self.lambda_client:
            try:
                resp = self.lambda_client.invoke(
                    FunctionName=self.lambda_egress_name,
                    InvocationType="RequestResponse",
                    Payload=json.dumps(payload_dict)
                )
                return {"status": "invocado_lambda", "statusCode": resp.get("StatusCode")}
            except Exception as e:
                logger.warning(f"Error invocando Lambda vía boto3: {e}. Probando fallback local.")

        # 2. Fallback de invocación directa del módulo src/lambda_egress
        try:
            from lambda_egress.lambda_function import lambda_handler
            resultado = lambda_handler(payload_dict, None)
            return {"status": "invocado_directo", "resultado": resultado}
        except Exception as ex:
            logger.error(f"Fallback de lambda_egress falló: {ex}")
            return {"status": "error_egress", "error": str(ex)}
