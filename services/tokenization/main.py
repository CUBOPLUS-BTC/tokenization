from fastapi import FastAPI
import uvicorn

app = FastAPI(title="Tokenization Service")

@app.get("/health")
async def health():
    return {"status": "ok", "service": "tokenization"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8002)
