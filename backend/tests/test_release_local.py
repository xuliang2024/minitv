import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.release_local import publish, rollback


def test_pointer_switches_only_when_health_ok(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("v2")
    releases = tmp_path / "releases"
    releases.mkdir()
    monkeypatch.setattr("scripts.release_local.health_ok", lambda url: False)
    try:
        publish(dist, releases, "bbb", "http://health")
        raised = False
    except SystemExit:
        raised = True
    assert raised
    assert not (releases / "current").exists()

    monkeypatch.setattr("scripts.release_local.health_ok", lambda url: True)
    (dist / "index.html").write_text("v1")
    publish(dist, releases, "aaa", "http://health")
    (dist / "index.html").write_text("v2")
    publish(dist, releases, "bbb", "http://health")
    assert (releases / "current").read_text().strip() == "bbb"
    rollback(releases, "aaa")
    assert (releases / "current").read_text().strip() == "aaa"
    assert (releases / "aaa" / "index.html").read_text() == "v1"
