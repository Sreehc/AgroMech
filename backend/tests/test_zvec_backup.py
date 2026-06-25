import tarfile
from datetime import datetime, timedelta, timezone

from agromech_api.config import Settings
from agromech_api.zvec_backup import (
    RestoreStatus,
    backup_zvec,
    cleanup_zvec_backups,
    restore_zvec_backup,
)


def zvec_settings(tmp_path, **overrides) -> Settings:
    base = {
        "_env_file": None,
        "file_storage_backend": "local",
        "graph_backend": "local",
        "vector_backend": "zvec",
        "model_provider": "local",
        "embedding_provider": "local",
        "zvec_path": str(tmp_path / "zvec"),
        "zvec_backup_path": str(tmp_path / "backups" / "zvec"),
        "zvec_backup_retention_days": 7,
    }
    base.update(overrides)
    return Settings(**base)


def test_backup_zvec_creates_restoreable_archive(tmp_path) -> None:
    settings = zvec_settings(tmp_path)
    zvec_path = tmp_path / "zvec"
    zvec_path.mkdir()
    (zvec_path / "agromech_chunks.json").write_text('{"vectors": {"chunk-1": {}}}', encoding="utf-8")

    result = backup_zvec(settings, now=datetime(2026, 6, 23, 8, 30, tzinfo=timezone.utc))

    assert result.created is True
    assert result.archive_path.name == "zvec-20260623T083000Z.tar.gz"
    assert result.archive_path.exists()
    with tarfile.open(result.archive_path, "r:gz") as archive:
        assert "agromech_chunks.json" in archive.getnames()

    restore_target = tmp_path / "restored-zvec"
    restore_result = restore_zvec_backup(settings, result.archive_path, restore_path=restore_target)

    assert restore_result.status == RestoreStatus.RESTORED
    assert (restore_target / "agromech_chunks.json").read_text(encoding="utf-8") == '{"vectors": {"chunk-1": {}}}'


def test_cleanup_zvec_backups_removes_only_expired_archives(tmp_path) -> None:
    settings = zvec_settings(tmp_path)
    backup_path = tmp_path / "backups" / "zvec"
    backup_path.mkdir(parents=True)
    expired = backup_path / "zvec-20260610T000000Z.tar.gz"
    retained = backup_path / "zvec-20260620T000000Z.tar.gz"
    unrelated = backup_path / "notes.txt"
    for path in [expired, retained, unrelated]:
        path.write_text("x", encoding="utf-8")

    result = cleanup_zvec_backups(
        settings,
        now=datetime(2026, 6, 23, tzinfo=timezone.utc),
        retention=timedelta(days=7),
    )

    assert result.deleted_paths == [expired]
    assert not expired.exists()
    assert retained.exists()
    assert unrelated.exists()


def test_restore_zvec_backup_reports_rebuild_required_on_failure(tmp_path) -> None:
    settings = zvec_settings(tmp_path)
    missing_archive = tmp_path / "backups" / "zvec" / "missing.tar.gz"

    result = restore_zvec_backup(settings, missing_archive)

    assert result.status == RestoreStatus.REBUILD_REQUIRED
    assert "missing" in result.message
