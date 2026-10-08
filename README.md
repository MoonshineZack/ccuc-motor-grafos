# Motor de Grafos CCUC — Laboratorio DevOps

Base de desarrollo para el motor de grafos CCUC (Código Cliente Único Comercial).
Cualquier desarrollador clona, levanta el laboratorio y en menos de 15 minutos
está corriendo pruebas. Spec completa: [`spec-laboratorio-devops-ccuc.md`](spec-laboratorio-devops-ccuc.md).

## Requisitos previos

- Docker + Docker Compose
- Python 3.12
- (opcional) AWS CLI v2 + [`awslocal`](https://github.com/localstack/awscli-local): `pip install awscli-local`
- (solo despliegue) Node.js 22+ para el CDK CLI (`npx aws-cdk`)

## Inicio rápido

```bash
git clone <repo> && cd ccuc-motor-grafos
docker-compose up -d          # levanta todo el laboratorio
docker-compose ps             # los 4 contenedores deben quedar (healthy)
```

Al arrancar, `scripts/init-aws.sh` crea solo (sin intervención manual):

- Cola `ccuc-novedades-prod.fifo` (dedup por contenido) + su DLQ `ccuc-novedades-dlq.fifo`
- Buckets S3 `ccuc-landing` y `ccuc-staging`

## Endpoints locales

| Servicio | URL |
|---|---|
| AWS emulado (LocalStack) | `http://localhost:4566` |
| Gremlin (simula Neptune) | `ws://localhost:8182/gremlin` |
| OpenSearch (sin seguridad, solo local) | `http://localhost:9200` |

## Flujo de desarrollo

1. Editar en `./src/motor_reglas/` o `./src/motor_calidad/` (hot-reloading: el cambio
   se refleja dentro del contenedor al instante, sin reconstruir).
2. Entrar al contenedor:
   ```bash
   docker exec -it dev-motor-reglas bash
   ```
3. Correr el consumidor:
   ```bash
   cd /app && python motor_reglas/main.py
   ```
4. Enviar una novedad de prueba (desde otro terminal):
   ```bash
   docker exec -it dev-localstack awslocal sqs send-message \
     --queue-url http://localhost:4566/000000000000/ccuc-novedades-prod.fifo \
     --message-body '{"cliente_a":"C-1","cliente_b":"C-2","estado":"A"}' \
     --message-group-id g1
   ```

## Pruebas (obligatorias antes de push)

```bash
docker exec -it dev-motor-reglas bash
cd /app && pytest ../tests/ -v
```

Dos pruebas son **críticas** y el pipeline las convierte en bloqueo de merge:

| Prueba | Archivo | Qué valida |
|---|---|---|
| Idempotencia | `tests/test_idempotency.py` | Los duplicados se descartan por `hashPayload` **antes** de tocar OpenSearch/Neptune (100%). |
| Tabla de verdad | `tests/test_truth_table.py` | Cliente en estado Inactivo ("I") → **NO** se crea la arista `:INTERSECTA`. |

> El equipo de desarrollo implementa la lógica real dentro de `src/motor_reglas/core.py`;
> la infraestructura para ejecutarlas ya está lista.

## Despliegue (CI/CD)

- **Merge a `develop`** → pipeline ejecuta `pytest` + `cdk synth` y despliega a AWS Dev/QA.
  Los desarrolladores **no** despliegan a mano.
- Configuración única en GitHub:
  1. Secrets del job `deploy`: `AWS_ROLE_ARN` (rol OIDC) y `AWS_REGION`.
  2. Branch protection en `develop`: requerir los checks `Pruebas críticas` y `cdk synth`
     (esto es lo que **bloquea el merge** si una prueba falla).

Infraestructura como código en [`infra/`](infra/) — despliegue manual (solo DevOps):

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r infra/requirements.txt
cd infra && npx aws-cdk@2 synth   # valida; npx aws-cdk@2 deploy para desplegar
```

## FinOps: horario y apagado automático

| Medida | Detalle |
|---|---|
| Horario hábil | **07:00 – 20:00 COT** (lunes a viernes) |
| Apagado | Regla EventBridge `cron(0 1 ? * TUE-SAT *)` = 01:00 UTC = **20:00 COT** |
| Encendido | Regla EventBridge `cron(0 12 ? * MON-FRI *)` = 12:00 UTC = **07:00 COT** |
| Lambda `ccuc-ciclo-vida-devqa` | **stop**: escala ECS a 0, fija Neptune en 0.5 ACU, destruye el NAT Gateway. **start**: recrea el NAT (mismo Elastic IP), restaura Neptune (0.5–4 ACU), escala ECS a 1. |
| Fargate | Spot al 100%, mínimo 1 tarea en horario hábil. |

### Estimación de costos (meta: ≤ ~USD 91/mes)

| Recurso | Supuesto | USD/mes (aprox.) |
|---|---|---|
| OpenSearch t3.small.search ×1 | 24/7 | ~26 |
| Neptune Serverless | 0.5 ACU de noche, ~1 ACU promedio de día | ~25 |
| NAT Gateway | Solo horario hábil (~260 h) + tráfico bajo | ~14 |
| Fargate Spot (0.25 vCPU / 512 MB) | Solo horario hábil | ~2 |
| Lambda, EventBridge, SQS, S3, CloudWatch, EIP | — | ~4 |
| **Total** | | **~71** (margen ~20 frente a la meta de 91) |

> Estimación orientativa de la fase de estimación; adjuntar el desglose real de la
> consola de AWS en el DoD.

## Diferencias local vs. nube (conocidas)

- **Gremlin Server ≠ Neptune**: mismo protocolo TinkerPop, pero Neptune añade límites,
  IAM y compatibilidad propia. Lo que pase local *puede* comportarse distinto en AWS.
- **OpenSearch local** es single-node sin seguridad; en AWS es un Domain gestionado
  dentro de la VPC (con seguridad de red). Sin FGAC en Dev/QA (ver preguntas abiertas).
- **Deduplicación SQS**: la nativa de la cola solo cubre una ventana de **5 minutos**.
  La idempotencia real con `hashPayload` en la aplicación es obligatoria
  (`src/motor_reglas/core.py`).

## Preguntas abiertas (propuestas de la estimación — se confirman con Producto)

1. **Encendido:** implementado con una segunda regla EventBridge (`cron(0 12 ? * MON-FRI *)`)
   hacia la misma Lambda `ccuc-ciclo-vida-devqa`. ✅ resuelto en este repo.
2. **Fines de semana:** asumido apagado (el cron de encendido es lun–vie). Confirmar.
3. **Diferencias local vs. nube:** ver sección anterior. Pedir nota formal al equipo si aplica.
4. **Deduplicación:** resuelto con `hashPayload` en la app + dedup de contenido en la cola.
5. **Nombre de la cola:** se mantiene `ccuc-novedades-prod.fifo` en todos los ambientes
   para que el código sea idéntico entre local y AWS (el ambiente lo define el endpoint,
   no el nombre). Alternativa: parametrizar con `QUEUE_NAME` (ya soportado por compose/CDK).
6. **Seguridad Dev/QA:** OpenSearch y Neptune solo en subredes privadas con SG propias
   (acceso desde la VPC); sin FGAC en OpenSearch. Si Producto exige más, agregar
   fine-grained access control + Cognito — fuera del costo estimado.
7. **CLI local:** es `awslocal` (wrapper oficial de LocalStack). Instalar con
   `pip install awscli-local` (requiere AWS CLI v2).

> ⚠️ **Riesgo conocido:** destruir/recrear el NAT Gateway fuera de CloudFormation genera
> *drift* en la VPC; el primer `cdk deploy` posterior puede requerir
> `npx aws-cdk@2 deploy --force`. Alternativa futura: VPC endpoints (SQS/S3/EC2/SSM)
> para eliminar el NAT por completo.

## Estructura del repositorio

```
ccuc-motor-grafos/
├── infra/                    # Todo el código CDK (IaC)
│   ├── app.py                # Stack Dev/QA completo
│   └── lambdas/              # ciclo de vida (FinOps) + orquestador
├── src/                      # Microservicios
│   ├── motor_reglas/         # consumidor SQS + core de reglas
│   └── motor_calidad/
├── tests/                    # Pruebas (pytest) — bloquean merge
├── scripts/
│   └── init-aws.sh           # Bootstrap de LocalStack
├── docker-compose.yml
├── .github/workflows/ci.yml  # CI/CD
└── README.md
```
