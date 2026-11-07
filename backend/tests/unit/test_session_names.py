"""Naming sessions so they can be told apart in Recent sessions."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient



@pytest.fixture(autouse=True)
def reset_state(monkeypatch, tmp_path):
    import services.session_service as session_service
    monkeypatch.setattr(session_service, "_state", {})
    monkeypatch.setattr(session_service, "SESSION_FILE", tmp_path / "last_session.json")
    monkeypatch.setattr(session_service, "RECENT_SESSIONS_FILE", tmp_path / "recent_sessions.json")
    monkeypatch.setattr(session_service, "APP_DIR", tmp_path)


@pytest.fixture
def client():
    from main import app
    return TestClient(app, raise_server_exceptions=True)


def _setup(client, camtrap_dir, out: Path, name: str = "") -> str:
    r = client.post("/api/setup", json={
        "camtrap_dir": str(camtrap_dir), "output_dir": str(out), "session_name": name,
        "target_species": ["Vulpes vulpes"], "study_start": "2025-11-01", "study_end": "2025-11-10",
    })
    assert r.status_code == 200, r.text
    return r.json()["session_dir"]


def test_name_given_at_setup_is_shown_in_recent_sessions(client, camtrap_dir, tmp_path):
    sd = _setup(client, camtrap_dir, tmp_path / "out", "Doñana 2023 combined")
    assert json.loads((Path(sd) / "config.json").read_text())["session_name"] == "Doñana 2023 combined"
    recent = client.get("/api/session/recent").json()
    assert recent[0]["session_dir"] == sd and recent[0]["session_name"] == "Doñana 2023 combined"
    assert client.get("/api/state").json()["config"]["session_name"] == "Doñana 2023 combined"


def test_sessions_without_a_name_still_work(client, camtrap_dir, tmp_path):
    _setup(client, camtrap_dir, tmp_path / "out")
    assert client.get("/api/session/recent").json()[0]["session_name"] == ""


def test_rename_a_recent_session_and_the_active_one(client, camtrap_dir, tmp_path):
    first = _setup(client, camtrap_dir, tmp_path / "a", "first")
    second = _setup(client, camtrap_dir, tmp_path / "b")          # now the active one
    r = client.put("/api/session/name", json={"session_dir": first, "name": "  spring   2023  "})
    assert r.status_code == 200 and r.json()["session_name"] == "spring 2023"
    assert json.loads((Path(first) / "config.json").read_text())["session_name"] == "spring 2023"
    # empty session_dir = the active session, also updated in memory
    assert client.put("/api/session/name", json={"name": "autumn"}).status_code == 200
    assert client.get("/api/state").json()["config"]["session_name"] == "autumn"
    names = {s["session_dir"]: s["session_name"] for s in client.get("/api/session/recent").json()}
    assert names == {first: "spring 2023", second: "autumn"}
    # an empty name clears it
    client.put("/api/session/name", json={"session_dir": first, "name": ""})
    assert json.loads((Path(first) / "config.json").read_text())["session_name"] == ""


def test_only_known_sessions_can_be_renamed(client, camtrap_dir, tmp_path):
    _setup(client, camtrap_dir, tmp_path / "a")
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "config.json").write_text("{}")
    r = client.put("/api/session/name", json={"session_dir": str(other), "name": "x"})
    assert r.status_code == 400
    assert json.loads((other / "config.json").read_text()) == {}
