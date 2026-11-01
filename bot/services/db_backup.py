"""
Резервні копії SQLite БД проєкту + надсилання адмінам у Telegram.

Архів: databases/backups/cosmy_db_backup_YYYYMMDD_HHMMSS.zip
Якщо архів більший за ліміт Telegram — надсилається кількома частинами (.part001, …).
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import tempfile
import zipfile
import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Sequence
from urllib.parse import unquote
from zoneinfo import ZoneInfo

from config import (
    CLIENTS_DATABASE_URL,
    DATABASE_URL,
    PRODUCTS_DATABASE_URL,
    administrators,
    BOT_TOKEN,
)

logger = logging.getLogger(__name__)

KYIV = ZoneInfo("Europe/Kyiv")
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Telegram Bot API: до 50 МБ на документ; беремо запас.
DEFAULT_TELEGRAM_MAX_BYTES = 48 * 1024 * 1024


def _backup_setting(name: str, env_key: str, default: str) -> str:
    """Читає налаштування з config.py або .env (сумісність зі старими деплоями)."""
    import config as cfg

    if hasattr(cfg, name):
        return str(getattr(cfg, name))
    return os.getenv(env_key, default)


def _backup_setting_int(name: str, env_key: str, default: int) -> int:
    return int(_backup_setting(name, env_key, str(default)))


def backup_dir_setting() -> str:
    return _backup_setting("DB_BACKUP_DIR", "DB_BACKUP_DIR", "./databases/backups")


def backup_keep_count_setting() -> int:
    return _backup_setting_int("DB_BACKUP_KEEP_COUNT", "DB_BACKUP_KEEP_COUNT", 5)


def backup_telegram_max_bytes_setting() -> int:
    return _backup_setting_int(
        "DB_BACKUP_TELEGRAM_MAX_BYTES",
        "DB_BACKUP_TELEGRAM_MAX_BYTES",
        DEFAULT_TELEGRAM_MAX_BYTES,
    )


def _sqlite_path_from_url(url: str) -> Path:
    prefix = "sqlite+aiosqlite:///"
    if not url.startswith(prefix):
        raise ValueError(f"Очікується SQLite URL, отримано: {url[:64]}…")
    if url.startswith(prefix + "//"):
        inner = url[len(prefix + "//") :]
        if inner.startswith("/"):
            return Path(unquote(inner))
    rest = unquote(url[len(prefix) :].lstrip("/"))
    return (PROJECT_ROOT / rest).resolve()


def configured_database_sources() -> list[tuple[str, Path]]:
    """Пари (лейбл, шлях) для всіх налаштованих SQLite БД."""
    return [
        ("finance_bot", _sqlite_path_from_url(DATABASE_URL)),
        ("products", _sqlite_path_from_url(PRODUCTS_DATABASE_URL)),
        ("cosmy_clients", _sqlite_path_from_url(CLIENTS_DATABASE_URL)),
    ]


def backup_dir() -> Path:
    path = Path(backup_dir_setting())
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def backup_sqlite_consistent(src: Path, dest: Path) -> None:
    """Hot backup через API SQLite (безпечно при активній БД)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    src_uri = f"file:{src.resolve()}?mode=ro"
    with sqlite3.connect(src_uri, uri=True, timeout=120) as source:
        source.execute("PRAGMA busy_timeout=120000")
        with sqlite3.connect(dest, timeout=120) as target:
            # Короткими шматками, щоб синк і веб встигали між сторінками.
            source.backup(target, pages=50, sleep=0.05)


def _human_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes / (1024 * 1024):.2f} MB"


def split_file(path: Path, chunk_size: int, out_dir: Path | None = None) -> list[Path]:
    """Розбити файл на частини не більше chunk_size байт."""
    if chunk_size <= 0:
        raise ValueError("chunk_size має бути > 0")
    target_dir = out_dir or path.parent
    target_dir.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    with path.open("rb") as src:
        index = 1
        while True:
            chunk = src.read(chunk_size)
            if not chunk:
                break
            part_path = target_dir / f"{path.name}.part{index:03d}"
            part_path.write_bytes(chunk)
            parts.append(part_path)
            index += 1
    return parts


@dataclass
class BackupRunResult:
    archive_path: Path | None = None
    backed_up: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    telegram_sent: bool = False
    telegram_parts: int = 0
    telegram_delivered_to: list[int] = field(default_factory=list)
    telegram_failed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Успіх створення архіву (Telegram може бути частковим)."""
        return bool(self.backed_up) and not any(
            e for e in self.errors if not str(e).startswith("Telegram:")
        )


def create_backup_archive(
    *,
    when: datetime | None = None,
    dest_dir: Path | None = None,
) -> tuple[Path, list[str], list[str]]:
    """
    Створити zip-архів з консистентними копіями БД.
    Повертає (шлях до zip, список успішних лейблів, список пропущених).
    """
    moment = when or datetime.now(KYIV)
    out_dir = dest_dir or backup_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = moment.strftime("%Y%m%d_%H%M%S")
    archive_path = out_dir / f"cosmy_db_backup_{stamp}.zip"

    backed_up: list[str] = []
    skipped: list[str] = []

    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for label, db_path in configured_database_sources():
            if not db_path.is_file():
                skipped.append(f"{label} ({db_path.name} — файл відсутній)")
                continue
            try:
                tmp_copy = out_dir / f".tmp_{label}_{stamp}.db"
                backup_sqlite_consistent(db_path, tmp_copy)
                zf.write(tmp_copy, arcname=f"{label}/{db_path.name}")
                backed_up.append(f"{label} ({db_path.name}, {_human_size(db_path.stat().st_size)})")
            except Exception as exc:
                skipped.append(f"{label}: {exc}")
            finally:
                tmp = out_dir / f".tmp_{label}_{stamp}.db"
                if tmp.is_file():
                    tmp.unlink(missing_ok=True)

    if not backed_up:
        archive_path.unlink(missing_ok=True)
        raise RuntimeError("Не вдалося скопіювати жодну БД")

    return archive_path, backed_up, skipped


def list_backup_archives(directory: Path | None = None) -> list[Path]:
    """Zip-архіви в папці бекапів, спочатку найновіший."""
    folder = directory or backup_dir()
    if not folder.is_dir():
        return []
    return sorted(
        (p for p in folder.glob("cosmy_db_backup_*.zip") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def _sqlite_sidecars(path: Path) -> list[Path]:
    return [
        path,
        Path(str(path) + "-wal"),
        Path(str(path) + "-shm"),
        Path(str(path) + "-journal"),
    ]


def _sqlite_integrity(path: Path) -> None:
    uri = f"file:{path.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        row = conn.execute("PRAGMA integrity_check").fetchone()
    if not row or row[0] != "ok":
        detail = row[0] if row else "порожня відповідь"
        raise RuntimeError(f"Файл пошкоджений: {path.name} ({detail})")


def _archive_member(zf: zipfile.ZipFile, label: str, filename: str) -> str | None:
    wanted = f"{label}/{filename}"
    matches: list[str] = []
    for info in zf.infolist():
        if info.is_dir():
            continue
        name = info.filename.replace("\\", "/")
        parts = Path(name).parts
        if name.startswith("/") or ".." in parts:
            continue
        if name == wanted or name.endswith("/" + wanted):
            matches.append(info.filename)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise RuntimeError(f"В архіві кілька файлів для {wanted}")
    return None


def _park_files(files: list[Path], aside_dir: Path) -> list[tuple[Path, Path]]:
    aside_dir.mkdir(parents=True, exist_ok=True)
    moved: list[tuple[Path, Path]] = []
    for src in files:
        if not src.is_file():
            continue
        target = aside_dir / src.name
        os.replace(src, target)
        moved.append((src, target))
    return moved


def _unpark_files(moved: list[tuple[Path, Path]]) -> None:
    for original, parked in reversed(moved):
        if not parked.is_file():
            continue
        original.parent.mkdir(parents=True, exist_ok=True)
        if original.exists():
            original.unlink()
        os.replace(parked, original)


@dataclass
class RestoreResult:
    archive: Path
    restored: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    aside_dir: Path | None = None


def restore_databases_from_archive(archive: Path) -> RestoreResult:
    """
    Повертає finance / products / clients з zip на шляхи з конфігурації.
    Поточні файли (і -wal/-shm) спершу ховає в databases/backups/pre_restore_*.
    Веб і бот мають бути зупинені.
    """
    archive = archive.resolve()
    if not archive.is_file():
        raise FileNotFoundError(f"Архів не знайдено: {archive}")

    sources = configured_database_sources()
    result = RestoreResult(archive=archive)
    stamp = datetime.now(KYIV).strftime("%Y%m%d_%H%M%S")
    aside_root = backup_dir() / f"pre_restore_{stamp}"

    with zipfile.ZipFile(archive, "r") as zf:
        bad = zf.testzip()
        if bad:
            raise RuntimeError(f"Архів пошкоджений, перший битий файл: {bad}")
        planned: list[tuple[str, Path, str]] = []
        for label, db_path in sources:
            member = _archive_member(zf, label, db_path.name)
            if member is None:
                result.skipped.append(f"{label} ({db_path.name} немає в архіві)")
                continue
            planned.append((label, db_path, member))
        if not planned:
            raise RuntimeError("В архіві немає відомих баз finance_bot, products, cosmy_clients")

        with tempfile.TemporaryDirectory(prefix="cosmy_restore_") as tmp_name:
            tmp = Path(tmp_name)
            extracted: list[tuple[str, Path, Path]] = []
            for label, db_path, member in planned:
                dest_tmp = tmp / label / db_path.name
                dest_tmp.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, dest_tmp.open("wb") as out:
                    shutil.copyfileobj(src, out)
                _sqlite_integrity(dest_tmp)
                extracted.append((label, db_path, dest_tmp))

            parked: list[tuple[Path, Path]] = []
            installed: list[Path] = []
            try:
                for label, db_path, src in extracted:
                    parked.extend(_park_files(_sqlite_sidecars(db_path), aside_root / label))
                    backup_sqlite_consistent(src, db_path)
                    _sqlite_integrity(db_path)
                    installed.append(db_path)
                    result.restored.append(f"{label} → {db_path}")
            except Exception:
                for path in installed:
                    for side in _sqlite_sidecars(path):
                        if side.is_file():
                            side.unlink()
                _unpark_files(parked)
                if aside_root.is_dir() and not any(aside_root.rglob("*")):
                    aside_root.rmdir()
                raise

    result.aside_dir = aside_root
    return result


def prune_old_backups(keep: int | None = None) -> int:
    limit = keep if keep is not None else backup_keep_count_setting()
    if limit <= 0:
        return 0
    directory = backup_dir()
    archives = sorted(
        directory.glob("cosmy_db_backup_*.zip"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    removed = 0
    for old in archives[limit:]:
        old.unlink(missing_ok=True)
        for part in directory.glob(f"{old.name}.part*"):
            part.unlink(missing_ok=True)
        removed += 1
    return removed


async def send_path_to_admins(
    bot,
    file_path: Path,
    *,
    caption: str,
    max_bytes: int,
    admin_ids: Sequence[int],
) -> tuple[int, list[int], list[str]]:
    """
    Надіслати файл адмінам; при перевищенні max_bytes — частинами.
    Помилки (chat not found тощо) — лише для цього admin_id, інші все одно отримують.
    Повертає (кількість частин, id успішних, описи помилок).
    """
    from aiogram.types import FSInputFile

    delivered: list[int] = []
    failed: list[str] = []
    size = file_path.stat().st_size

    if size <= max_bytes:
        doc = FSInputFile(file_path)
        for admin_id in admin_ids:
            try:
                await bot.send_document(admin_id, doc, caption=caption)
                delivered.append(admin_id)
            except Exception as exc:
                msg = f"{admin_id}: {exc}"
                failed.append(msg)
                logger.warning("Не вдалося надіслати бекап адміну %s: %s", admin_id, exc)
        return 1, delivered, failed

    parts = split_file(file_path, max_bytes, file_path.parent)
    total = len(parts)
    join_hint = (
        f"З'єднати на сервері:\n"
        f"<code>cat {file_path.name}.part* > {file_path.name}</code>"
    )
    intro = (
        f"{caption}\n\n"
        f"📦 Розмір архіву: <b>{_human_size(size)}</b> — надсилаю <b>{total}</b> частин.\n"
        f"{join_hint}"
    )

    for admin_id in admin_ids:
        try:
            await bot.send_message(admin_id, intro, parse_mode="HTML")
            for index, part in enumerate(parts, start=1):
                part_caption = f"{file_path.name} — частина {index}/{total}"
                doc = FSInputFile(part)
                await bot.send_document(admin_id, doc, caption=part_caption)
            delivered.append(admin_id)
        except Exception as exc:
            msg = f"{admin_id}: {exc}"
            failed.append(msg)
            logger.warning("Не вдалося надіслати бекап адміну %s: %s", admin_id, exc)

    for part in parts:
        part.unlink(missing_ok=True)

    return total, delivered, failed


async def run_database_backup(
    *,
    notify_telegram: bool = True,
    max_telegram_bytes: int | None = None,
) -> BackupRunResult:
    """Повний цикл: архів → Telegram адмінам → прибирання старих копій."""
    result = BackupRunResult()
    limit = max_telegram_bytes or backup_telegram_max_bytes_setting()

    try:
        archive, backed_up, skipped = await asyncio.to_thread(create_backup_archive)
        result.archive_path = archive
        result.backed_up = backed_up
        result.skipped = skipped
    except Exception as exc:
        result.errors.append(str(exc))
        logger.error("❌ Помилка створення резервної копії БД: %s", exc, exc_info=True)
        return result

    try:
        removed = prune_old_backups()
        if removed:
            logger.info("🗑 Видалено старих архівів резервних копій: %s", removed)
    except Exception as exc:
        logger.warning("Не вдалося прибрати старі бекапи: %s", exc)

    if not notify_telegram:
        return result

    await notify_backup_telegram(result, max_telegram_bytes=limit)
    return result


async def notify_backup_telegram(
    result: BackupRunResult,
    *,
    max_telegram_bytes: int | None = None,
) -> BackupRunResult:
    """Надіслати вже зібраний архів. Не чіпає SQLite — можна викликати без лока фінансів."""
    limit = max_telegram_bytes or backup_telegram_max_bytes_setting()
    archive = result.archive_path
    if archive is None or not archive.is_file():
        result.errors.append("Telegram: архів відсутній")
        return result

    if not BOT_TOKEN or not administrators:
        result.errors.append("BOT_TOKEN або ADMINISTRATORS не налаштовані — Telegram пропущено")
        return result

    when_label = datetime.now(KYIV).strftime("%d.%m.%Y %H:%M")
    lines = [
        f"💾 <b>Резервна копія БД</b> ({when_label})",
        "",
        "<b>У архіві:</b>",
    ]
    lines.extend(f"• {row}" for row in result.backed_up)
    if result.skipped:
        lines.append("")
        lines.append("<b>Пропущено:</b>")
        lines.extend(f"• {row}" for row in result.skipped)
    lines.append("")
    lines.append(f"📁 <code>{result.archive_path.name}</code> ({_human_size(result.archive_path.stat().st_size)})")
    caption = "\n".join(lines)

    from aiogram import Bot

    bot = Bot(token=BOT_TOKEN)
    try:
        parts, delivered, failed = await send_path_to_admins(
            bot,
            archive,
            caption=caption,
            max_bytes=limit,
            admin_ids=administrators,
        )
        result.telegram_parts = parts
        result.telegram_delivered_to = delivered
        result.telegram_failed = failed
        result.telegram_sent = bool(delivered)
        if delivered:
            logger.info(
                "✅ Резервну копію надіслано адмінам %s (%s файл(ів) на адміна)",
                delivered,
                parts,
            )
        if failed:
            logger.warning("⚠️ Telegram бекап — не вдалося для: %s", "; ".join(failed))
        if not delivered and administrators:
            result.errors.append(
                "Telegram: не вдалося надіслати жодному адміну — "
                + "; ".join(failed or ["невідома помилка"])
            )
    except Exception as exc:
        result.errors.append(f"Telegram: {exc}")
        logger.error("❌ Критична помилка Telegram при бекапі: %s", exc, exc_info=True)
    finally:
        await bot.session.close()

    return result
