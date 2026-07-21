from app import db, repository


def test_music_description_and_license_are_persisted(tmp_path):
    conn = db.connect(str(tmp_path / "app.db"))
    db.init_schema(conn)
    music = repository.create_music(
        conn, name="Track", file_path="track.mp3", duration_sec=10,
        description="Background for YouTube", license="CC BY 4.0",
    )
    assert music.description == "Background for YouTube"
    assert music.license == "CC BY 4.0"
    repository.update_music_metadata(conn, music.id, "Updated", "Royalty-free")
    updated = repository.get_music(conn, music.id)
    assert updated.description == "Updated"
    assert updated.license == "Royalty-free"
