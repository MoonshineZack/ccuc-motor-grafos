#!/usr/bin/env bash
# Bootstrap de LocalStack: crea colas, buckets y la Lambda Egress de forma
# idempotente para que todos los desarrolladores tengan el mismo entorno.
set -euo pipefail

QUEUE_NAME="${QUEUE_NAME:-ccuc-novedades-prod.fifo}"
DLQ_NAME="${DLQ_NAME:-ccuc-novedades-dlq.fifo}"
LANDING_BUCKET="${LANDING_BUCKET:-ccuc-landing}"
STAGING_BUCKET="${STAGING_BUCKET:-ccuc-staging}"
EGRESS_LAMBDA_NAME="${EGRESS_LAMBDA_NAME:-ccuc-egress-orchestrator}"
EGRESS_LAMBDA_SRC="${EGRESS_LAMBDA_SRC:-/opt/lambda_egress}"

echo "[init-aws] creando DLQ ${DLQ_NAME}..."
if ! DLQ_URL=$(awslocal sqs get-queue-url --queue-name "${DLQ_NAME}" --query QueueUrl --output text 2>/dev/null); then
  DLQ_URL=$(awslocal sqs create-queue \
    --queue-name "${DLQ_NAME}" \
    --attributes FifoQueue=true,ContentBasedDeduplication=true,MessageRetentionPeriod=1209600 \
    --query QueueUrl --output text)
fi

echo "[init-aws] creando cola FIFO ${QUEUE_NAME} con DLQ..."
if ! awslocal sqs get-queue-url --queue-name "${QUEUE_NAME}" >/dev/null 2>&1; then
  DLQ_ARN=$(awslocal sqs get-queue-attributes --queue-url "${DLQ_URL}" --attribute-names QueueArn --query Attributes.QueueArn --output text)
  awslocal sqs create-queue \
    --queue-name "${QUEUE_NAME}" \
    --attributes "{\"FifoQueue\":\"true\",\"ContentBasedDeduplication\":\"true\",\"VisibilityTimeout\":\"300\",\"RedrivePolicy\":\"{\\\"deadLetterTargetArn\\\":\\\"${DLQ_ARN}\\\",\\\"maxReceiveCount\\\":5}\"}"
fi

for BUCKET in "${LANDING_BUCKET}" "${STAGING_BUCKET}"; do
  echo "[init-aws] creando bucket s3://${BUCKET}..."
  awslocal s3api head-bucket --bucket "${BUCKET}" 2>/dev/null || awslocal s3api create-bucket --bucket "${BUCKET}"
done

# ---------------------------------------------------------------------------
# Lambda Egress Orchestrator: se despliega en LocalStack para que el Motor de
# Reglas (Paso 4) la invoque con el evento de envío de la tríada y esta
# escriba realmente en S3. Si el servicio Lambda no está disponible, el motor
# conserva su fallback local de invocación directa.
# ---------------------------------------------------------------------------
if [ -f "${EGRESS_LAMBDA_SRC}/lambda_function.py" ]; then
  echo "[init-aws] empaquetando Lambda ${EGRESS_LAMBDA_NAME}..."
  rm -f "/tmp/${EGRESS_LAMBDA_NAME}.zip"
  (cd "${EGRESS_LAMBDA_SRC}" && python -m zipfile -c "/tmp/${EGRESS_LAMBDA_NAME}.zip" lambda_function.py)

  LAMBDA_ENV="Variables={DATALAKE_BUCKET=${LANDING_BUCKET},ENDPOINT_URL=http://dev-localstack:4566,AWS_DEFAULT_REGION=us-east-1,LOG_LEVEL=INFO}"

  if awslocal lambda get-function --function-name "${EGRESS_LAMBDA_NAME}" >/dev/null 2>&1; then
    echo "[init-aws] actualizando código de ${EGRESS_LAMBDA_NAME}..."
    awslocal lambda update-function-code \
      --function-name "${EGRESS_LAMBDA_NAME}" \
      --zip-file "fileb:///tmp/${EGRESS_LAMBDA_NAME}.zip" >/dev/null
    awslocal lambda update-function-configuration \
      --function-name "${EGRESS_LAMBDA_NAME}" \
      --environment "${LAMBDA_ENV}" >/dev/null
  else
    echo "[init-aws] creando Lambda ${EGRESS_LAMBDA_NAME}..."
    awslocal lambda create-function \
      --function-name "${EGRESS_LAMBDA_NAME}" \
      --runtime python3.12 \
      --handler lambda_function.lambda_handler \
      --role "arn:aws:iam::000000000000:role/ccuc-egress-lambda-role" \
      --zip-file "fileb:///tmp/${EGRESS_LAMBDA_NAME}.zip" \
      --timeout 60 \
      --memory-size 256 \
      --environment "${LAMBDA_ENV}" >/dev/null || {
        echo "[init-aws] ADVERTENCIA: no se pudo crear la Lambda en LocalStack."
        echo "[init-aws] El motor usará la invocación directa (fallback) para el egress."
      }
  fi

  for _ in $(seq 1 60); do
    STATE=$(awslocal lambda get-function --function-name "${EGRESS_LAMBDA_NAME}" --query Configuration.State --output text 2>/dev/null || true)
    [ "${STATE}" = "Active" ] && break
    sleep 1
  done
  echo "[init-aws] Lambda ${EGRESS_LAMBDA_NAME} estado: ${STATE:-desconocido}"
else
  echo "[init-aws] ADVERTENCIA: no se encontró ${EGRESS_LAMBDA_SRC}/lambda_function.py"
fi

echo "[init-aws] listo:"
awslocal sqs list-queues
awslocal s3api list-buckets --query 'Buckets[].Name' --output table
awslocal lambda list-functions --query 'Functions[].FunctionName' --output table 2>/dev/null || true
