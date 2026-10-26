import json
from pathlib import Path

from app.releases import release_payload


def test_no_phone_update_is_advertised_until_the_apk_is_there(tmp_path: Path):
    assert release_payload(tmp_path, "https://learn.sasanksankaran.in")["apk_url"] is None


def test_a_published_apk_is_a_newer_version_the_phone_can_download(tmp_path: Path):
    (tmp_path / "learning-revision.apk").write_bytes(b"apk")
    (tmp_path / "release.json").write_text(
        json.dumps({"version_code": 3, "version_name": "1.2", "notes": "Photo answers."}),
        encoding="utf-8",
    )
    payload = release_payload(tmp_path, "https://learn.sasanksankaran.in/")
    assert payload["version_code"] == 3
    assert payload["version_name"] == "1.2"
    assert payload["apk_url"] == "https://learn.sasanksankaran.in/releases/learning-revision.apk"
    assert payload["notes"] == "Photo answers."
