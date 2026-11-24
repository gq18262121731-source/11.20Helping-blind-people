from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend import frontend_preview
from backend.config import settings


@pytest.fixture
def preview(tmp_path, monkeypatch):
    root = tmp_path / "frontend"
    root.mkdir()
    (root / "assets").mkdir()
    (root / "login.html").write_text("<html></html>", encoding="utf-8")
    (root / "brand.css").write_text("body {color: blue}", encoding="utf-8")
    (root / "live-preview.js").write_text("/* preview */", encoding="utf-8")
    monkeypatch.setattr(frontend_preview, "FRONTEND", root)
    monkeypatch.setattr(settings, "frontend_live_preview", True)
    app = FastAPI()
    app.include_router(frontend_preview.router)
    with TestClient(app) as client:
        yield client, root


def test_preview_is_disabled_when_not_enabled(preview, monkeypatch):
    client, _ = preview
    monkeypatch.setattr(settings, "frontend_live_preview", False)
    result = client.get("/api/dev/frontend-version")
    assert result.json() == {"enabled": False}
    assert result.headers["cache-control"] == "no-store"


def test_css_changes_only_replace_styles(preview):
    client, root = preview
    before = client.get("/api/dev/frontend-version").json()
    (root / "brand.css").write_text("body {color: aqua}", encoding="utf-8")
    after = client.get("/api/dev/frontend-version").json()
    assert before["enabled"] is True
    assert before["styles"]["/brand.css"] != after["styles"]["/brand.css"]
    assert before["reload"] == after["reload"]
    assert client.get("/api/dev/frontend-version").json() == after


@pytest.mark.parametrize("filename", ["login.html", "login.js", "assets/hat.png"])
def test_markup_scripts_and_images_refresh_the_page(preview, filename):
    client, root = preview
    before = client.get("/api/dev/frontend-version").json()
    (root / filename).write_text("changed preview asset", encoding="utf-8")
    after = client.get("/api/dev/frontend-version").json()
    assert before["reload"] != after["reload"]
    assert before["styles"] == after["styles"]


def test_preview_script_is_not_cached(preview):
    client, _ = preview
    result = client.get("/live-preview.js")
    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store"
