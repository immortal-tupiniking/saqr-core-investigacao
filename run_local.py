from __future__ import annotations

import threading
import webbrowser

import uvicorn


def open_browser() -> None:
    webbrowser.open("http://127.0.0.1:8000")


if __name__ == "__main__":
    threading.Timer(1.2, open_browser).start()
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)
