"""Сканування та очищення кешів і тимчасових файлів для вкладки «Очищення».

Видалення завжди відбувається за ключем цілі з get_targets() — довільний шлях
ззовні не приймається. Кожна ціль описує, ЩО саме буде видалено (конкретні
папки кешу чи файли), і ніколи не зачіпає cookies, паролі, історію чи
закладки браузерів — ці дані зберігаються поза шляхами, які тут скануються.

Сканування (scan_target/scan_many) — лише читання розмірів. Видалення
(clean_target/clean_many) вимагає process_control.UserAction — користувач
натиснув кнопку й підтвердив — і кожна тека пишеться в журнал аудиту logs.txt.
"""

import ctypes
import glob
import os
import tempfile

from core import process_control
from core.admin import is_admin
from core.logging_setup import get_audit_logger, get_logger
from core.game_mode import get_running_process_name_set
from core.i18n import t

WINDOWS_TEMP_PATH = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Temp")

CAT_TEMP = "temp"
CAT_BROWSERS = "browsers"
CAT_APPS = "apps"
CAT_RECYCLE = "recycle"
CAT_SHADERS = "shaders"
CAT_WINDOWS = "windows"
CAT_THUMBNAILS = "thumbnails"

SHADER_NOTE = "cleanup.shader_note"  # ключ перекладу


def category_label(category: str) -> str:
    """Назва категорії (CAT_*) поточною мовою."""
    return t(f"cleanup.cat.{category}")

_SHERB_NOCONFIRMATION = 0x00000001
_SHERB_NOPROGRESSUI = 0x00000002
_SHERB_NOSOUND = 0x00000004


class _SHQUERYRBINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint32),
        ("i64Size", ctypes.c_int64),
        ("i64NumItems", ctypes.c_int64),
    ]


# ------------------------------------------------------------------ targets

def _folder_target(key, category, label, patterns, process_names=None,
                    requires_admin=False, note=None, exclude_names=None):
    return {
        "key": key,
        "category": category,
        "label": label,
        "kind": "folder",
        "patterns": [p for p in patterns if p],
        "process_names": process_names or [],
        "requires_admin": requires_admin,
        "note": note,
        "exclude_names": exclude_names or set(),
    }


def get_targets() -> list[dict]:
    """Список усіх цілей очищення, згрупованих за категорією.

    Шляхи, яких немає на цьому ПК (браузер не встановлено, Steam не знайдено
    тощо), просто не додаються до списку — scan_target() для них не викликається.
    """
    targets = [_folder_target(
        key="user_temp", category=CAT_TEMP,
        label=t("cleanup.target.user_temp"),
        patterns=[os.path.abspath(tempfile.gettempdir())],
    )]

    if os.path.isdir(WINDOWS_TEMP_PATH):
        targets.append(_folder_target(
            key="windows_temp", category=CAT_TEMP,
            label=t("cleanup.target.windows_temp", path=WINDOWS_TEMP_PATH),
            patterns=[os.path.abspath(WINDOWS_TEMP_PATH)],
        ))

    local = os.environ.get("LOCALAPPDATA", "")
    roaming = os.environ.get("APPDATA", "")

    if local:
        targets.append(_folder_target(
            key="chrome_cache", category=CAT_BROWSERS, label=t("cleanup.target.chrome"),
            patterns=[
                os.path.join(local, "Google", "Chrome", "User Data", "*", "Cache"),
                os.path.join(local, "Google", "Chrome", "User Data", "*", "Code Cache"),
                os.path.join(local, "Google", "Chrome", "User Data", "*", "GPUCache"),
            ],
            process_names=["chrome.exe"],
        ))
        targets.append(_folder_target(
            key="edge_cache", category=CAT_BROWSERS, label=t("cleanup.target.edge"),
            patterns=[
                os.path.join(local, "Microsoft", "Edge", "User Data", "*", "Cache"),
                os.path.join(local, "Microsoft", "Edge", "User Data", "*", "Code Cache"),
                os.path.join(local, "Microsoft", "Edge", "User Data", "*", "GPUCache"),
            ],
            process_names=["msedge.exe"],
        ))
        targets.append(_folder_target(
            key="firefox_cache", category=CAT_BROWSERS, label=t("cleanup.target.firefox"),
            patterns=[os.path.join(local, "Mozilla", "Firefox", "Profiles", "*", "cache2")],
            process_names=["firefox.exe"],
        ))
        targets.append(_folder_target(
            key="dx_shader_cache", category=CAT_SHADERS, label=t("cleanup.target.dx_shaders"),
            patterns=[os.path.join(local, "D3DSCache")],
            note=SHADER_NOTE,
        ))
        targets.append(_folder_target(
            key="nvidia_shader_cache", category=CAT_SHADERS, label=t("cleanup.target.nvidia_shaders"),
            patterns=[
                os.path.join(local, "NVIDIA", "DXCache"),
                os.path.join(local, "NVIDIA", "GLCache"),
                os.path.join(local, "NVIDIA Corporation", "NV_Cache"),
            ],
            note=SHADER_NOTE,
        ))

    if roaming:
        targets.append(_folder_target(
            key="opera_cache", category=CAT_BROWSERS, label=t("cleanup.target.opera"),
            patterns=[
                os.path.join(roaming, "Opera Software", "Opera Stable", "Cache"),
                os.path.join(roaming, "Opera Software", "Opera Stable", "Code Cache"),
                os.path.join(roaming, "Opera Software", "Opera Stable", "GPUCache"),
            ],
            process_names=["opera.exe"],
        ))

    # Кеш програм (CAT_APPS) шукається автоматично — див. core/app_cache.py.

    targets.append({
        "key": "recycle_bin", "category": CAT_RECYCLE, "label": t("cleanup.target.recycle"),
        "kind": "recycle_bin", "requires_admin": False, "process_names": [], "note": None,
    })

    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    targets.append(_folder_target(
        key="windows_update_cache", category=CAT_WINDOWS,
        label=t("cleanup.target.windows_update"),
        patterns=[os.path.join(system_root, "SoftwareDistribution", "Download")],
        requires_admin=True,
    ))

    program_data = os.environ.get("ProgramData", r"C:\ProgramData")
    targets.append(_folder_target(
        key="crash_dumps", category=CAT_WINDOWS, label=t("cleanup.target.crash_dumps"),
        patterns=[
            os.path.join(local, "CrashDumps") if local else "",
            os.path.join(program_data, "Microsoft", "Windows", "WER", "ReportQueue"),
            os.path.join(program_data, "Microsoft", "Windows", "WER", "ReportArchive"),
        ],
        requires_admin=True,
    ))

    if local:
        explorer_dir = os.path.join(local, "Microsoft", "Windows", "Explorer")
        targets.append({
            "key": "thumbnail_cache", "category": CAT_THUMBNAILS, "label": t("cleanup.target.thumbnails"),
            "kind": "files_glob",
            "patterns": [
                os.path.join(explorer_dir, "thumbcache_*.db"),
                os.path.join(explorer_dir, "iconcache_*.db"),
            ],
            "base_dir": explorer_dir,
            "requires_admin": False, "process_names": [], "note": None,
        })

    return targets


def _find_target(key: str) -> dict | None:
    for target in get_targets():
        if target["key"] == key:
            return target
    return None


def _query_recycle_bin() -> dict | None:
    try:
        info = _SHQUERYRBINFO()
        info.cbSize = ctypes.sizeof(_SHQUERYRBINFO)
        result = ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info))
    except (AttributeError, OSError):
        return None
    if result != 0:
        return None
    return {"size_bytes": max(info.i64Size, 0), "item_count": max(info.i64NumItems, 0)}


def _empty_recycle_bin() -> bool:
    try:
        flags = _SHERB_NOCONFIRMATION | _SHERB_NOPROGRESSUI | _SHERB_NOSOUND
        result = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, flags)
    except (AttributeError, OSError):
        return False
    return result == 0


def _resolve_folder_patterns(target: dict) -> list[str]:
    exclude = target.get("exclude_names", set())
    resolved = []
    for pattern in target["patterns"]:
        for path in glob.glob(pattern):
            if not os.path.isdir(path):
                continue
            if exclude and os.path.basename(os.path.dirname(path)).lower() in exclude:
                continue
            resolved.append(os.path.abspath(path))

    seen = set()
    unique = []
    for path in resolved:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def _scan_folder_dirs(dirs: list[str]) -> tuple[int, int, bool]:
    total_size = 0
    file_count = 0
    denied_dirs = 0
    any_top_denied = False

    def _on_error(_exc):
        nonlocal denied_dirs
        denied_dirs += 1

    for path in dirs:
        try:
            os.listdir(path)
        except OSError:
            any_top_denied = True
            continue

        for root, _dirs, files in os.walk(path, onerror=_on_error):
            for name in files:
                try:
                    total_size += os.path.getsize(os.path.join(root, name))
                    file_count += 1
                except OSError:
                    continue

    access_denied = file_count == 0 and (any_top_denied or denied_dirs > 0)
    return total_size, file_count, access_denied


def _clean_dir_contents(path: str) -> tuple[int, int, int]:
    """Видаляє файли й порожні підпапки всередині path; сам path не чіпає."""
    freed_bytes = 0
    deleted_count = 0
    skipped_count = 0

    for root, _dirs, files in os.walk(path, topdown=False, onerror=lambda e: None):
        for name in files:
            file_path = os.path.join(root, name)
            try:
                size = os.path.getsize(file_path)
                os.remove(file_path)
                freed_bytes += size
                deleted_count += 1
            except OSError:
                skipped_count += 1

        if root != path:
            try:
                os.rmdir(root)
            except OSError:
                pass

    return freed_bytes, deleted_count, skipped_count


# --------------------------------------------------------------------- scan

def scan_target(key: str, running_processes: set[str] | None = None) -> dict:
    target = _find_target(key)
    result = {
        "key": key,
        "label": target["label"] if target else key,
        "category": target.get("category") if target else "",
        "exists": False,
        "access_denied": False,
        "size_bytes": 0,
        "file_count": 0,
        "requires_admin": bool(target.get("requires_admin")) if target else False,
        "admin_blocked": False,
        "process_running": False,
        "note": target.get("note") if target else None,
    }
    if target is None:
        return result

    if target.get("requires_admin") and not is_admin():
        result["admin_blocked"] = True
        get_logger("core.cleanup").error("No administrator rights for %s", key)

    process_names = target.get("process_names") or []
    if process_names:
        if running_processes is None:
            running_processes = get_running_process_name_set()
        if any(name.lower() in running_processes for name in process_names):
            result["process_running"] = True

    kind = target.get("kind", "folder")

    if kind == "recycle_bin":
        info = _query_recycle_bin()
        if info is not None:
            result["exists"] = True
            result["size_bytes"] = info["size_bytes"]
            result["file_count"] = info["item_count"]
        return result

    if kind == "files_glob":
        base_dir = target.get("base_dir")
        result["exists"] = bool(base_dir and os.path.isdir(base_dir))
        size = 0
        count = 0
        for pattern in target["patterns"]:
            for file_path in glob.glob(pattern):
                try:
                    size += os.path.getsize(file_path)
                    count += 1
                except OSError:
                    continue
        result["size_bytes"] = size
        result["file_count"] = count
        return result

    dirs = _resolve_folder_patterns(target)
    result["exists"] = len(dirs) > 0
    if not dirs:
        return result

    size, count, access_denied = _scan_folder_dirs(dirs)
    result["size_bytes"] = size
    result["file_count"] = count
    result["access_denied"] = access_denied
    return result


def scan_many(keys: list[str], progress_cb=None) -> dict:
    running = get_running_process_name_set()
    results = {}
    for key in keys:
        result = scan_target(key, running_processes=running)
        results[key] = result
        if progress_cb:
            progress_cb(key, result)
    return results


# -------------------------------------------------------------------- clean

def clean_target(key: str, action, running_processes: set[str] | None = None) -> dict:
    """Видаляє файли цілі (лише з UserAction). Зайняті файли й запущені програми пропускаються без помилок."""
    process_control.require(action, f"cleaning {key}")
    audit = get_audit_logger()
    target = _find_target(key)
    result = {"key": key, "freed_bytes": 0, "deleted_count": 0, "skipped_count": 0, "skipped_reason": None}
    if target is None:
        return result

    if target.get("requires_admin") and not is_admin():
        result["skipped_reason"] = t("cleanup.skipped")
        get_logger("core.cleanup").error("No administrator rights to clean %s", key)
        return result

    process_names = target.get("process_names") or []
    if process_names:
        if running_processes is None:
            running_processes = get_running_process_name_set()
        if any(name.lower() in running_processes for name in process_names):
            result["skipped_reason"] = t("app_cache.err.running")
            return result

    kind = target.get("kind", "folder")

    if kind == "recycle_bin":
        if not _empty_recycle_bin():
            result["skipped_reason"] = t("cleanup.err.recycle")
        else:
            audit.info("Recycle Bin emptied — reason: %s", action.reason)
        return result

    if kind == "files_glob":
        freed = deleted = skipped = 0
        for pattern in target["patterns"]:
            for file_path in glob.glob(pattern):
                try:
                    size = os.path.getsize(file_path)
                    os.remove(file_path)
                    freed += size
                    deleted += 1
                except OSError:
                    skipped += 1
        audit.info("Deleted %d files (%d bytes, skipped %d): %s — reason: %s",
                   deleted, freed, skipped, target["label"], action.reason)
        result.update(freed_bytes=freed, deleted_count=deleted, skipped_count=skipped)
        return result

    freed = deleted = skipped = 0
    for path in _resolve_folder_patterns(target):
        f, d, s = _clean_dir_contents(path)
        if d or s:
            audit.info("Deleted %d files (%d bytes, skipped %d) in %s — reason: %s",
                       d, f, s, path, action.reason)
        freed += f
        deleted += d
        skipped += s
    result.update(freed_bytes=freed, deleted_count=deleted, skipped_count=skipped)
    return result


def clean_many(keys: list[str], action, progress_cb=None, start_cb=None) -> dict:
    process_control.require(action, f"cleaning {keys}")
    running = get_running_process_name_set()
    total_freed = total_deleted = total_skipped = 0
    results = {}

    for key in keys:
        if start_cb:
            start_cb(key)
        result = clean_target(key, action, running_processes=running)
        results[key] = result
        total_freed += result["freed_bytes"]
        total_deleted += result["deleted_count"]
        total_skipped += result["skipped_count"]
        if result.get("skipped_reason"):
            total_skipped += 1
        if progress_cb:
            progress_cb(key, result)

    return {
        "freed_bytes": total_freed,
        "deleted_count": total_deleted,
        "skipped_count": total_skipped,
        "results": results,
    }


def format_size(size_bytes: int) -> str:
    """Форматує розмір у байтах у зручний вигляд (Б/КБ/МБ/ГБ/ТБ)."""
    size = float(size_bytes)
    for unit in ("units.b", "units.kb", "units.mb", "units.gb"):
        if size < 1024:
            return f"{size:.0f} {t(unit)}" if unit == "units.b" else f"{size:.1f} {t(unit)}"
        size /= 1024
    return t("units.tb_value", size=size)
