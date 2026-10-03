import json
import sqlite3 as sql
from typing import Optional

from loguru import logger

from core.models import ApkInfo


# Порядок в SQL и в кортеже должен совпадать т.к. используется для SELECT.
_COLUMNS = (
    "apk_files_sha256",
    "apk_cert_sha256",
    "apk_time",
    "apk_size",
    "apk_arches",
    "apk_file_names",
    "app_name",
    "app_package",
    "app_version",
    "app_min_sdk",
    "app_target_sdk",
    "app_max_sdk",
    "target_version",
    "target_edition",
    "is_orig_cert",
)
_COLUMNS_SQL = ", ".join(_COLUMNS)
_COLUMNS_SET = frozenset(_COLUMNS)


def _row_to_apk_info(row) -> ApkInfo:
    """Распаковывает строку из БД в объект ApkInfo."""
    try:
        apk_arches = json.loads(row["apk_arches"]) if row["apk_arches"] else []
    except (json.JSONDecodeError, TypeError):
        logger.warning(f"Битый JSON в apk_arches для {row['apk_files_sha256']}")
        apk_arches = []

    try:
        apk_file_names = json.loads(row["apk_file_names"]) if row["apk_file_names"] else []
    except (json.JSONDecodeError, TypeError):
        logger.warning(f"Битый JSON в apk_file_names для {row['apk_files_sha256']}")
        apk_file_names = []

    raw_orig = row["is_orig_cert"]
    if raw_orig is None:
        is_orig_cert = None
    else:
        try:
            is_orig_cert = bool(int(raw_orig))
        except (ValueError, TypeError):
            is_orig_cert = bool(raw_orig)

    return ApkInfo(
        apk_files_sha256=row["apk_files_sha256"],
        apk_cert_sha256=row["apk_cert_sha256"],
        apk_time=row["apk_time"],
        apk_size=row["apk_size"],
        apk_arches=apk_arches,
        apk_file_names=apk_file_names,
        app_name=row["app_name"],
        app_package=row["app_package"],
        app_version=row["app_version"],
        app_min_sdk=row["app_min_sdk"],
        app_target_sdk=row["app_target_sdk"],
        app_max_sdk=row["app_max_sdk"],
        target_version=row["target_version"],
        target_edition=row["target_edition"],
        is_orig_cert=is_orig_cert,
    )


def init_db(conn):
    """Создание таблиц, триггеров, индексов."""

    conn.execute("PRAGMA foreign_keys = ON")

    conn.execute("""
    CREATE TABLE IF NOT EXISTS apk_files (
        apk_files_sha256 TEXT PRIMARY KEY,
        apk_cert_sha256 TEXT NOT NULL,
        apk_time TIMESTAMP NOT NULL,
        apk_size INTEGER NOT NULL,
        apk_arches TEXT NOT NULL,
        apk_file_names TEXT NOT NULL DEFAULT '[]',
        app_name TEXT NOT NULL,
        app_package TEXT NOT NULL,
        app_version TEXT NOT NULL,
        app_min_sdk TEXT,
        app_target_sdk TEXT,
        app_max_sdk TEXT,
        target_version TEXT,
        target_edition TEXT,
        is_orig_cert BOOLEAN
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS apk_files_duplicates (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        apk_files_sha256  TEXT NOT NULL,
        apk_file_name     TEXT NOT NULL,
        apk_file_path     TEXT NOT NULL,
        discovered_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(apk_files_sha256, apk_file_path),
        FOREIGN KEY (apk_files_sha256) REFERENCES apk_files(apk_files_sha256)
            ON DELETE CASCADE
    )
    """)

    conn.execute("""
    CREATE INDEX IF NOT EXISTS idx_duplicates_sha256
        ON apk_files_duplicates(apk_files_sha256)
    """)

    # Пересоздаём триггеры, чтобы при изменении логики не остались старые версии.
    conn.execute("DROP TRIGGER IF EXISTS trg_duplicates_ai")
    conn.execute("DROP TRIGGER IF EXISTS trg_duplicates_ad")

    conn.execute("""
    CREATE TRIGGER trg_duplicates_ai
    AFTER INSERT ON apk_files_duplicates
    BEGIN
        UPDATE apk_files
        SET apk_file_names = (
            SELECT json_group_array(apk_file_name)
            FROM (
                SELECT apk_file_name FROM apk_files_duplicates
                WHERE apk_files_sha256 = NEW.apk_files_sha256
                ORDER BY id
            )
        )
        WHERE apk_files_sha256 = NEW.apk_files_sha256;
    END
    """)

    conn.execute("""
    CREATE TRIGGER trg_duplicates_ad
    AFTER DELETE ON apk_files_duplicates
    BEGIN
        UPDATE apk_files
        SET apk_file_names = COALESCE(
            (
                SELECT json_group_array(apk_file_name)
                FROM (
                    SELECT apk_file_name FROM apk_files_duplicates
                    WHERE apk_files_sha256 = OLD.apk_files_sha256
                    ORDER BY id
                )
            ),
            '[]'
        )
        WHERE apk_files_sha256 = OLD.apk_files_sha256;
    END
    """)


def get_apk(sha256: str, conn) -> Optional[ApkInfo]:
    """Возвращает ApkInfo по sha256 или None."""
    cursor = conn.cursor()
    cursor.execute(
        f"SELECT {_COLUMNS_SQL} FROM apk_files WHERE apk_files_sha256 = ?",
        (sha256,),
    )
    row = cursor.fetchone()
    return _row_to_apk_info(row) if row is not None else None


def read_db(conn, limit: Optional[int] = None, **kwargs) -> list[ApkInfo]:
    """Считывает записи об APK по заданным параметрам."""

    invalid = set(kwargs) - _COLUMNS_SET
    if invalid:
        raise ValueError(f"Неизвестные колонки для фильтра: {sorted(invalid)}")

    cursor = conn.cursor()
    query = f"SELECT {_COLUMNS_SQL} FROM apk_files"
    parameters: list = []

    if kwargs:
        conditions = [f"{column} = ?" for column in kwargs]
        query += " WHERE " + " AND ".join(conditions)
        parameters = list(kwargs.values())

    if limit is not None:
        query += " LIMIT ?"
        parameters.append(limit)

    cursor.execute(query, tuple(parameters))
    return [_row_to_apk_info(row) for row in cursor.fetchall()]


def write_db(info: ApkInfo, conn):
    """Записывает основную информацию об APK."""

    data = {
        "apk_files_sha256": info.apk_files_sha256,
        "apk_cert_sha256": info.apk_cert_sha256,
        "apk_time": info.apk_time,
        "apk_size": info.apk_size,
        "apk_arches": json.dumps(info.apk_arches),
        "app_name": info.app_name,
        "app_package": info.app_package,
        "app_version": info.app_version,
        "app_min_sdk": info.app_min_sdk,
        "app_target_sdk": info.app_target_sdk,
        "app_max_sdk": info.app_max_sdk,
        "target_version": info.target_version,
        "target_edition": info.target_edition,
        "is_orig_cert": info.is_orig_cert,
    }

    conn.execute("""
    INSERT INTO apk_files (
        apk_files_sha256, apk_cert_sha256, apk_time, apk_size, apk_arches,
        app_name, app_package, app_version, app_min_sdk, app_target_sdk, app_max_sdk,
        target_version, target_edition, is_orig_cert
    ) VALUES (
        :apk_files_sha256, :apk_cert_sha256, :apk_time, :apk_size, :apk_arches,
        :app_name, :app_package, :app_version, :app_min_sdk, :app_target_sdk, :app_max_sdk,
        :target_version, :target_edition, :is_orig_cert
    )
    ON CONFLICT(apk_files_sha256) DO UPDATE SET
        apk_cert_sha256 = excluded.apk_cert_sha256,
        apk_time        = excluded.apk_time,
        apk_size        = excluded.apk_size,
        apk_arches      = excluded.apk_arches,
        app_name        = excluded.app_name,
        app_package     = excluded.app_package,
        app_version     = excluded.app_version,
        app_min_sdk     = excluded.app_min_sdk,
        app_target_sdk  = excluded.app_target_sdk,
        app_max_sdk     = excluded.app_max_sdk,
        target_version  = excluded.target_version,
        target_edition  = excluded.target_edition,
        is_orig_cert    = excluded.is_orig_cert
    """, data)


def add_db(sha256: str, conn, apk_file_name: str, apk_file_path: str) -> list[str]:
    """
    Регистрирует файл как дубль. Возвращает актуальный список имён файлов
    для данного APK (обновляется триггером trg_duplicates_ai).
    """
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR IGNORE INTO apk_files_duplicates
            (apk_files_sha256, apk_file_name, apk_file_path)
        VALUES (?, ?, ?)
    """, (sha256, apk_file_name, apk_file_path))

    cursor.execute("""
        SELECT apk_file_names FROM apk_files WHERE apk_files_sha256 = ?
    """, (sha256,))
    row = cursor.fetchone()
    if row is None or not row["apk_file_names"]:
        return []
    try:
        return json.loads(row["apk_file_names"])
    except (json.JSONDecodeError, TypeError):
        return []


def get_duplicates(sha256: str, conn) -> list[dict]:
    """Возвращает все записи дублей для APK."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT apk_file_name, apk_file_path, discovered_at
        FROM apk_files_duplicates
        WHERE apk_files_sha256 = ?
        ORDER BY id
    """, (sha256,))
    return [
        {
            "apk_file_name": r["apk_file_name"],
            "apk_file_path": r["apk_file_path"],
            "discovered_at": r["discovered_at"],
        }
        for r in cursor.fetchall()
    ]