"""Pruebas de la Lambda Egress Orchestrator (Paso 4 de la arquitectura).

Validan que la función se active con el evento de envío de la tríada en sus
distintos formatos y que persista el objeto en el Data Lake (S3).
"""
import hashlib
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


def test_sin_codigocu_propaga_error(fake_s3):
    with pytest.raises(ValueError, match="codigoCu"):
        egress.lambda_handler({"datos_consolidados": {}}, None)

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


def _esperar_hash(payload):
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def test_clave_s3_es_deterministica_por_hash(fake_s3):
    egress.lambda_handler(TRIADA, None)

    key = next(iter(fake_s3.objects))[1]
    assert key == f"triadas-resueltas/CU-1001/{_esperar_hash(TRIADA)}_payload.json"


def test_reintento_con_mismo_payload_no_duplica_objeto(fake_s3):
    egress.lambda_handler(TRIADA, None)
    egress.lambda_handler(TRIADA, None)

    assert len(fake_s3.objects) == 1


def test_payloads_distintos_generan_claves_distintas(fake_s3):
    otra_triada = {**TRIADA, "score_calidad": 55.0}

    egress.lambda_handler(TRIADA, None)
    egress.lambda_handler(otra_triada, None)

    assert len(fake_s3.objects) == 2
    assert len({key for _, key in fake_s3.objects}) == 2
