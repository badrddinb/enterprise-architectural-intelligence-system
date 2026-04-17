"""
Compliance Checker Service — FastAPI HTTP wrapper for Docker deployment.

Provides a health endpoint and API for triggering compliance checks.
"""

import os

from fastapi import FastAPI

app = FastAPI(title="Compliance Checker Service", version="1.0.0")


@app.get("/health")
def health_check():
    """Health check endpoint for Docker HEALTHCHECK and load balancers."""
    return {"status": "healthy", "service": "compliance-checker"}


@app.post("/")
@app.post("/invoke")
def invoke_check():
    """Placeholder for compliance check invocation.

    The full compliance check pipeline requires a CertifiedMathGraph input
    and LLM/Qdrant connectivity. This endpoint confirms the service is alive.
    """
    return {
        "status": "ready",
        "message": "Compliance checker service is running. POST a graph to /check to start.",
    }


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("SERVICE_PORT", "8005"))
    uvicorn.run(app, host="0.0.0.0", port=port)