"""The built canvas is served by the real local application."""

import re

from fastapi.testclient import TestClient

from app.main import app


def test_canvas_serves_its_own_built_assets_and_local_boundary(tmp_path, monkeypatch):
    for key, name in [
        ("POSTER_DATA_DIR", "posters"),
        ("POSTER_EXECUTION_DB", "executions.sqlite3"),
        ("POSTER_CHECKPOINT_DB", "checkpoints.sqlite3"),
        ("POSTER_DESIGN_DB", "designs.sqlite3"),
        ("POSTER_DESIGN_ASSET_DIR", "design-assets"),
    ]:
        monkeypatch.setenv(key, str(tmp_path / name))
    with TestClient(app) as client:
        response = client.get("/canvas")
        assert response.status_code == 200
        assert 'id="canvas-root"' in response.text
        assets = re.findall(r'(?:src|href)="(/static/canvas/[^\"]+)"', response.text)
        assert assets
        assert any(path.endswith(".js") for path in assets)
        for path in assets:
            assert client.get(path).status_code == 200
        assert "create-draft.js" not in response.text
        assert 'href="/canvas"' in client.get("/").text
        assert client.get("/canvas", headers={"Host": "example.com"}).status_code == 403
