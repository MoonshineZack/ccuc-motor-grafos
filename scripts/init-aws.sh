#!/usr/bin/env bash
# Bootstrap de LocalStack: crea colas y buckets de forma idempotente
# para que todos los desarrolladores tengan exactamente el mismo entorno.
set -euo pipefail

QUEUE_NAME="${QUEUE_NAME:-ccuc-novedades-prod.fifo}"
DLQ_NAME="${DLQ_NAME:-ccuc-novedades-dlq.fifo}"
LANDING_BUCKET="${LANDING_BUCKET:-ccuc-landing}"
STAGING_BUCKET="${STAGING_BUCKET:-ccuc-staging}"

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

echo "[init-aws] listo:"
awslocal sqs list-queues
awslocal s3api list-buckets --query 'Buckets[].Name' --output table
