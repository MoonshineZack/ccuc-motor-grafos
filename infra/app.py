"""Infraestructura AWS Dev/QA del motor de grafos CCUC (spec 5.1 y 5.4)."""
import os

from aws_cdk import (
    App,
    CfnOutput,
    Duration,
    Environment,
    RemovalPolicy,
    Stack,
    Tags,
    aws_applicationautoscaling as appscaling,
    aws_cloudwatch as cloudwatch,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_events as events,
    aws_events_targets as targets,
    aws_iam as iam,
    aws_lambda as _lambda,
    aws_logs as logs,
    aws_neptune as neptune,
    aws_opensearchservice as opensearch,
    aws_s3 as s3,
    aws_sqs as sqs,
    aws_ssm as ssm,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.join(BASE_DIR, "..")
LAMBDAS_DIR = os.path.join(BASE_DIR, "lambdas")

QUEUE_NAME = "ccuc-novedades-prod.fifo"
DLQ_NAME = "ccuc-novedades-dlq.fifo"
NAT_TAG = "ccuc-rol"


class CcucDevQaStack(Stack):
    def __init__(self, scope, construct_id, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        # ------------------------------------------------------------------
        # Red (5.1): VPC con 1 NAT Gateway, etiquetado para la lambda de ciclo de vida
        # ------------------------------------------------------------------
        vpc = ec2.Vpc(
            self,
            "VPC",
            max_azs=3,
            nat_gateways=1,
            subnet_configuration=[
                ec2.SubnetConfiguration(name="public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24),
                ec2.SubnetConfiguration(
                    name="private", subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS, cidr_mask=24
                ),
            ],
        )
        Tags.of(vpc).add("Name", "ccuc-devqa")
        for node in vpc.node.find_all():
            if isinstance(node, (ec2.CfnNatGateway, ec2.CfnEIP)):
                Tags.of(node).add(NAT_TAG, "nat")

        # ------------------------------------------------------------------
        # Colas SQS FIFO con deduplicación por contenido + DLQ (5.1)
        # ------------------------------------------------------------------
        dlq = sqs.Queue(
            self,
            "Dlq",
            queue_name=DLQ_NAME,
            fifo=True,
            content_based_deduplication=True,
            retention_period=Duration.days(14),
        )
        queue = sqs.Queue(
            self,
            "ColaNovedades",
            queue_name=QUEUE_NAME,
            fifo=True,
            content_based_deduplication=True,
            visibility_timeout=Duration.seconds(300),
            dead_letter_queue=sqs.DeadLetterQueue(max_receive_count=5, queue=dlq),
        )

        # ------------------------------------------------------------------
        # OpenSearch (5.1): single-AZ, 1 data node t3.small, dentro de la VPC
        # ------------------------------------------------------------------
        search_sg = ec2.SecurityGroup(self, "BusquedaSg", vpc=vpc, description="OpenSearch CCUC")
        search_sg.add_ingress_rule(ec2.Peer.ipv4(vpc.vpc_cidr_block), ec2.Port.tcp(443))
        domain = opensearch.Domain(
            self,
            "Busqueda",
            version=opensearch.EngineVersion.OPENSEARCH_2_17,
            capacity=opensearch.CapacityConfig(
                data_node_instance_type="t3.small.search", data_nodes=1
            ),
            ebs=opensearch.EbsOptions(volume_size=10, volume_type=ec2.EbsDeviceVolumeType.GP3),
            vpc=vpc,
            vpc_subnets=[ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS)],
            security_groups=[search_sg],
            encryption_at_rest=opensearch.EncryptionAtRestOptions(enabled=False),
            node_to_node_encryption=False,
            enforce_https=False,
            access_policies=[
                iam.PolicyStatement(actions=["es:*"], resources=["*"], principals=[iam.AnyPrincipal()])
            ],
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ------------------------------------------------------------------
        # Neptune Serverless (5.1 + 5.4: la lambda reduce su capacidad de noche)
        # ------------------------------------------------------------------
        neptune_sg = ec2.SecurityGroup(self, "GrafosSg", vpc=vpc, description="Neptune CCUC")
        neptune_sg.add_ingress_rule(ec2.Peer.ipv4(vpc.vpc_cidr_block), ec2.Port.tcp(8182))
        neptune_subnets = neptune.CfnDBSubnetGroup(
            self,
            "GrafosSubnetGroup",
            db_subnet_group_description="Subredes privadas CCUC",
            subnet_ids=[subnet.subnet_id for subnet in vpc.private_subnets],
        )
        neptune_cluster = neptune.CfnDBCluster(
            self,
            "GrafosCluster",
            db_cluster_identifier="ccuc-grafos-devqa",
            db_subnet_group_name=neptune_subnets.ref,
            vpc_security_group_ids=[neptune_sg.security_group_id],
            deletion_protection=False,
            serverless_scaling_configuration=neptune.CfnDBCluster.ServerlessScalingConfigurationProperty(
                min_capacity=0.5, max_capacity=4
            ),
        )
        neptune.CfnDBInstance(
            self,
            "GrafosInstance",
            db_cluster_identifier=neptune_cluster.ref,
            db_instance_identifier="ccuc-grafos-devqa-1",
            db_instance_class="db.serverless",
        )

        # ------------------------------------------------------------------
        # Data Lake S3 y Lambda Egress Orchestrator (Paso 4 de arquitectura)
        # ------------------------------------------------------------------
        landing_bucket = s3.Bucket(
            self,
            "LandingBucket",
            bucket_name=f"ccuc-landing-{self.account or 'nutresa'}-{self.region or 'us-east-1'}",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        egress_lambda = _lambda.Function(
            self,
            "EgressOrchestrator",
            function_name="ccuc-egress-orchestrator",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="lambda_function.lambda_handler",
            code=_lambda.Code.from_asset(os.path.join(REPO_ROOT, "src", "lambda_egress")),
            timeout=Duration.seconds(60),
            environment={
                "DATALAKE_BUCKET": landing_bucket.bucket_name,
            },
        )
        landing_bucket.grant_write(egress_lambda)

        # ------------------------------------------------------------------
        # ECS Fargate con Spot, autoescalado min 2 - max 20 tareas (5.1 + 5.4)
        # ------------------------------------------------------------------
        cluster = ecs.Cluster(self, "Cluster", cluster_name="ccuc-devqa", vpc=vpc)
        task_def = ecs.FargateTaskDefinition(
            self,
            "MotorReglasTask",
            cpu=256,
            memory_limit_mib=512,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.X86_64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
        )
        task_logs = logs.LogGroup(
            self,
            "MotorReglasLogs",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )
        task_def.add_container(
            "motor-reglas",
            image=ecs.ContainerImage.from_asset(
                REPO_ROOT,
                file="src/motor_reglas/Dockerfile",
                exclude=[
                    ".venv",
                    "**/.venv",
                    "cdk.out",
                    "**/cdk.out",
                    ".git",
                    "**/.git",
                    "node_modules",
                    "**/node_modules",
                    "__pycache__",
                    "**/__pycache__",
                    "**/.pytest_cache",
                    "**/*.pyc",
                ],
            ),
            logging=ecs.LogDrivers.aws_logs(stream_prefix="motor-reglas", log_group=task_logs),
            environment={
                "QUEUE_URL": queue.queue_url,
                "ENDPOINT_URL": "",
                "OPENSEARCH_ENDPOINT": domain.domain_endpoint,
                "GREMLIN_HOST": neptune_cluster.attr_endpoint,
                "GREMLIN_PORT": neptune_cluster.attr_port,
                "LAMBDA_EGRESS_NAME": egress_lambda.function_name,
            },
        )
        queue.grant_consume_messages(task_def.task_role)
        egress_lambda.grant_invoke(task_def.task_role)

        # Reglas IAM para Amazon Neptune Serverless (Gremlin API)
        task_def.task_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "neptune-db:connect",
                    "neptune-db:ReadDataViaQuery",
                    "neptune-db:WriteDataViaQuery",
                    "neptune-db:GetGraphSummary",
                ],
                resources=["*"],
            )
        )

        # Reglas IAM CloudWatch Metrics para BacklogPerTask
        task_def.task_role.add_to_policy(
            iam.PolicyStatement(
                actions=["cloudwatch:PutMetricData"],
                resources=["*"],
                conditions={"StringEquals": {"cloudwatch:namespace": "CCUC/MotorReglas"}},
            )
        )

        service = ecs.FargateService(
            self,
            "MotorReglasService",
            service_name="ccuc-motor-reglas",
            cluster=cluster,
            task_definition=task_def,
            desired_count=2,
            capacity_provider_strategies=[
                ecs.CapacityProviderStrategy(capacity_provider="FARGATE_SPOT", weight=1)
            ],
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            assign_public_ip=False,
            circuit_breaker=ecs.DeploymentCircuitBreaker(enable=True, rollback=True),
        )

        # Application Auto Scaling: target tracking sobre BacklogPerTask (min 2 - max 20 tareas)
        scaling = service.auto_scale_task_count(
            min_capacity=2,
            max_capacity=20,
        )
        scaling.scale_to_track_custom_metric(
            "BacklogPerTaskTracking",
            metric=cloudwatch.Metric(
                namespace="CCUC/MotorReglas",
                metric_name="BacklogPerTask",
                statistic="Average",
                period=Duration.minutes(1),
            ),
            target_value=10.0,
            scale_in_cooldown=Duration.seconds(300),
            scale_out_cooldown=Duration.seconds(60),
        )

        # ------------------------------------------------------------------
        # Lambda de ciclo de vida (5.4) + reglas EventBridge (UTC = COT-5)
        # Apaga: lunes a viernes 20:00 COT (cron 01:00 UTC mar-sáb)
        # Enciende: lunes a viernes 07:00 COT (cron 12:00 UTC lun-vie)
        # ------------------------------------------------------------------
        nat_state = ssm.StringParameter(
            self,
            "NatState",
            parameter_name="/ccuc/devqa/nat-state",
            string_value="{}",
        )
        ciclo_vida = _lambda.Function(
            self,
            "CicloVida",
            function_name="ccuc-ciclo-vida-devqa",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="index.handler",
            code=_lambda.Code.from_asset(os.path.join(LAMBDAS_DIR, "ciclo_vida")),
            timeout=Duration.minutes(10),
            memory_size=256,
            environment={
                "ECS_CLUSTER": cluster.cluster_name,
                "ECS_SERVICE": service.service_name,
                "NEPTUNE_CLUSTER_ID": neptune_cluster.ref,
                "NEPTUNE_MIN": "0.5",
                "NEPTUNE_MAX_ON": "4",
                "NEPTUNE_MAX_OFF": "0.5",
                "VPC_ID": vpc.vpc_id,
                "NAT_TAG": NAT_TAG,
                "NAT_TAG_VALUE": "nat",
                "NAT_STATE_PARAM": nat_state.parameter_name,
            },
        )
        nat_state.grant_read(ciclo_vida)
        nat_state.grant_write(ciclo_vida)
        ciclo_vida.add_to_role_policy(
            iam.PolicyStatement(actions=["ecs:UpdateService"], resources=[service.service_arn])
        )
        ciclo_vida.add_to_role_policy(
            iam.PolicyStatement(
                actions=["rds:ModifyDBCluster"],
                resources=[
                    self.format_arn(service="rds", resource="cluster", resource_name=neptune_cluster.ref)
                ],
            )
        )
        ciclo_vida.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "rds:DescribeDBClusters",
                    "ec2:DescribeNatGateways",
                    "ec2:DescribeAddresses",
                    "ec2:DescribeRouteTables",
                    "ec2:DescribeVpcs",
                    "ec2:CreateNatGateway",
                    "ec2:DeleteNatGateway",
                    "ec2:CreateRoute",
                    "ec2:ReplaceRoute",
                    "ec2:DeleteRoute",
                ],
                resources=["*"],
            )
        )

        apagar = events.Rule(
            self,
            "ApagarDevQa",
            schedule=events.Schedule.expression("cron(0 1 ? * TUE-SAT *)"),
            description="Apaga Dev/QA 20:00 COT (lunes-viernes)",
        )
        apagar.add_target(
            targets.LambdaFunction(ciclo_vida, event=events.RuleTargetInput.from_object({"action": "stop"}))
        )
        encender = events.Rule(
            self,
            "EncenderDevQa",
            schedule=events.Schedule.expression("cron(0 12 ? * MON-FRI *)"),
            description="Enciende Dev/QA 07:00 COT (lunes-viernes)",
        )
        encender.add_target(
            targets.LambdaFunction(ciclo_vida, event=events.RuleTargetInput.from_object({"action": "start"}))
        )

        # Lambda de orquestación (5.1): stub que el equipo CCUC implementa
        _lambda.Function(
            self,
            "Orquestador",
            function_name="ccuc-orquestador-devqa",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="index.handler",
            code=_lambda.Code.from_asset(os.path.join(LAMBDAS_DIR, "orquestador")),
            timeout=Duration.seconds(30),
            environment={"QUEUE_URL": queue.queue_url},
        )

        CfnOutput(self, "ColaNovedadesUrl", value=queue.queue_url)
        CfnOutput(self, "ColaDlqUrl", value=dlq.queue_url)
        CfnOutput(self, "LandingBucketName", value=landing_bucket.bucket_name)
        CfnOutput(self, "EgressLambdaArn", value=egress_lambda.function_arn)
        CfnOutput(self, "OpenSearchEndpoint", value=domain.domain_endpoint)
        CfnOutput(self, "NeptuneEndpoint", value=neptune_cluster.attr_endpoint)
        CfnOutput(self, "EcsCluster", value=cluster.cluster_name)
        CfnOutput(self, "EcsService", value=service.service_name)


app = App()
CcucDevQaStack(
    app,
    "CcucDevQaStack",
    env=Environment(account=os.getenv("CDK_DEFAULT_ACCOUNT"), region=os.getenv("CDK_DEFAULT_REGION")),
    description="Motor de grafos CCUC - ambiente Dev/QA (spec-laboratorio-devops-ccuc)",
)
app.synth()
