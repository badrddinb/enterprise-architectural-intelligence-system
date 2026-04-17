#!/bin/bash
# =============================================================================
# init-aws.sh — LocalStack Initialization Script
# =============================================================================
# This script is automatically executed by LocalStack on startup when mounted
# to /etc/localstack/init/ready.d/ via a Docker volume.
#
# It uses the awslocal CLI to provision:
#   1. S3 Bucket  — arch-ingestion-bucket
#   2. SQS Queues — Raster-Processing-Queue, Vector-Processing-Queue
#   3. Step Function — architectural-plan-processor (dummy ASL)
# =============================================================================

set -euo pipefail

echo "============================================"
echo " LocalStack Bootstrap — init-aws.sh"
echo "============================================"

# ---------------------------------------------------------------------------
# Helper: retry a command up to N times with a sleep interval
# ---------------------------------------------------------------------------
retry() {
    local max_attempts="${1}"
    local sleep_interval="${2}"
    shift 2

    local attempt=1
    while [ $attempt -le $max_attempts ]; do
        echo "[retry] Attempt ${attempt}/${max_attempts}: $*"
        if eval "$@"; then
            echo "[retry] Success on attempt ${attempt}"
            return 0
        fi
        echo "[retry] Failed. Retrying in ${sleep_interval}s..."
        sleep "${sleep_interval}"
        attempt=$((attempt + 1))
    done

    echo "[retry] All ${max_attempts} attempts exhausted for: $*"
    return 1
}

# Wait for LocalStack to be fully ready
echo ""
echo ">>> Waiting for LocalStack to be ready..."
retry 30 2 "awslocal s3 ls > /dev/null 2>&1"
echo "    LocalStack is ready."

# ===========================================================================
# 1. S3 BUCKET
# ===========================================================================
echo ""
echo ">>> Creating S3 bucket: arch-ingestion-bucket"

if awslocal s3api head-bucket --bucket arch-ingestion-bucket 2>/dev/null; then
    echo "    S3 bucket 'arch-ingestion-bucket' already exists — skipping."
else
    awslocal s3api create-bucket \
        --bucket arch-ingestion-bucket \
        --region us-east-1 \
        --create-bucket-configuration LocationConstraint=us-east-1 2>/dev/null || \
    awslocal s3api create-bucket \
        --bucket arch-ingestion-bucket \
        --region us-east-1

    echo "    ✓ S3 bucket 'arch-ingestion-bucket' created."
fi

# ===========================================================================
# 2. SQS QUEUES
# ===========================================================================
echo ""
echo ">>> Creating SQS queues..."

# Raster-Processing-Queue
if awslocal sqs get-queue-url --queue-name Raster-Processing-Queue > /dev/null 2>&1; then
    echo "    SQS queue 'Raster-Processing-Queue' already exists — skipping."
else
    awslocal sqs create-queue \
        --queue-name Raster-Processing-Queue \
        --attributes '{
            "VisibilityTimeout": "300",
            "MessageRetentionPeriod": "1209600",
            "DelaySeconds": "0",
            "ReceiveMessageWaitTimeSeconds": "20"
        }'
    echo "    ✓ SQS queue 'Raster-Processing-Queue' created."
fi

# Vector-Processing-Queue
if awslocal sqs get-queue-url --queue-name Vector-Processing-Queue > /dev/null 2>&1; then
    echo "    SQS queue 'Vector-Processing-Queue' already exists — skipping."
else
    awslocal sqs create-queue \
        --queue-name Vector-Processing-Queue \
        --attributes '{
            "VisibilityTimeout": "300",
            "MessageRetentionPeriod": "1209600",
            "DelaySeconds": "0",
            "ReceiveMessageWaitTimeSeconds": "20"
        }'
    echo "    ✓ SQS queue 'Vector-Processing-Queue' created."
fi

# ===========================================================================
# 3. STEP FUNCTION STATE MACHINE
# ===========================================================================
echo ""
echo ">>> Registering Step Function state machine: architectural-plan-processor"

STATE_MACHINE_DEF='/opt/localstack/state-machine-definition.json'

# Check if the definition file exists (it will be mounted from the host)
if [ ! -f "${STATE_MACHINE_DEF}" ]; then
    echo "    WARNING: State machine definition not found at ${STATE_MACHINE_DEF}"
    echo "    Using inline fallback definition..."

    # Inline fallback — minimal Pass-state machine for testing
    STATE_MACHINE_DEF='/tmp/fallback-state-machine.json'
    cat > "${STATE_MACHINE_DEF}" <<'FALLBACK_ASL'
{
  "Comment": "Fallback dummy state machine for LocalStack testing.",
  "StartAt": "Process",
  "States": {
    "Process": {
      "Type": "Pass",
      "Result": { "status": "completed", "message": "Dummy execution succeeded" },
      "End": true
    }
  }
}
FALLBACK_ASL
fi

# Check if the state machine already exists
EXISTING_ARN=$(awslocal stepfunctions list-state-machines \
    --query "stateMachines[?name=='architectural-plan-processor'].stateMachineArn" \
    --output text 2>/dev/null || echo "")

if [ -n "${EXISTING_ARN}" ] && [ "${EXISTING_ARN}" != "None" ]; then
    echo "    State machine 'architectural-plan-processor' already exists (ARN: ${EXISTING_ARN}) — updating definition."
    awslocal stepfunctions update-state-machine \
        --state-machine-arn "${EXISTING_ARN}" \
        --definition file://"${STATE_MACHINE_DEF}"
    echo "    ✓ State machine definition updated."
else
    IAM_ROLE_ARN="arn:aws:iam::000000000000:role/StepFunctionsExecutionRole"

    # Ensure the IAM role exists (LocalStack auto-creates on reference, but explicit is better)
    awslocal iam create-role \
        --role-name StepFunctionsExecutionRole \
        --assume-role-policy-document '{
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": { "Service": "states.us-east-1.amazonaws.com" },
                "Action": "sts:AssumeRole"
            }]
        }' 2>/dev/null || echo "    IAM role already exists — skipping."

    awslocal stepfunctions create-state-machine \
        --name architectural-plan-processor \
        --definition file://"${STATE_MACHINE_DEF}" \
        --role-arn "${IAM_ROLE_ARN}"

    echo "    ✓ Step Function state machine 'architectural-plan-processor' created."
fi

# ===========================================================================
# Summary
# ===========================================================================
echo ""
echo "============================================"
echo " LocalStack Bootstrap Complete"
echo "============================================"
echo " S3 Bucket : arch-ingestion-bucket"
echo " SQS Queues: Raster-Processing-Queue, Vector-Processing-Queue"
echo " Step Fn   : architectural-plan-processor"
echo "============================================"