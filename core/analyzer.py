# -------------------------------------------------------------------------------
# -- Импорты
# -------------------------------------------------------------------------------

import hashlib
import os
import zipfile
from datetime import datetime
from pathlib import Path

from androguard.core.apk import APK
from loguru import logger

from core import settings
from core.models import ApkInfo
from core.prints import print_android
from utils.keytool import get_apk_cert_sha256
from utils.solar2d import (
    run_extract_car,
    run_unpack_car,
    run_decompile_lu,
    run_clear_decompiler,
)
from games.day_r import get_edition, get_version


# -------------------------------------------------------------------------------
# -- Функции
# -------------------------------------------------------------------------------

def get_apk_files_sha256(apk_path: str | Path) -> str:
    """
    Превращает содержимое APK в хеш и возвращает как строку.
    """
    hasher = hashlib.sha256()
    with open(apk_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def get_apk_build_date(apk_path: str | Path) -> str:
    """
    Определяет дату сборки APK. Возвращает строку с датой.
    """
    valid_dates = []
    with zipfile.ZipFile(apk_path, "r") as zip_file:
        for file_info in zip_file.infolist():
            # Игнорируем зафиксированные даты Android SDK (2008 и ранее)
            if file_info.date_time[0] <= 2008:
                continue
            try:
                valid_dates.append(datetime(*file_info.date_time))
            except (ValueError, TypeError):
                logger.warning(
                    f"Битая дата в {apk_path}: {file_info.filename} "
                    f"-> {file_info.date_time}"
                )

    if valid_dates:
        return max(valid_dates).strftime("%Y-%m-%d %H:%M:%S")
    return "Дата сброшена"


def get_apk_architectures(apk_path: str | Path) -> list[str]:
    """
    Определяет процессорные архитектуры, которые поддерживает APK.
    Возвращает список строк.
    """
    arches = set()
    with zipfile.ZipFile(apk_path, "r") as zip_file:
        for file_info in zip_file.infolist():
            name = file_info.filename.replace("\\", "/")
            if not (name.startswith("lib/") and name.endswith(".so")):
                continue
            parts = name.split("/")
            if len(parts) > 2:
                arches.add(parts[1])

    return list(arches) if arches else ["Универсальный"]


def _extract_day_r_metadata(apk_path: str | Path, apk_cert_hash: str):
    """
    Извлекает метаданные (версия, издание, оригинальность сертификата).
    При ошибке — (None, None, None).
    """
    resource_car = None
    try:
        resource_car = run_extract_car(apk_path)
        if resource_car is None:
            logger.warning(f"run_extract_car вернул None для {apk_path}")
            return None, None, None

        run_unpack_car(resource_car, settings.DECOMPILE_LUA_DIR)
        run_decompile_lu()

        target_version = get_version()
        target_edition, is_orig_cert = get_edition(apk_cert_hash)
        return target_version, target_edition, is_orig_cert
    except Exception:
        logger.exception(f"Ошибка извлечения Day R-метаданных для {apk_path}")
        return None, None, None
    finally:
        if resource_car and os.path.exists(resource_car):
            try:
                os.remove(resource_car)
            except OSError:
                logger.warning(f"Не удалось удалить {resource_car}")

        try:
            run_clear_decompiler("input")
            run_clear_decompiler("output")
        except Exception:
            logger.exception("Ошибка очистки декомпилятора")


def get_apk_info(apk_path: str | Path, apk_sha256: str) -> ApkInfo:
    """
    Определяет всю метаинформацию об APK-файле.
    """
    path = Path(apk_path)
    if not path.exists():
        raise FileNotFoundError(f"APK не найден: {path}")

    try:
        apk_obj = APK(path)
    except Exception:
        logger.exception(f"Не удалось распарсить APK: {path}")
        raise

    #  Свойства файла (APK/диска)
    apk_file_size = path.stat().st_size
    apk_build_time = get_apk_build_date(path)
    apk_architectures = get_apk_architectures(path)
    apk_cert_hash = get_apk_cert_sha256(path)

    #  Свойства приложения (AndroidManifest.xml)
    app_name = apk_obj.get_app_name()
    app_package = apk_obj.get_package()
    app_version = apk_obj.get_androidversion_name()
    app_min_sdk = print_android(apk_obj.get_min_sdk_version())
    app_target_sdk = print_android(apk_obj.get_target_sdk_version())
    app_max_sdk = print_android(apk_obj.get_max_sdk_version())

    #  Свойства текущего приложения
    target_version, target_edition, is_orig_cert = _extract_day_r_metadata(
        path, apk_cert_hash
    )

    # 4. Сборка модели
    return ApkInfo(
        apk_files_sha256=apk_sha256,
        apk_cert_sha256=apk_cert_hash,
        apk_time=apk_build_time,
        apk_size=apk_file_size,
        apk_arches=apk_architectures,
        apk_file_names=[],  # заполняется через add_db
        app_name=app_name,
        app_package=app_package,
        app_version=app_version,
        app_min_sdk=app_min_sdk,
        app_target_sdk=app_target_sdk,
        app_max_sdk=app_max_sdk,
        target_version=target_version,
        target_edition=target_edition,
        is_orig_cert=is_orig_cert,
    )