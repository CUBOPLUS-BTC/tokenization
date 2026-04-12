from pathlib import Path
import sys

from fastapi import FastAPI
import uvicorn

sys.path.append(str(Path(__file__).resolve().parents[1]))

from common import get_settings

settings = get_settings(service_name="nostr", default_port=8005)

app = FastAPI(title="Nostr Service")

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": settings.service_name,
        "env_profile": settings.env_profile,
    }

if __name__ == "__main__":
    uvicorn.run(app, host=settings.service_host, port=settings.service_port)
