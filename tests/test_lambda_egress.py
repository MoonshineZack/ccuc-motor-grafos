"""Pruebas de la Lambda Egress Orchestrator (Paso 4 de la arquitectura).

Validan que la función se active con el evento de envío de la tríada en sus
distintos formatos y que persista el objeto en el Data Lake (S3).
"""
import json

import pytest

import lambda_egress.lambda_function as egress


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.buckets = set()

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.objects[(Bucket, Key)] = Body

    def create_bucket(self, Bucket):
        self.buckets.add(Bucket)


@pytest.fixture
def fake_s3(monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr(egress, "_s3_client", fake)
    monkeypatch.setattr(egress, "TARGET_BUCKET", "ccuc-landing")
    return fake


TRIADA = {
    "codigoCu": "CU-1001",
    "codigoPvu": "PVU-5001-ABC",
    "golden_record_id": "GR-9999",
    "matches_found": True,
    "score_calidad": 98.5,
    "datos_consolidados": {"nombre": "Juan Perez"},
}


def _assert_guardado(fake_s3, codigo_cu="CU-1001"):
    assert len(fake_s3.objects) == 1
    (bucket, key), body = next(iter(fake_s3.objects.items()))
    assert bucket == "ccuc-landing"
    assert key.startswith(f"triadas-resueltas/{codigo_cu}/")
    assert key.endswith("_payload.json")
    assert json.loads(body)["codigoCu"] == codigo_cu


def test_evento_directo_desde_ecs(fake_s3):
    resp = egress.lambda_handler(TRIADA, None)

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["codigoCu"] == "CU-1001"
    _assert_guardado(fake_s3)


def test_evento_sqs_records(fake_s3):
    evento = {
        "Records": [
            {"messageId": "m-1", "body": json.dumps(TRIADA)},
        ]
    }

    resp = egress.lambda_handler(evento, None)

    assert resp["statusCode"] == 200
    _assert_guardado(fake_s3)


def test_evento_api_gateway_body_string(fake_s3):
    evento = {"body": json.dumps(TRIADA)}

    resp = egress.lambda_handler(evento, None)

    assert resp["statusCode"] == 200
    _assert_guardado(fake_s3)


def test_sin_codigocu_devuelve_500(fake_s3):
    resp = egress.lambda_handler({"datos_consolidados": {}}, None)

    assert resp["statusCode"] == 500
    assert "codigoCu" in json.loads(resp["body"])["error"]
    assert fake_s3.objects == {}


def test_s3_no_such_bucket_se_autorecupera_en_local(fake_s3, monkeypatch):
    from botocore.exceptions import ClientError

    monkeypatch.setenv("ENDPOINT_URL", "http://dev-localstack:4566")
    original_put = fake_s3.put_object
    calls = {"n": 0}

    def put_object(Bucket, Key, Body, ContentType=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ClientError(
                {"Error": {"Code": "NoSuchBucket", "Message": "no existe"}},
                "PutObject",
            )
        return original_put(Bucket, Key, Body, ContentType)

    monkeypatch.setattr(fake_s3, "put_object", put_object)

    resp = egress.lambda_handler(TRIADA, None)

    assert resp["statusCode"] == 200
    assert "ccuc-landing" in fake_s3.buckets
