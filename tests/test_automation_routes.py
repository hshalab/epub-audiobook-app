from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app.config import settings
    from app.routes import video

    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    monkeypatch.setattr(settings, "default_background_image", str(tmp_path / "default.png"))
    monkeypatch.setattr(video, "_BACKGROUNDS_DIR", tmp_path / "backgrounds")
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def seeded_book(client):
    now = "2026-07-26T00:00:00+00:00"
    conn = client.app.state.conn
    book_id = conn.execute(
        "INSERT INTO book (title,original_filename,epub_path,patch_size,status,created_at,updated_at) "
        "VALUES ('B','b.epub','b.epub',1,'ready',?,?)",
        (now, now),
    ).lastrowid
    conn.commit()
    return book_id


def _seed_assets(client, *assets):
    conn = client.app.state.conn
    now = "2026-07-26T00:00:00+00:00"
    ids = []
    for name, media_type in assets:
        ids.append(conn.execute(
            "INSERT INTO media_assets (file_path,filename,media_type,created_at,updated_at) VALUES (?,?,?,?,?)",
            (f"/tmp/{name}", name, media_type, now, now),
        ).lastrowid)
    conn.commit()
    return ids


def test_settings_get_and_put_are_strict(client):
    assert client.get("/automation/settings").json()["video"]["fps"] == 30

    response = client.put("/automation/settings", json={"video": {"fps": 25}})
    assert response.status_code == 200
    assert response.json()["video"]["fps"] == 25
    assert client.get("/automation/settings").json()["video"]["fps"] == 25

    invalid = client.put("/automation/settings", json={"video": {"raw_args": "-y"}})
    assert invalid.status_code == 422
    assert invalid.json()["detail"][0]["type"] == "extra_forbidden"
    assert invalid.json()["detail"][0]["loc"][-1] == "raw_args"


def test_media_list_imports_background_files_and_preserves_old_shape(client):
    backgrounds = Path(client.app.state.conn.execute("PRAGMA database_list").fetchone()["file"]).parent / "backgrounds"
    backgrounds.mkdir()
    (backgrounds / "still.png").write_bytes(b"png")
    (backgrounds / "loop.mp4").write_bytes(b"mp4")
    (backgrounds / "ignore.txt").write_text("no")

    old_response = client.get("/video/backgrounds")
    assert old_response.status_code == 200
    assert set(old_response.json()) == {"backgrounds"}
    assert set(old_response.json()["backgrounds"][0]) == {"name", "path", "is_default", "is_video"}

    assets = client.get("/automation/media").json()["assets"]
    assert [(asset["filename"], asset["media_type"]) for asset in assets] == [
        ("loop.mp4", "video"),
        ("still.png", "image"),
    ]


def test_background_upload_preserves_keys_and_upserts_asset(client):
    response = client.post(
        "/video/upload-background",
        files={"file": ("cover.png", b"png", "image/png")},
    )
    assert response.status_code == 200
    assert set(response.json()) == {"name", "path"}
    assert response.json()["name"].endswith("_cover.png")
    assets = client.get("/automation/media").json()["assets"]
    assert [(asset["filename"], asset["file_path"]) for asset in assets] == [
        (response.json()["name"], response.json()["path"]),
    ]


def test_book_media_rejects_wrong_role_and_preserves_order(client, seeded_book):
    ids = _seed_assets(client, ("a.png", "image"), ("b.mp4", "video"))

    assert client.put(f"/books/{seeded_book}/automation/media/nope", json={"asset_ids": []}).status_code == 404
    response = client.put(
        f"/books/{seeded_book}/automation/media/background",
        json={"asset_ids": ids[::-1]},
    )
    assert response.status_code == 200
    assert [asset["id"] for asset in response.json()["assets"]] == ids[::-1]

    replacement = client.put(
        f"/books/{seeded_book}/automation/media/background",
        json={"asset_ids": ids},
    )
    assert [asset["id"] for asset in replacement.json()["assets"]] == ids

    empty = client.put(
        f"/books/{seeded_book}/automation/media/background",
        json={"asset_ids": []},
    )
    assert empty.status_code == 200
    assert empty.json()["assets"] == []


def test_book_media_accepts_ids_only_and_rejects_unknown_records(client, seeded_book):
    invalid = client.put(
        f"/books/{seeded_book}/automation/media/webcam",
        json={"asset_ids": [], "paths": ["C:/secret.txt"]},
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"][0]["type"] == "extra_forbidden"
    assert client.put(f"/books/{seeded_book}/automation/media/webcam", json={"asset_ids": [999]}).status_code == 404
    assert client.put("/books/999/automation/media/webcam", json={"asset_ids": []}).status_code == 404


def test_book_media_rejects_duplicate_ids_with_structured_422(client, seeded_book):
    asset_id = _seed_assets(client, ("clip.mp4", "video"))[0]
    response = client.put(
        f"/books/{seeded_book}/automation/media/background",
        json={"asset_ids": [asset_id, asset_id]},
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "asset_ids"]
    assert response.json()["detail"][0]["type"] == "value_error"


def test_webcam_accepts_video_and_rejects_image(client, seeded_book):
    image_id, video_id = _seed_assets(client, ("still.png", "image"), ("clip.mp4", "video"))
    invalid = client.put(
        f"/books/{seeded_book}/automation/media/webcam",
        json={"asset_ids": [image_id]},
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"][0]["loc"] == ["body", "asset_ids"]

    valid = client.put(
        f"/books/{seeded_book}/automation/media/webcam",
        json={"asset_ids": [video_id]},
    )
    assert valid.status_code == 200
    assert [asset["id"] for asset in valid.json()["assets"]] == [video_id]


@pytest.mark.parametrize("role,name,media_type", [
    ("background", "track.mp3", "audio"),
    ("background", "still.png", "video"),
    ("background", "clip.mp4", "image"),
    ("webcam", "still.png", "video"),
])
def test_book_media_rejects_unsupported_or_mislabeled_assets(
    client, seeded_book, role, name, media_type,
):
    asset_id = _seed_assets(client, (name, media_type))[0]
    response = client.put(
        f"/books/{seeded_book}/automation/media/{role}",
        json={"asset_ids": [asset_id]},
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "asset_ids"]
    assert response.json()["detail"][0]["type"] == "value_error"


def test_background_preview_path_safety_is_unchanged(client, tmp_path):
    secret = tmp_path / "secret.png"
    secret.write_bytes(b"secret")
    assert client.get("/video/backgrounds/preview", params={"path": str(secret)}).status_code == 404
    assert client.get("/video/backgrounds/preview", params={"path": "backgrounds/../secret.png"}).status_code == 404


def test_valid_and_default_background_preview(client, tmp_path):
    backgrounds = tmp_path / "backgrounds"
    backgrounds.mkdir()
    valid = backgrounds / "valid.png"
    default = tmp_path / "default.png"
    valid.write_bytes(b"valid")
    default.write_bytes(b"default")

    assert client.get("/video/backgrounds/preview", params={"path": str(valid)}).content == b"valid"
    assert client.get("/video/backgrounds/preview", params={"path": str(default)}).content == b"default"
    listed = client.get("/video/backgrounds").json()["backgrounds"]
    assert any(item["name"] == "__default__" and item["path"] == str(default) for item in listed)


def test_default_directory_does_not_allow_descendant_preview(client, tmp_path, monkeypatch):
    from app.config import settings

    default_directory = tmp_path / "defaults"
    default_directory.mkdir()
    descendant = default_directory / "secret.png"
    descendant.write_bytes(b"secret")
    monkeypatch.setattr(settings, "default_background_image", str(default_directory))

    response = client.get("/video/backgrounds/preview", params={"path": str(descendant)})
    assert response.status_code == 404


def test_symlink_escape_is_not_listed_or_upserted(client, tmp_path):
    backgrounds = tmp_path / "backgrounds"
    backgrounds.mkdir()
    secret = tmp_path / "secret.png"
    secret.write_bytes(b"secret")
    escaped = backgrounds / "escaped.png"
    try:
        escaped.symlink_to(secret)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    assert client.get("/video/backgrounds").json()["backgrounds"] == []
    assert client.get("/automation/media").json()["assets"] == []
    count = client.app.state.conn.execute("SELECT COUNT(*) FROM media_assets").fetchone()[0]
    assert count == 0
