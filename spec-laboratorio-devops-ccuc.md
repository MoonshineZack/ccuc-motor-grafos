# Spec para DevOps: Laboratorio Local y Entorno AWS Dev/QA (Motor de Grafos CCUC)

| Campo | Valor |
|---|---|
| **Proyecto** | Motor de Grafos CCUC (Código Cliente Único Comercial) |
| **Solicitante** | Producto (PPO) |
| **Destinatario** | Ingeniería DevOps |
| **Estado** | Para revisión y estimación |
| **Fecha** | 2026-10-08 |

---

## 1. ¿Para qué es esto? (léelo primero)

Los desarrolladores van a construir un algoritmo que recibe novedades de clientes (por una cola), decide si dos clientes son "el mismo cliente comercial" y lo guarda en una base de datos de grafos.

Para que puedan programar **sin depender de la nube** (más rápido, gratis y sin romper nada compartido), necesitamos de ti dos cosas:

1. **Un laboratorio local**: un solo comando (`docker-compose up -d`) que levante en el computador del desarrollador copias simuladas de los servicios de AWS y de las bases de datos.
2. **Un entorno AWS Dev/QA barato**: la infraestructura real desplegada con código (CDK), con costo controlado (~USD 91/mes) y apagado automático fuera de horario.

**Resultado esperado:** un repositorio base donde cualquier desarrollador clona, levanta el laboratorio y en menos de 15 minutos está corriendo pruebas.

---

## 2. Glosario rápido (para no perderse)

| Término | Qué es, en simple |
|---|---|
| **IaC** (Infraestructura como Código) | Crear la infraestructura escribiendo código, no haciendo clic en la consola de AWS. |
| **AWS CDK v2** | Herramienta para escribir IaC usando un lenguaje de programación (aquí, Python). |
| **Stack (CDK)** | Un grupo de recursos AWS que se despliegan juntos. |
| **SQS FIFO** | Cola de mensajes que respeta el orden y evita duplicados. |
| **DLQ** (Dead Letter Queue) | Cola "de rechazados": a donde van los mensajes que fallaron varias veces para revisarlos después. |
| **ECS Fargate** | Forma de correr contenedores Docker en AWS sin administrar servidores. |
| **Fargate Spot** | Lo mismo, pero con capacidad "sobrante" de AWS: hasta ~70% más barato, pero AWS puede interrumpirlo. Está bien para Dev/QA. |
| **Neptune** | Base de datos de grafos de AWS (nodos y relaciones). |
| **Gremlin** | Lenguaje para consultar grafos. |
| **OpenSearch** | Motor de búsqueda (texto, coincidencias aproximadas). |
| **LocalStack** | Programa que imita servicios AWS (SQS, S3, EventBridge…) en tu máquina. |
| **Idempotencia** | Procesar el mismo mensaje 1 o 5 veces debe dar el mismo resultado. |
| **hashPayload** | Una "huella digital" del contenido del mensaje; si dos mensajes tienen la misma huella, son duplicados. |
| **Hot-reloading** | Cambiar código en tu computador y verlo reflejado de inmediato dentro del contenedor, sin reconstruir la imagen. |
| **FinOps** | Disciplina de controlar y optimizar costos en la nube. |
| **NAT Gateway** | Recurso que da salida a internet a servicios en subredes privadas. Cobra por hora aunque no se use. |
| **DoD** (Definition of Done) | Lista de condiciones que deben cumplirse para considerar el trabajo terminado. |

---

## 3. Vista general (cómo encaja todo)

```
        LOCAL (laptop del dev)                          AWS DEV/QA (real)
 ┌─────────────────────────────────┐          ┌──────────────────────────────────┐
 │ LocalStack  :4566               │  imita   │ SQS FIFO + DLQ, S3, EventBridge  │
 │ (SQS, S3, EventBridge)          │ ───────► │                                  │
 │ Gremlin Server :8182            │  imita   │ Amazon Neptune Serverless        │
 │ OpenSearch single-node :9200    │  imita   │ OpenSearch                       │
 │ dev-motor-reglas (código ./src) │  corre   │ ECS Fargate Spot (microservicios)│
 └─────────────────────────────────┘          │ Lambdas de orquestación          │
                                              │ VPC (+ NAT Gateway)              │
                                              └──────────────────────────────────┘
```

**Flujo funcional (para entender qué estás habilitando):**
mensaje de novedad → cola SQS FIFO → microservicio (`motor-reglas`) calcula `hashPayload` → si es duplicado, se descarta → si no, consulta OpenSearch / Neptune → si las reglas lo permiten, crea la relación `:INTERSECTA` entre clientes en el grafo.

---

## 4. Alcance

### Dentro del alcance
- Repositorio base con la estructura y el stack definidos.
- Código CDK de la infraestructura AWS Dev/QA.
- Laboratorio local con Docker Compose.
- Script de arranque automático de LocalStack.
- Esqueleto de pruebas (pytest) y ejecución en el contenedor de desarrollo.
- Apagado automático nocturno del ambiente Dev/QA.
- Pipeline CI/CD que despliega al hacer *merge* a `develop`.
- README para desarrolladores.

### Fuera del alcance
- La lógica del algoritmo CCUC (reglas, cálculo del grafo): es trabajo del equipo de desarrollo.
- Ambientes de Producción.
- Carga de datos reales.

---

## 5. Entregables y requisitos

### 5.1 Repositorio base

**Stack congelado (no cambiar sin aprobación de Producto):**
- AWS CDK **v2 (2.1128.1)**
- Python **3.12.9**

**Estructura obligatoria de carpetas:**

```
ccuc-motor-grafos/
├── infra/                 # Todo el código CDK (IaC)
├── src/                   # Microservicios
│   ├── motor_reglas/
│   └── motor_calidad/
├── tests/                 # Pruebas (pytest)
├── scripts/
│   └── init-aws.sh        # Bootstrap de LocalStack
├── docker-compose.yml
└── README.md
```

**Qué debe crear la IaC (en `/infra`):**

| Recurso | Detalle |
|---|---|
| VPC | Red base del ambiente |
| SQS FIFO | Con **deduplicación basada en contenido** y su **DLQ** |
| ECS Fargate | Clúster para los microservicios |
| Neptune Serverless | Base de grafos |
| OpenSearch | Búsqueda |
| Lambdas de orquestación | Incluye la de ciclo de vida (ver 5.4) |

**Cómo saber que está bien:** `cdk synth` corre sin errores y `cdk deploy` crea el ambiente completo en Dev/QA.

---

### 5.2 Laboratorio local (emulación)

Un único `docker-compose.yml` que levante todo **sin instalar bases de datos de forma nativa** en la máquina.

| Servicio | Imagen / tecnología | Puerto | Notas |
|---|---|---|---|
| Servicios AWS | `localstack/localstack` | `4566` | SQS, S3, EventBridge |
| Grafos | TinkerPop **Gremlin Server** | `8182` | Simula Neptune. Endpoint: `ws://localhost:8182/gremlin` |
| Búsqueda | OpenSearch **single-node** | `9200` | **Seguridad desactivada** (solo local) |
| Microservicio | `dev-motor-reglas` | — | Con volumen `./src:/app` (hot-reloading) |

**Bootstrap automático (`scripts/init-aws.sh`):**
Al arrancar, LocalStack debe ejecutar este script y crear solo:
- Cola `ccuc-novedades-prod.fifo`
- Su DLQ correspondiente
- Buckets S3: **landing** y **staging**

> **Por qué importa:** si el dev tiene que crear colas a mano cada vez, perdemos tiempo y cada quien termina con un entorno distinto. El script hace que todos tengan lo mismo.

**Hot-reloading:** el contenedor `dev-motor-reglas` monta `./src:/app`. El desarrollador edita en su IDE y el cambio se ve dentro del contenedor sin reconstruir la imagen.

**Cómo saber que está bien (checklist de verificación):**
1. `docker-compose up -d` en una máquina limpia termina sin errores.
2. `docker ps` muestra todos los contenedores *healthy*.
3. La cola FIFO, la DLQ y los dos buckets existen sin intervención manual.
4. `curl http://localhost:9200` responde.
5. Editar un archivo en `./src` se refleja dentro del contenedor al instante.

---

### 5.3 Pruebas automatizadas

DevOps no escribe la lógica de negocio, pero **debe dejar listo** el entorno donde estas pruebas corren y fallan si algo se rompe. Se ejecutan con:

```bash
docker exec -it dev-motor-reglas bash
pytest ../tests/ -v
```

Dos pruebas son **críticas** y deben existir como mínimo (el equipo dev las implementa; DevOps garantiza que corren en el contenedor y en el pipeline):

| Prueba | Qué valida | Archivo sugerido |
|---|---|---|
| **Idempotencia** | Se inyectan mensajes duplicados y el cálculo de `hashPayload` descarta el **100%** de los duplicados **antes** de llamar a OpenSearch o Neptune. | `tests/test_idempotency.py` |
| **Tabla de verdad** | Si el cliente está en estado **Inactivo ("I")**, el algoritmo **NO** crea la arista `:INTERSECTA`. | `tests/test_truth_table.py` |

> **Importante:** el pipeline CI debe **bloquear el merge** si alguna de estas dos pruebas falla.

---

### 5.4 FinOps: costos y apagado automático (AWS Dev/QA)

**Meta de costo:** ~**USD 91/mes** para Dev/QA.

| Medida | Requisito |
|---|---|
| Fargate Spot | Usar Spot con **mínimo 1 tarea** (ahorro hasta ~70%) |
| Apagado automático | Regla en **EventBridge** con `cron(0 1 ? * TUE-SAT *)` |
| Lambda de ciclo de vida | `ccuc-ciclo-vida-devqa`: **reduce la capacidad de Neptune** y **destruye el NAT Gateway** fuera de horario hábil |

> **Ojo con la zona horaria:** EventBridge usa **UTC**. `01:00 UTC` = `20:00 COT` (Colombia, UTC-5). Por eso el cron dice martes–sábado: es el lunes–viernes a las 8 pm hora Colombia. Horario hábil del ambiente: **07:00 a 20:00 COT**.

---

### 5.5 Despliegue (CI/CD)

- El despliegue a AWS Dev/QA lo hace **automáticamente el pipeline** al hacer *merge* a la rama `develop`.
- Los desarrolladores **no** despliegan a mano.

---

## 6. Definition of Done (checklist final)

Marca cada punto solo cuando se pueda **demostrar**, no cuando "debería funcionar".

### Repositorio y arquitectura
- [ ] Proyecto inicializado con **AWS CDK v2 (2.1128.1)** y **Python 3.12.9**
- [ ] Carpetas `/infra`, `/src`, `/tests` separadas
- [ ] CDK define VPC, SQS FIFO (dedup por contenido + DLQ), ECS Fargate, Neptune Serverless, OpenSearch y Lambdas de orquestación

### Laboratorio local
- [ ] `docker-compose.yml` levanta todo sin instalaciones nativas de BD
- [ ] LocalStack expuesto en `4566`
- [ ] `init-aws.sh` crea automáticamente `ccuc-novedades-prod.fifo`, su DLQ y buckets S3 landing y staging
- [ ] Gremlin Server en `8182` y OpenSearch single-node (sin seguridad) en `9200`
- [ ] `dev-motor-reglas` con volumen `./src:/app` (hot-reloading funcionando)

### Pruebas
- [ ] Test de idempotencia (pytest) existe y pasa
- [ ] Test de tabla de verdad (cliente "I" → sin `:INTERSECTA`) existe y pasa
- [ ] Ambos corren en el pipeline y bloquean el merge si fallan

### FinOps
- [ ] Fargate Spot con 1 tarea mínima
- [ ] Regla EventBridge `cron(0 1 ? * TUE-SAT *)` activa
- [ ] Lambda `ccuc-ciclo-vida-devqa` reduce Neptune y destruye NAT Gateway fuera de horario
- [ ] Costo mensual estimado ≤ ~USD 91 (adjuntar estimación)

### Documentación
- [ ] `README.md` en la raíz, validado por un desarrollador que no participó en el montaje (prueba de "máquina limpia")

---

## 7. Puntos a aclarar antes de empezar (preguntas abiertas)

Estos puntos **no están definidos** en el requerimiento original. Por favor respóndelos o propón una opción en la estimación:

1. **Encendido:** el documento define el apagado (20:00 COT) pero **no el encendido**. ¿Quién/qué recrea el NAT Gateway y restaura la capacidad de Neptune a las 07:00 COT? Proponemos una segunda regla EventBridge hacia la misma Lambda.
2. **Fines de semana:** asumimos que el ambiente permanece apagado sábado y domingo. ¿Se confirma?
3. **Diferencias local vs. nube:** Gremlin Server no es Neptune, y OpenSearch single-node no es el servicio gestionado (el README menciona "OpenSearch Serverless", pero el local es single-node). Lo que pasa local **puede comportarse distinto** en AWS. Pedimos una nota breve con las diferencias conocidas.
4. **Deduplicación SQS FIFO:** la deduplicación nativa de SQS solo cubre una ventana de **5 minutos**. Por eso la idempotencia con `hashPayload` en la aplicación es obligatoria y no se debe depender solo de la cola.
5. **Nombre de la cola:** `ccuc-novedades-prod.fifo` contiene "prod" aunque sea para el laboratorio. ¿Se mantiene así (para que el código sea idéntico entre ambientes) o se parametriza por ambiente?
6. **Seguridad en Dev/QA:** OpenSearch local va sin seguridad (aceptable solo en local). Para AWS Dev/QA, ¿qué política de acceso aplicamos (VPC privada, IAM)?
7. **Herramienta CLI local:** el README menciona un plugin `aws-local`; confirmar si es `awslocal` (wrapper oficial de LocalStack) y dejar la instalación documentada.

---

## 8. Cómo se acepta el trabajo

1. DevOps hace una **demo en vivo**: máquina limpia → `git clone` → `docker-compose up -d` → colas y buckets creados → `pytest` corriendo.
2. Un desarrollador del equipo ejecuta el README **sin ayuda** y reporta fricciones.
3. Se muestra en AWS: despliegue por pipeline, apagado nocturno ejecutado y estimación de costo.
4. Producto marca el DoD de la sección 6.

---

## 9. Apéndice: README para desarrolladores (resumen de lo que debe decir)

El `README.md` del repositorio debe incluir, como mínimo:

- **Requisitos previos:** Docker + Docker Compose, Python 3.12.9, (opcional) AWS CLI v2 y `awslocal`.
- **Inicio rápido:** clonar, `docker-compose up -d`, verificar contenedores.
- **Endpoints locales:**
  - AWS emulado: `http://localhost:4566`
  - Gremlin: `ws://localhost:8182/gremlin`
  - OpenSearch: `http://localhost:9200`
- **Flujo de desarrollo:** editar en `./src/motor_reglas/` o `./src/motor_calidad/`, entrar con `docker exec -it dev-motor-reglas bash`, ejecutar `python main.py`.
- **Pruebas (obligatorias antes de push):** `pytest ../tests/ -v` y `pytest ../tests/test_idempotency.py`.
- **Despliegue:** automático por pipeline al hacer merge a `develop`; nota FinOps sobre apagado nocturno y horario hábil 07:00–20:00 COT.
