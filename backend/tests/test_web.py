from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.web import install_web, web_file


def test_the_api_alone_does_not_serve_the_website():
    with TestClient(app) as client:
        assert client.get("/").status_code == 404
        assert client.get("/api/health").json() == {"ok": True}


def test_one_process_serves_the_site_and_the_api(tmp_path: Path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("HOME", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("js", encoding="utf-8")
    site = FastAPI()

    @site.get("/api/health")
    def health() -> dict:
        return {"ok": True}

    install_web(site, dist)
    client = TestClient(site)
    assert client.get("/").text == "HOME"
    assert client.get("/topics/1").text == "HOME"
    assert client.get("/assets/app.js").text == "js"
    assert client.get("/api/health").json() == {"ok": True}
    assert client.get("/api/missing").status_code == 404


def test_a_path_outside_the_site_is_rejected(tmp_path: Path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("HOME", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("no", encoding="utf-8")
    with pytest.raises(HTTPException):
        web_file(dist, "../secret.txt")
