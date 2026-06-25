from __future__ import annotations

import shutil
import tarfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path

from agromech_api.config import Settings

BACKUP_PREFIX = "zvec-"
BACKUP_SUFFIX = ".tar.gz"


class RestoreStatus(StrEnum):
    RESTORED = "restored"
    REBUILD_REQUIRED = "rebuild_required"


@dataclass(frozen=True)
class BackupResult:
    archive_path: Path
    created: bool
    message: str


@dataclass(frozen=True)
class CleanupResult:
    deleted_paths: list[Path]


@dataclass(frozen=True)
class RestoreResult:
    status: RestoreStatus
    message: str
    restore_path: Path


def backup_zvec(settings: Settings, *, now: datetime | None = None) -> BackupResult:
    source_path = Path(settings.zvec_path)
    backup_path = Path(settings.zvec_backup_path)
    timestamp = _normalize_now(now).strftime("%Y%m%dT%H%M%SZ")
    archive_path = backup_path / f"{BACKUP_PREFIX}{timestamp}{BACKUP_SUFFIX}"

    if not source_path.exists():
        return BackupResult(
            archive_path=archive_path,
            created=False,
            message=f"Zvec path does not exist: {source_path}",
        )
    if not source_path.is_dir():
        return BackupResult(
            archive_path=archive_path,
            created=False,
            message=f"Zvec path is not a directory: {source_path}",
        )

    backup_path.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "w:gz") as archive:
        for child in sorted(source_path.iterdir()):
            archive.add(child, arcname=child.name)
    return BackupResult(archive_path=archive_path, created=True, message="backup_created")


def cleanup_zvec_backups(
    settings: Settings,
    *,
    now: datetime | None = None,
    retention: timedelta | None = None,
) -> CleanupResult:
    backup_path = Path(settings.zvec_backup_path)
    if not backup_path.exists():
        return CleanupResult(deleted_paths=[])

    cutoff = _normalize_now(now) - (
        retention if retention is not None else timedelta(days=settings.zvec_backup_retention_days)
    )
    deleted_paths: list[Path] = []
    for archive_path in sorted(backup_path.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}")):
        archive_time = _parse_backup_time(archive_path)
        if archive_time is None or archive_time >= cutoff:
            continue
        archive_path.unlink()
        deleted_paths.append(archive_path)
    return CleanupResult(deleted_paths=deleted_paths)


def restore_zvec_backup(
    settings: Settings,
    archive_path: str | Path,
    *,
    restore_path: str | Path | None = None,
) -> RestoreResult:
    archive_path = Path(archive_path)
    target_path = Path(restore_path) if restore_path is not None else Path(settings.zvec_path)
    if not archive_path.exists():
        return RestoreResult(
            status=RestoreStatus.REBUILD_REQUIRED,
            message=f"Zvec backup is missing: {archive_path}; rebuild vector index from Postgres chunks",
            restore_path=target_path,
        )

    temporary_path = target_path.with_name(f".{target_path.name}.restore-tmp")
    try:
        if temporary_path.exists():
            shutil.rmtree(temporary_path)
        temporary_path.mkdir(parents=True)
        with tarfile.open(archive_path, "r:gz") as archive:
            archive.extractall(temporary_path, filter="data")
        if target_path.exists():
            shutil.rmtree(target_path)
        temporary_path.replace(target_path)
    except (OSError, tarfile.TarError, ValueError) as exc:
        if temporary_path.exists():
            shutil.rmtree(temporary_path, ignore_errors=True)
        return RestoreResult(
            status=RestoreStatus.REBUILD_REQUIRED,
            message=f"Zvec restore failed: {exc}; rebuild vector index from Postgres chunks",
            restore_path=target_path,
        )

    return RestoreResult(
        status=RestoreStatus.RESTORED,
        message="restore_completed",
        restore_path=target_path,
    )


def _normalize_now(now: datetime | None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _parse_backup_time(path: Path) -> datetime | None:
    name = path.name
    if not name.startswith(BACKUP_PREFIX) or not name.endswith(BACKUP_SUFFIX):
        return None
    timestamp = name[len(BACKUP_PREFIX) : -len(BACKUP_SUFFIX)]
    try:
        return datetime.strptime(timestamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
