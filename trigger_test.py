#!/usr/bin/env python3
"""
trigger_test.py — Integration test script for the local simulation environment.

Uses boto3 to:
  1. Upload a local file to the LocalStack S3 bucket (arch-ingestion-bucket).
  2. Send a test message to each SQS queue.
  3. Start an execution of the Step Function state machine.

Usage:
  # Make sure the Docker Compose stack is running first:
  #   docker compose up -d

  # Upload a specific file:
  python trigger_test.py --file path/to/plan.pdf

  # Upload a dummy test payload (no real file needed):
  python trigger_test.py --dummy

  # Run all tests (S3 upload + SQS + Step Function):
  python trigger_test.py --all --file path/to/plan.pdf
"""

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import boto3
from botocore.config import Config

# ---------------------------------------------------------------------------
# Configuration — defaults match .env.template and docker-compose.yml
# ---------------------------------------------------------------------------
LOCALSTACK_ENDPOINT = os.getenv("AWS_ENDPOINT_URL_EXTERNAL", "http://localhost:4566")
AWS_REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "test")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "test")

S3_BUCKET = os.getenv("S3_BUCKET_NAME", "arch-ingestion-bucket")
SQS_RASTER_QUEUE = os.getenv("SQS_RASTER_QUEUE", "Raster-Processing-Queue")
SQS_VECTOR_QUEUE = os.getenv("SQS_VECTOR_QUEUE", "Vector-Processing-Queue")
STATE_MACHINE_NAME = os.getenv("STATE_MACHINE_NAME", "architectural-plan-processor")


def get_boto3_client(service: str):
    """Create a boto3 client pointing to the LocalStack endpoint."""
    return boto3.client(
        service,
        region_name=AWS_REGION,
        endpoint_url=LOCALSTACK_ENDPOINT,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        config=Config(retries={"max_attempts": 3, "mode": "adaptive"}),
    )


def upload_file_to_s3(file_path: str, key: str = None) -> dict:
    """Upload a local file to the S3 bucket and return the response."""
    s3 = get_boto3_client("s3")
    path = Path(file_path)

    if not path.exists():
        print(f"ERROR: File not found: {file_path}")
        sys.exit(1)

    if key is None:
        key = f"uploads/{uuid.uuid4().hex[:8]}_{path.name}"

    print(f"\n{'='*60}")
    print(f" S3 Upload")
    print(f"{'='*60}")
    print(f"  File      : {path.absolute()}")
    print(f"  Bucket    : {S3_BUCKET}")
    print(f"  Key       : {key}")
    print(f"  Size      : {path.stat().st_size:,} bytes")
    print(f"  Endpoint  : {LOCALSTACK_ENDPOINT}")

    response = s3.upload_file(str(path.absolute()), S3_BUCKET, key)
    print(f"  Status    : ✓ Uploaded successfully")

    # Verify by head-object
    head = s3.head_object(Bucket=S3_BUCKET, Key=key)
    print(f"  ETag      : {head.get('ETag', 'N/A')}")

    return {"bucket": S3_BUCKET, "key": key, "size": path.stat().st_size}


def upload_dummy_payload() -> dict:
    """Create and upload a dummy JSON payload to S3."""
    s3 = get_boto3_client("s3")

    dummy_payload = {
        "fileId": f"test-{uuid.uuid4().hex[:8]}",
        "fileName": "test-plan.pdf",
        "uploadedAt": datetime.now(timezone.utc).isoformat(),
        "mimeType": "application/pdf",
        "sizeBytes": 2048,
        "metadata": {
            "source": "trigger_test.py",
            "environment": "local",
            "version": "1.0.0",
        },
    }

    key = f"uploads/{dummy_payload['fileId']}_{dummy_payload['fileName']}"

    print(f"\n{'='*60}")
    print(f" S3 Upload (Dummy Payload)")
    print(f"{'='*60}")
    print(f"  Bucket    : {S3_BUCKET}")
    print(f"  Key       : {key}")
    print(f"  Endpoint  : {LOCALSTACK_ENDPOINT}")

    s3.put_object(
        Bucket=S3_BUCKET,
        Key=key,
        Body=json.dumps(dummy_payload, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    print(f"  Status    : ✓ Uploaded successfully")

    return {"bucket": S3_BUCKET, "key": key, "payload": dummy_payload}


def send_sqs_messages(upload_info: dict):
    """Send test messages to both SQS queues."""
    sqs = get_boto3_client("sqs")

    queues = [SQS_RASTER_QUEUE, SQS_VECTOR_QUEUE]

    print(f"\n{'='*60}")
    print(f" SQS Messages")
    print(f"{'='*60}")

    for queue_name in queues:
        try:
            queue_url = sqs.get_queue_url(QueueName=queue_name)["QueueUrl"]
        except sqs.exceptions.QueueDoesNotExist:
            print(f"  {queue_name}: ✗ Queue not found — skipping.")
            continue

        message_body = {
            "event": "file-uploaded",
            "bucket": upload_info.get("bucket", S3_BUCKET),
            "key": upload_info.get("key", "unknown"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "messageId": uuid.uuid4().hex,
        }

        response = sqs.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(message_body, indent=2),
            MessageAttributes={
                "source": {
                    "StringValue": "trigger_test.py",
                    "DataType": "String",
                },
                "environment": {
                    "StringValue": "local",
                    "DataType": "String",
                },
            },
        )

        print(f"  {queue_name}:")
        print(f"    MessageId : {response['MessageId']}")
        print(f"    MD5       : {response['MD5OfMessageBody']}")
        print(f"    Status    : ✓ Sent")


def trigger_step_function(upload_info: dict):
    """Start an execution of the Step Function state machine."""
    sfn = get_boto3_client("stepfunctions")

    print(f"\n{'='*60}")
    print(f" Step Function Execution")
    print(f"{'='*60}")
    print(f"  State Machine : {STATE_MACHINE_NAME}")
    print(f"  Endpoint      : {LOCALSTACK_ENDPOINT}")

    # Resolve the state machine ARN
    try:
        state_machines = sfn.list_state_machines()
        sm_arn = None
        for sm in state_machines.get("stateMachines", []):
            if sm["name"] == STATE_MACHINE_NAME:
                sm_arn = sm["stateMachineArn"]
                break

        if sm_arn is None:
            print(f"  Status: ✗ State machine '{STATE_MACHINE_NAME}' not found.")
            print(f"  Available: {[sm['name'] for sm in state_machines.get('stateMachines', [])]}")
            return

    except Exception as e:
        print(f"  Status: ✗ Error listing state machines: {e}")
        return

    # Build the execution input
    execution_name = f"test-exec-{uuid.uuid4().hex[:8]}"
    execution_input = {
        "executionId": execution_name,
        "uploadedFile": {
            "bucket": upload_info.get("bucket", S3_BUCKET),
            "key": upload_info.get("key", "unknown"),
            "fileId": upload_info.get("payload", {}).get("fileId", "unknown"),
        },
        "pipeline": {
            "version": "1.0.0",
            "environment": "local",
            "triggeredAt": datetime.now(timezone.utc).isoformat(),
            "triggeredBy": "trigger_test.py",
        },
    }

    print(f"  Execution Name: {execution_name}")
    print(f"  ARN           : {sm_arn}")

    response = sfn.start_execution(
        stateMachineArn=sm_arn,
        name=execution_name,
        input=json.dumps(execution_input, indent=2),
    )

    print(f"  Execution ARN : {response['executionArn']}")
    print(f"  Started At    : {response['startDate']}")
    print(f"  Status        : ✓ Execution started")

    # Optionally describe the execution after a brief pause
    import time
    print(f"\n  Waiting 2s for execution to complete...")
    time.sleep(2)

    desc = sfn.describe_execution(executionArn=response["executionArn"])
    print(f"  Final Status  : {desc['status']}")

    if desc["status"] == "SUCCEEDED" and desc.get("output"):
        output = json.loads(desc["output"])
        print(f"  Output        : {json.dumps(output, indent=4)}")


def main():
    parser = argparse.ArgumentParser(
        description="Trigger test script for the local Architectural Intelligence System."
    )
    parser.add_argument(
        "--file", "-f",
        help="Path to a local file to upload to S3.",
    )
    parser.add_argument(
        "--dummy", "-d",
        action="store_true",
        help="Upload a dummy JSON payload instead of a real file.",
    )
    parser.add_argument(
        "--s3-only",
        action="store_true",
        help="Only upload to S3 (skip SQS and Step Functions).",
    )
    parser.add_argument(
        "--sqs-only",
        action="store_true",
        help="Only send SQS messages.",
    )
    parser.add_argument(
        "--sfn-only",
        action="store_true",
        help="Only trigger the Step Function.",
    )
    parser.add_argument(
        "--all", "-a",
        action="store_true",
        help="Run all tests: S3 upload + SQS messages + Step Function execution.",
    )

    args = parser.parse_args()

    # Default: if nothing specified, run everything with dummy data
    if not any([args.file, args.dummy, args.s3_only, args.sqs_only, args.sfn_only, args.all]):
        args.dummy = True
        args.all = True

    print(f"\n{'#'*60}")
    print(f" Enterprise Architectural Intelligence System")
    print(f" Local Integration Test")
    print(f"{'#'*60}")
    print(f"  Endpoint : {LOCALSTACK_ENDPOINT}")
    print(f"  Region   : {AWS_REGION}")

    upload_info = {}

    # S3 Upload
    if not args.sqs_only and not args.sfn_only:
        if args.file:
            upload_info = upload_file_to_s3(args.file)
        elif args.dummy:
            upload_info = upload_dummy_payload()

    # SQS Messages
    if not args.s3_only and not args.sfn_only:
        send_sqs_messages(upload_info)

    # Step Function
    if not args.s3_only and not args.sqs_only:
        trigger_step_function(upload_info)

    print(f"\n{'#'*60}")
    print(f" Test Complete ✓")
    print(f"{'#'*60}\n")


if __name__ == "__main__":
    main()