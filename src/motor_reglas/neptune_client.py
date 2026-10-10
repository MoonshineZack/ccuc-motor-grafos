"""Cliente de conexión e interacción con Amazon Neptune Serverless / Gremlin Server."""
import logging
import os
import socket
import urllib.parse
from typing import Dict, Any, List, Optional

logger = logging.getLogger("ccuc.neptune")

try:
    from gremlin_python.driver.driver_remote_connection import DriverRemoteConnection
    from gremlin_python.process.anonymous_traversal import traversal
    from gremlin_python.process.graph_traversal import __
    GREMLIN_AVAILABLE = True
except ImportError:
    GREMLIN_AVAILABLE = False


def _check_host_port(host: str, port: int, timeout_sec: float = 0.5) -> bool:
    """Verifica si el puerto del host está escuchando conexiones TCP."""
    try:
        with socket.create_connection((host, port), timeout=timeout_sec):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


class NeptuneGremlinClient:
    """Gestiona operaciones sobre la base de datos de grafos CCUC usando Gremlin API."""

    def __init__(
        self,
        endpoint_url: Optional[str] = None,
        use_mock_fallback: bool = True,
        force_mock: bool = False
    ):
        self.endpoint_url = endpoint_url or self._resolve_endpoint()
        self.use_mock_fallback = use_mock_fallback
        self.force_mock = force_mock
        self.g = None
        self.connection = None
        self.is_connected = False
        
        # Almacenamiento en memoria para modo offline / pruebas unitarias
        self._mock_vertices: Dict[str, Dict[str, Any]] = {}
        self._mock_edges: List[Dict[str, Any]] = []

        if not self.force_mock:
            self._connect()

    def _resolve_endpoint(self) -> str:
        """Determina la URL del servidor Gremlin según el entorno."""
        env_url = os.environ.get("GREMLIN_ENDPOINT")
        if env_url:
            return env_url
        
        host = os.environ.get("GREMLIN_HOST", "dev-gremlin")
        port = os.environ.get("GREMLIN_PORT", "8182")
        protocol = "wss" if "amazonaws.com" in host else "ws"
        return f"{protocol}://{host}:{port}/gremlin"

    def _connect(self):
        """Intenta inicializar la conexión con Gremlin Server si el endpoint está disponible."""
        if not GREMLIN_AVAILABLE:
            logger.info("gremlinpython no disponible; operando en modo simulación de grafo.")
            return

        # Análisis rápido de host y puerto para evitar bloqueos por timeout
        try:
            parsed = urllib.parse.urlparse(self.endpoint_url)
            host = parsed.hostname or "localhost"
            port = parsed.port or 8182
            if not _check_host_port(host, port, timeout_sec=0.3):
                logger.info(f"Gremlin Server en {host}:{port} no responde; usando simulación en memoria.")
                return
        except Exception:
            pass

        try:
            self.connection = DriverRemoteConnection(self.endpoint_url, 'g')
            self.g = traversal().withRemote(self.connection)
            self.is_connected = True
            logger.info(f"Conexión exitosa a Gremlin Server: {self.endpoint_url}")
        except Exception as e:
            logger.warning(
                f"No se pudo conectar a Gremlin ({self.endpoint_url}): {e}. "
                "Activando fallback local simulado."
            )
            self.is_connected = False
            self.g = None

    def close(self):
        """Cierra la conexión con el servidor Gremlin."""
        if self.connection:
            try:
                self.connection.close()
            except Exception:
                pass
            self.is_connected = False

    def buscar_candidatos_cu(self, documento_normalizado: str) -> List[Dict[str, Any]]:
        """
        Búsqueda exacta de candidatos CU indexados por documentoNormalizado.
        Regla: Generación de candidatos (blocking) sin motor de búsqueda.
        """
        if not documento_normalizado:
            return []

        if self.is_connected and self.g:
            try:
                t = self.g.V().hasLabel("Cliente").has("documentoNormalizado", documento_normalizado)
                t = t.valueMap(True) if hasattr(t, "valueMap") else t.value_map(True)
                resultados = t.toList() if hasattr(t, "toList") else t.to_list()
                
                candidatos = []
                for res in resultados:
                    candidato = {}
                    for k, v in res.items():
                        candidato[str(k)] = v[0] if isinstance(v, list) and v else v
                    candidatos.append(candidato)
                return candidatos
            except Exception as e:
                logger.error(f"Error consultando candidatos en Neptune: {e}")

        # Fallback en memoria
        candidatos = []
        for v in self._mock_vertices.values():
            if v.get("label") == "Cliente" and v.get("documentoNormalizado") == documento_normalizado:
                candidatos.append(dict(v))
        return candidatos

    def upsert_cliente(
        self,
        codigo_cu: str,
        documento_normalizado: str,
        nombre_completo: str,
        tipo_persona: str,
        estado: str
    ) -> str:
        """Crea o actualiza el vértice de Cliente Único (CU) en el grafo."""
        if self.is_connected and self.g:
            try:
                t = self.g.V().hasLabel("Cliente").has("documentoNormalizado", documento_normalizado)
                traversal_check = t.toList() if hasattr(t, "toList") else t.to_list()
                if traversal_check:
                    (
                        self.g.V(traversal_check[0])
                        .property("nombreCompleto", nombre_completo)
                        .property("estado", estado)
                        .iterate()
                    )
                    return codigo_cu
                else:
                    (
                        self.g.addV("Cliente")
                        .property("codigoCu", codigo_cu)
                        .property("documentoNormalizado", documento_normalizado)
                        .property("nombreCompleto", nombre_completo)
                        .property("tipoPersona", tipo_persona)
                        .property("estado", estado)
                        .iterate()
                    )
                    return codigo_cu
            except Exception as e:
                logger.error(f"Error en upsert_cliente en Neptune: {e}")

        # Fallback en memoria
        vid = f"Cliente_{documento_normalizado or codigo_cu}"
        self._mock_vertices[vid] = {
            "id": vid,
            "label": "Cliente",
            "codigoCu": codigo_cu,
            "documentoNormalizado": documento_normalizado,
            "nombreCompleto": nombre_completo,
            "tipoPersona": tipo_persona,
            "estado": estado,
        }
        return codigo_cu

    def upsert_pvu(
        self,
        codigo_pvu: str,
        direccion_normalizada: str,
        codigo_ciudad: str,
        nombre_negocio: str,
        h3_index: Optional[str] = None
    ) -> str:
        """Crea o actualiza el vértice de Punto de Venta Único (PVU)."""
        if self.is_connected and self.g:
            try:
                t = self.g.V().hasLabel("PVU").has("codigoPvu", codigo_pvu)
                check = t.toList() if hasattr(t, "toList") else t.to_list()
                if not check:
                    (
                        self.g.addV("PVU")
                        .property("codigoPvu", codigo_pvu)
                        .property("direccionNormalizada", direccion_normalizada)
                        .property("codigoCiudad", codigo_ciudad)
                        .property("nombreNegocio", nombre_negocio)
                        .property("h3Index", h3_index or "")
                        .iterate()
                    )
                return codigo_pvu
            except Exception as e:
                logger.error(f"Error en upsert_pvu en Neptune: {e}")

        # Fallback en memoria
        vid = f"PVU_{codigo_pvu}"
        self._mock_vertices[vid] = {
            "id": vid,
            "label": "PVU",
            "codigoPvu": codigo_pvu,
            "direccionNormalizada": direccion_normalizada,
            "codigoCiudad": codigo_ciudad,
            "nombreNegocio": nombre_negocio,
            "h3Index": h3_index or "",
        }
        return codigo_pvu

    def vincular_cliente_pvu(self, codigo_cu: str, codigo_pvu: str):
        """Crea la arista :TIENE_PVU entre el Cliente y el Punto de Venta."""
        if self.is_connected and self.g:
            try:
                (
                    self.g.V()
                    .hasLabel("Cliente")
                    .has("codigoCu", codigo_cu)
                    .as_("cu")
                    .V()
                    .hasLabel("PVU")
                    .has("codigoPvu", codigo_pvu)
                    .coalesce(
                        __.inE("TIENE_PVU").where(__.outV().as_("cu")),
                        __.addE("TIENE_PVU").from_("cu")
                    )
                    .iterate()
                )
                return
            except Exception as e:
                logger.error(f"Error vinculando Cliente con PVU: {e}")

        # Fallback
        self._mock_edges.append({
            "from": codigo_cu,
            "to": codigo_pvu,
            "label": "TIENE_PVU",
        })

    def crear_relacion_intersecta(
        self,
        codigo_cu_origen: str,
        codigo_cu_destino: str,
        activo: bool = True
    ) -> bool:
        """
        Crea la arista :INTERSECTA entre dos clientes.
        Regla de negocio crítica: Si el cliente está inactivo (activo=False),
        NO debe crear la arista :INTERSECTA.
        """
        if not activo:
            logger.info("Cliente inactivo: regla impide crear arista :INTERSECTA.")
            return False

        if codigo_cu_origen == codigo_cu_destino:
            return False

        if self.is_connected and self.g:
            try:
                (
                    self.g.V()
                    .hasLabel("Cliente")
                    .has("codigoCu", codigo_cu_origen)
                    .as_("origen")
                    .V()
                    .hasLabel("Cliente")
                    .has("codigoCu", codigo_cu_destino)
                    .coalesce(
                        __.inE("INTERSECTA").where(__.outV().as_("origen")),
                        __.addE("INTERSECTA").from_("origen")
                    )
                    .iterate()
                )
                return True
            except Exception as e:
                logger.error(f"Error creando arista :INTERSECTA en Neptune: {e}")

        self._mock_edges.append({
            "from": codigo_cu_origen,
            "to": codigo_cu_destino,
            "label": "INTERSECTA",
        })
        return True
