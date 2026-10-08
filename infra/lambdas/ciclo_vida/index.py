"""Lambda de ciclo de vida Dev/QA (spec 5.4).

stop  (20:00 COT): escala ECS a 0, fija Neptune en su mínimo y destruye el NAT Gateway.
start (07:00 COT): recrea el NAT Gateway (mismo Elastic IP), restaura Neptune y escala ECS a 1.
"""
import json
import os
import time

import boto3

ecs = boto3.client("ecs")
rds = boto3.client("rds")
ec2 = boto3.client("ec2")
ssm = boto3.client("ssm")


def handler(event, context):
    action = (event or {}).get("action", "stop")
    if action == "start":
        _start()
    else:
        _stop()
    return {"action": action, "ok": True}


def _scale_ecs(desired):
    ecs.update_service(
        cluster=os.environ["ECS_CLUSTER"],
        service=os.environ["ECS_SERVICE"],
        desiredCount=desired,
    )


def _set_neptune(min_capacity, max_capacity):
    rds.modify_db_cluster(
        DBClusterIdentifier=os.environ["NEPTUNE_CLUSTER_ID"],
        ServerlessV2ScalingConfiguration={
            "MinCapacity": min_capacity,
            "MaxCapacity": max_capacity,
        },
        ApplyImmediately=True,
    )


def _tag_filter():
    return {"Name": f"tag:{os.environ['NAT_TAG']}", "Values": [os.environ["NAT_TAG_VALUE"]]}


def _find_nat():
    nats = ec2.describe_nat_gateways(
        Filters=[_tag_filter(), {"Name": "state", "Values": ["available", "pending"]}]
    )["NatGateways"]
    return nats[0] if nats else None


def _find_eip():
    addrs = ec2.describe_addresses(Filters=[_tag_filter()])["Addresses"]
    return addrs[0] if addrs else None


def _route_tables_using(nat_id):
    rtbs = []
    for rtb in ec2.describe_route_tables(
        Filters=[{"Name": "vpc-id", "Values": [os.environ["VPC_ID"]]}]
    )["RouteTables"]:
        if any(r.get("NatGatewayId") == nat_id for r in rtb.get("Routes", [])):
            rtbs.append(rtb["RouteTableId"])
    return rtbs


def _stop():
    _scale_ecs(0)
    _set_neptune(float(os.environ["NEPTUNE_MIN"]), float(os.environ["NEPTUNE_MAX_OFF"]))
    nat = _find_nat()
    if not nat:
        print("NAT ya destruido")
        return
    route_tables = _route_tables_using(nat["NatGatewayId"])
    for rtb in route_tables:
        try:
            ec2.delete_route(RouteTableId=rtb, DestinationCidrBlock="0.0.0.0/0")
        except ec2.exceptions.ClientError as exc:
            print(f"delete_route {rtb}: {exc}")
    eip = _find_eip()
    state = {
        "subnet_id": nat["SubnetId"],
        "route_tables": route_tables,
        "allocation_id": eip["AllocationId"] if eip else None,
    }
    ssm.put_parameter(
        Name=os.environ["NAT_STATE_PARAM"], Value=json.dumps(state), Type="String", Overwrite=True
    )
    ec2.delete_nat_gateway(NatGatewayId=nat["NatGatewayId"])
    print(f"NAT destruido: {state}")


def _start():
    state = json.loads(
        ssm.get_parameter(Name=os.environ["NAT_STATE_PARAM"])["Parameter"]["Value"]
    )
    nat = _find_nat()
    if not nat and state.get("subnet_id"):
        kwargs = {"SubnetId": state["subnet_id"]}
        if state.get("allocation_id"):
            kwargs["AllocationId"] = state["allocation_id"]
        nat_id = ec2.create_nat_gateway(**kwargs)["NatGatewayId"]
        for _ in range(30):
            status = ec2.describe_nat_gateways(NatGatewayIds=[nat_id])["NatGateways"][0]["State"]
            if status == "available":
                break
            if status in ("failed", "deleting"):
                raise RuntimeError(f"NAT {nat_id} en estado {status}")
            time.sleep(10)
        for rtb in state.get("route_tables", []):
            try:
                ec2.create_route(
                    RouteTableId=rtb, DestinationCidrBlock="0.0.0.0/0", NatGatewayId=nat_id
                )
            except ec2.exceptions.ClientError:
                ec2.replace_route(
                    RouteTableId=rtb, DestinationCidrBlock="0.0.0.0/0", NatGatewayId=nat_id
                )
        print(f"NAT recreado: {nat_id}")
    else:
        print("NAT ya existe")
    _set_neptune(float(os.environ["NEPTUNE_MIN"]), float(os.environ["NEPTUNE_MAX_ON"]))
    _scale_ecs(1)
