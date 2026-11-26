"""A small FastAPI app. minicloud serves it with uvicorn on port 80."""

import platform

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI(title="minicloud demo")


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    version = platform.python_version()
    return f"<h1>Xin chào từ minicloud</h1><p>Python {version} với FastAPI, qua uvicorn.</p>"


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
