"""
Raster Line Extraction Service — FastAPI HTTP wrapper for Docker deployment.

Wraps the Lambda handler in a FastAPI application with health endpoint.
"""

import os

from fastapi import FastAPI
from pydantic import BaseModel

from lambda_function import lambda_handler

app = FastAPI(title="Raster Line Extraction Service", version="1.0.0")


class SQSEventPayload(BaseModel):
    """Wrapper for SQS event payload passed to the Lambda handler."""
    Records: list[dict] = []


@app.get("/health")
def health_check():
    """Health check endpoint for Docker HEALTHCHECK and load balancers."""
    return {"status": "healthy", "service": "raster-line-extraction"}


@app.post("/")
@app.post("/invoke")
def invoke_lambda(event: SQSEventPayload):
    """Invoke the Lambda handler with an SQS event payload."""
    result = lambda_handler(event.model_dump(), None)
    return result


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("SERVICE_PORT", "8002"))
    uvicorn.run(app, host="0.0.0.0", port=port)