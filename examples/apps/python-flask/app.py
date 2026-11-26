"""A small Flask app. minicloud serves it with gunicorn on port 80."""

import platform

from flask import Flask

app = Flask(__name__)


@app.get("/")
def home() -> str:
    version = platform.python_version()
    return f"<h1>Xin chào từ minicloud</h1><p>Python {version} với Flask, qua gunicorn.</p>"
