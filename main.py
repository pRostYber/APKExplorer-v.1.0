from pathlib import Path
import sqlite3 as sql

from loguru import logger

from core import settings
from core.analyzer import get_apk_files_sha256, get_apk_info
from core.prints import print_result
from core.data_base import init_db, get_apk, write_db, add_db
from games.day_r import run_sort

logger.remove()
logger.add(
    "logs/app.log",
    rotation="10 MB",
    retention="7 days",
    level="INFO",
)


def main():
    """Вход в программу. Начало процесса."""
    total = 0
    errors = 0

    # Единая сессия подключения на весь цикл обработки.
    with sql.connect(settings.DATABASE_PATH) as conn:
        conn.row_factory = sql.Row
        init_db(conn)

        for i, apk in enumerate(Path(settings.ARCHIVE_DIR).rglob("*.apk"), start=1):
            total = i
            logger.info(f"АПК №{i}: {apk}")

            try:
                sha256 = get_apk_files_sha256(apk)
                info = get_apk(sha256, conn)

                if info is not None:
                    logger.info(f"{i}. --- Старый APK ---")
                else:
                    info = get_apk_info(apk, sha256)
                    write_db(info, conn)
                    logger.info(f"{i}. --- Новый APK ---")

                file_names = add_db(sha256, conn, apk.stem, apk_file_path=str(apk))
                info.apk_file_names = file_names

                conn.commit()

                print_result(info)
                run_sort(apk, info)

            except Exception:
                errors += 1
                logger.exception(f"Ошибка при обработке {apk}")
                conn.rollback()
                continue
            print("\n \n")

    logger.info(f"Обработано APK: {total}, ошибок: {errors}")


if __name__ == '__main__':
    main()