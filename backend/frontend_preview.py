"""Opt-in live preview for browsers displaying this computer's frontend."""

import hashlib
from pathlib import Path

from fastapi import APIRouter, Response
from fastapi.responses import FileResponse

from .config import settings


router = APIRouter()
FRONTEND = Path(__file__).resolve().parent.parent / "frontend"


def fingerprint(paths):
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.relative_to(FRONTEND).as_posix().encode("utf-8"))
        try:
            content = path.read_bytes()
        except FileNotFoundError:
            content = b""
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()


@router.get("/live-preview.js")
async def preview_script():
    return FileResponse(FRONTEND / "live-preview.js", headers={"Cache-Control": "no-store"})


@router.get("/api/dev/frontend-version")
async def frontend_version(response: Response):
    response.headers["Cache-Control"] = "no-store"
    if not settings.frontend_live_preview:
        return {"enabled": False}
    styles = {"/" + path.name: fingerprint([path]) for path in FRONTEND.glob("*.css")}
    reload_files = [*FRONTEND.glob("*.html"), *FRONTEND.glob("*.js")]
    reload_files.extend(path for path in (FRONTEND / "assets").rglob("*") if path.is_file())
    return {"enabled": True, "styles": styles, "reload": fingerprint(reload_files)}
