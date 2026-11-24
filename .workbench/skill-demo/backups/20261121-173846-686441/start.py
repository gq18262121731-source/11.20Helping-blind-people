import os
import webbrowser
from pathlib import Path
from threading import Thread
from time import sleep
from urllib.request import urlopen

import uvicorn

ROOT = Path(__file__).resolve().parents[3]
os.chdir(ROOT)
os.environ["DATABASE_PATH"] = str(ROOT / ".workbench/skill-demo/demo.sqlite")

from tools.skill_demo.support import attach_demo
from backend.main import app

attach_demo(app)


def open_when_ready():
    for attempt in range(90):
        try:
            with urlopen("http://127.0.0.1:8765/api/health", timeout=1):
                webbrowser.open("http://127.0.0.1:8765/skill-demo")
            return
        except OSError:
            sleep(1)
    print("请检查服务日志，再手动打开浏览器。")


if __name__ == "__main__":
    Thread(target=open_when_ready, daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=8765)
