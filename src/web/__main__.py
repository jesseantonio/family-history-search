"""python -m src.web [--port 8000] [--no-browser]"""

from __future__ import annotations

import argparse
import threading
import webbrowser

import uvicorn


def main() -> None:
    p = argparse.ArgumentParser(description="Interface web do Family History Search.")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--no-browser", action="store_true", help="não abre o navegador automaticamente")
    a = p.parse_args()
    url = f"http://{a.host}:{a.port}/"
    if not a.no_browser:
        threading.Timer(1.2, webbrowser.open, args=(url,)).start()
    print(f"Interface em {url}  (Ctrl+C para encerrar)")
    uvicorn.run("src.web.server:app", host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
