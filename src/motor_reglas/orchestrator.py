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


class TriadaEgressError(RuntimeError):
    """La tríada no pudo persistirse en el Data Lake vía la Lambda Egress.

    Se propaga para que el consumidor SQS **no** elimine el mensaje y pueda
    reintentarse (o terminar en la DLQ) en lugar de perder la novedad.
    """


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

        try:
            return self._procesar_novedad(payload, huella)
        except Exception:
            # La novedad no quedó confirmada: se libera la huella para que el
            # reintento de SQS no se descarte como duplicado.
            if seen_cache is not None:
                seen_cache.discard(huella)
            raise

    def _procesar_novedad(self, payload: Dict[str, Any], huella: str) -> Dict[str, Any]:
        """Pipeline de 4 pasos para una novedad ya validada como no duplicada."""
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

    @staticmethod
    def _leer_respuesta_lambda(resp: Dict[str, Any]) -> Dict[str, Any]:
        """Valida la respuesta de ``lambda.invoke`` y extrae el cuerpo del handler.

        ``Invoke`` devuelve HTTP 200 aunque el handler haya fallado; por eso es
        imprescindible revisar ``FunctionError`` y el ``statusCode`` del payload.
        """
        if resp.get("FunctionError"):
            raise TriadaEgressError(f"La Lambda Egress reportó FunctionError: {resp['FunctionError']}")

        status_code = resp.get("StatusCode")
        if status_code not in (200, 202):
            raise TriadaEgressError(f"Lambda Egress devolvió StatusCode {status_code}")

        payload = resp.get("Payload")
        if payload is None:
            return {}

        raw = payload.read() if hasattr(payload, "read") else payload
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8")
        if not raw:
            return {}

        try:
            body = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError) as exc:
            raise TriadaEgressError(f"Respuesta inválida de Lambda Egress: {raw!r}") from exc

        if isinstance(body, dict):
            handler_status = int(body.get("statusCode", 200) or 200)
            if handler_status >= 400:
                detalle = body.get("body") or body.get("error") or body
                raise TriadaEgressError(f"Lambda Egress falló ({handler_status}): {detalle}")
        return body if isinstance(body, dict) else {"raw": body}

    def _enviar_a_lambda_egress(self, payload_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Invoca la Lambda Egress Orchestrator (Paso 4) para persistir en S3.

        Si la invocación remota falla se intenta el handler directo como
        *fallback* local. Si ambos fallan se lanza ``TriadaEgressError`` para
        que el mensaje SQS **no** se elimine y pueda reintentarse / ir a la DLQ.
        """
        error_remoto: Optional[Exception] = None

        # 1. Invocación real de la Lambda (evento del envío de la tríada).
        if self.lambda_client:
            try:
                resp = self.lambda_client.invoke(
                    FunctionName=self.lambda_egress_name,
                    InvocationType="RequestResponse",
                    Payload=json.dumps(payload_dict),
                )
                body = self._leer_respuesta_lambda(resp)
                return {
                    "status": "invocado_lambda",
                    "statusCode": resp.get("StatusCode"),
                    "lambda_statusCode": body.get("statusCode"),
                }
            except Exception as exc:  # noqa: BLE001 - se reintenta con fallback
                error_remoto = exc
                logger.warning(
                    "Error invocando Lambda %s: %s. Probando fallback directo.",
                    self.lambda_egress_name,
                    exc,
                )

        # 2. Fallback de invocación directa del módulo src/lambda_egress.
        try:
            from lambda_egress.lambda_function import lambda_handler

            resultado = lambda_handler(payload_dict, None)
            status = int(resultado.get("statusCode", 500) or 500)
            if status >= 400:
                raise TriadaEgressError(f"El handler de egress devolvió {status}: {resultado}")
            return {"status": "invocado_directo", "statusCode": status, "resultado": resultado}
        except Exception as exc:  # noqa: BLE001 - se reporta como fallo definitivo
            logger.error("Fallback de lambda_egress falló: %s", exc)
            raise TriadaEgressError(
                f"No se pudo persistir la tríada en el Data Lake: {exc}"
            ) from (error_remoto or exc)
