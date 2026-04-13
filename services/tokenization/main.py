from pathlib import Path
import sys

from fastapi import FastAPI
from fastapi.responses import JSONResponse
import uvicorn

sys.path.append(str(Path(__file__).resolve().parents[1]))

from common import get_readiness_payload, get_settings

settings = get_settings(service_name="tokenization", default_port=8002)

app = FastAPI(title="Tokenization Service")

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": settings.service_name,
        "env_profile": settings.env_profile,
    }


@app.get("/ready")
async def ready():
    payload = get_readiness_payload(settings)
    status_code = 200 if payload["status"] == "ready" else 503
    return JSONResponse(status_code=status_code, content=payload)

if __name__ == "__main__":
    uvicorn.run(app, host=settings.service_host, port=settings.service_port)
