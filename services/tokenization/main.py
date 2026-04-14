from pathlib import Path
import sys

from fastapi import FastAPI
from fastapi.responses import JSONResponse
import uvicorn

sys.path.append(str(Path(__file__).resolve().parents[1]))

from google.protobuf.json_format import MessageToDict
from common import get_readiness_payload, get_settings
from tapd_client import TapdClient

settings = get_settings(service_name="tokenization", default_port=8002)

app = FastAPI(title="Tokenization Service")
tapd_client = TapdClient(settings)

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


@app.get("/tapd/info")
async def tapd_info():
    try:
        info = tapd_client.get_info()
        return MessageToDict(info)
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to connect to tapd", "detail": str(e)},
        )


@app.get("/tapd/assets")
async def tapd_assets():
    try:
        assets = tapd_client.list_assets()
        return MessageToDict(assets)
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to list assets from tapd", "detail": str(e)},
        )

if __name__ == "__main__":
    uvicorn.run(app, host=settings.service_host, port=settings.service_port)
