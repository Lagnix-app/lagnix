"""Перевірка правила безпеки проєкту (запуск: python tools/check_safety.py).

1. Завершувати й закривати процеси (.terminate() / .kill() / taskkill /
   TerminateProcess / os.kill / WM_CLOSE через PostMessage) можна лише в
   core/process_control.py.
2. UserAction створюється лише в core/process_control.py, а ask_user_action()
   викликається лише з UI (ui/*), тобто після натискання кнопки.
3. Видаляти файли (os.remove / os.unlink / os.rmdir / shutil.rmtree /
   SHEmptyRecycleBin) можна лише у функціях з білого списку нижче. Публічні
   функції видалення мусять викликати process_control.require(), а приватні
   помічники — викликатися тільки з таких захищених функцій.
4. Сумісність з античитами (Vanguard, VAC, FACEIT, EasyAntiCheat, BattlEye): у коді немає
   ін'єкцій, хуків, DLL у чужі процеси, читання/запису чужої пам'яті, хендлів із правами PROCESS_VM_*,
   зміни пріоритету / affinity процесів, емуляції введення; OpenProcess — лише з
   PROCESS_QUERY_LIMITED_INFORMATION (core/process_info.py); оверлей не шукає й не чіпає чужих вікон;
   процеси античитів (core/anticheat.py) завжди захищені від завершення.
Код виходу 1 — є порушення.
"""

import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PROCESS_CONTROL = os.path.join("core", "process_control.py")
# функції, що видаляють лише власні тимчасові файли, створені тут же (не дані користувача)
OWN_TEMP_FILE_FUNCS = {"_remove_legacy_dir", "set_enabled", "download_and_verify", "seed_settings", "_seed_settings"}
KILL_RE = re.compile(r"\.terminate\(|\.kill\(|TerminateProcess|taskkill|os\.kill\(|Stop-Process|"
                     r"WM_CLOSE|PostMessage|SendMessage|EndTask")
# 4. заборонені в усьому коді (поза цим файлом) виклики/константи
ANTICHEAT_RE = re.compile(
    r"WriteProcessMemory|ReadProcessMemory|VirtualAllocEx|VirtualProtectEx|CreateRemoteThread|NtCreateThreadEx|"
    r"QueueUserAPC|SetWindowsHookEx|SetWinEventHook|DebugActiveProcess|NtReadVirtualMemory|NtWriteVirtualMemory|"
    r"SetPriorityClass|SetProcessAffinityMask|SetThreadAffinityMask|SetProcessPriorityBoost|cpu_affinity|\.nice\(|ionice|"
    r"PROCESS_VM_|PROCESS_ALL_ACCESS|PROCESS_CREATE_THREAD|PROCESS_DUP_HANDLE|PROCESS_SET_INFORMATION|pymem|"
    r"SendInput|keybd_event|mouse_event|LoadLibraryExW?\(|InjectDll")
OPEN_PROCESS_FILE = os.path.join("core", "process_info.py")
OVERLAY_FILE = os.path.join("ui", "overlay.py")
# оверлей — лише власне вікно topmost: жодного пошуку/керування чужими вікнами
OVERLAY_FORBIDDEN_RE = re.compile(r"FindWindow|EnumWindows|GetForegroundWindow|SetForegroundWindow|SetParent|"
                                  r"GetWindowThreadProcessId|AttachThreadInput|GetWindowDC|BitBlt|PrintWindow")
DELETE_CALLS = {"remove", "unlink", "rmdir", "rmtree", "removedirs", "SHEmptyRecycleBinW"}

# модуль -> (захищені функції, що мусять викликати require; помічники, які видаляють)
DELETE_ALLOWED = {
    os.path.join("core", "cleanup.py"): ({"clean_target", "clean_many"}, {"_clean_dir_contents", "_empty_recycle_bin"}),
    os.path.join("core", "app_cache.py"): ({"clean_group", "close_group"}, {"_remove_empty_dirs"}),
    # тимчасовий XML власного завдання планувальника, створений у цій же функції
    os.path.join("core", "launch_on_windows.py"): (set(), {"set_enabled"}),
    # власний завантажений інсталятор PawnIO, якщо його підпис недійсний
    os.path.join("core", "pawnio.py"): (set(), {"download_and_verify"}),
    # власна порожня тека Lagnix_Disabled зі старих версій (rmdir лише порожньої)
    os.path.join("core", "autostart.py"): (set(), {"_remove_legacy_dir"}),
    # прихований режим знімків: лише власний data.json в окремій теці знімків
    os.path.join("ui", "screenshot_mode.py"): (set(), {"seed_settings"}),
    # промо-режим: лише власні кадри запису й тимчасові settings/data у теці промо
    os.path.join("ui", "promo", "engine.py"): (set(), {"run"}),
    os.path.join("ui", "promo", "__init__.py"): (set(), {"_seed_settings", "run"}),
}


def _py_files():
    for folder in ("core", "ui", "tools"):
        for dirpath, _dirs, files in os.walk(os.path.join(ROOT, folder)):
            for name in files:
                if name.endswith(".py"):
                    path = os.path.join(dirpath, name)
                    yield os.path.relpath(path, ROOT), path
    yield "main.py", os.path.join(ROOT, "main.py")


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _functions(tree):
    return [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _innermost_function(tree, node):
    best = None
    for fn in _functions(tree):
        if fn.lineno <= node.lineno <= (fn.end_lineno or fn.lineno):
            if best is None or fn.lineno >= best.lineno:
                best = fn
    return best


def main() -> int:
    problems = []
    this_file = os.path.relpath(os.path.abspath(__file__), ROOT)

    for rel, path in _py_files():
        with open(path, encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source)

        if rel not in (PROCESS_CONTROL, this_file):
            for lineno, line in enumerate(source.splitlines(), 1):
                code = line.split("#", 1)[0]
                if KILL_RE.search(code):
                    problems.append(f"{rel}:{lineno}: process termination outside process_control: {line.strip()}")

        if rel != this_file:
            for lineno, line in enumerate(source.splitlines(), 1):
                code = line.split("#", 1)[0]
                if ANTICHEAT_RE.search(code):
                    problems.append(f"{rel}:{lineno}: forbidden for anti-cheat safety (injection / foreign memory / "
                                    f"priority / affinity / input): {line.strip()}")
                if "OpenProcess" in code and not (rel == OPEN_PROCESS_FILE and "_PROCESS_QUERY_LIMITED_INFORMATION" in code
                                                  or ".argtypes" in code or ".restype" in code):
                    problems.append(f"{rel}:{lineno}: OpenProcess is allowed only with PROCESS_QUERY_LIMITED_INFORMATION "
                                    f"in {OPEN_PROCESS_FILE}: {line.strip()}")
                if rel == OVERLAY_FILE and OVERLAY_FORBIDDEN_RE.search(code):
                    problems.append(f"{rel}:{lineno}: the overlay must not touch other windows: {line.strip()}")

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node)
            if name == "UserAction" and rel != PROCESS_CONTROL:
                problems.append(f"{rel}:{node.lineno}: UserAction is created outside process_control")
            if name in ("ask_user_action", "ask_force_close") and not rel.startswith("ui" + os.sep):
                problems.append(f"{rel}:{node.lineno}: {name} outside the UI (not from a button press)")
            if name in DELETE_CALLS and not (name == "remove" and isinstance(node.func, ast.Attribute)
                                             and not (isinstance(node.func.value, ast.Name)
                                                      and node.func.value.id == "os")):
                fn = _innermost_function(tree, node)
                guarded, helpers = DELETE_ALLOWED.get(rel, (set(), set()))
                if fn is None or fn.name not in guarded | helpers:
                    where = fn.name if fn else "<module>"
                    problems.append(f"{rel}:{node.lineno}: deletion ({name}) in a disallowed function {where}")

        guarded, helpers = DELETE_ALLOWED.get(rel, (set(), set()))
        by_name = {fn.name: fn for fn in _functions(tree)}
        for fname in guarded:
            fn = by_name.get(fname)
            if fn is None:
                problems.append(f"{rel}: missing function {fname} from the protected list")
                continue
            if not any(isinstance(n, ast.Call) and _call_name(n) == "require" for n in ast.walk(fn)):
                problems.append(f"{rel}:{fn.lineno}: {fname} does not call process_control.require()")
        for helper in helpers - OWN_TEMP_FILE_FUNCS:
            for fn in _functions(tree):
                uses = any(isinstance(n, ast.Name) and n.id == helper for n in ast.walk(fn))
                if uses and fn.name != helper and fn.name not in guarded:
                    problems.append(f"{rel}:{fn.lineno}: deletion helper {helper} is called from an unprotected function {fn.name}")

    # процеси античитів завжди захищені: is_protected() їх знає, а terminate() перевіряє is_protected()
    for rel, needle in (("core/system_processes.py", "anticheat.is_anticheat_process"),
                        ("core/process_control.py", "is_protected(proc.name())")):
        with open(os.path.join(ROOT, *rel.split("/")), encoding="utf-8") as f:
            if needle not in f.read():
                problems.append(f"{rel}: missing the anti-cheat process protection ({needle})")
    from importlib import import_module
    sys.path.insert(0, ROOT)
    anticheat = import_module("core.anticheat")
    for name in ("vgc.exe", "vgtray.exe", "EasyAntiCheat.exe", "EasyAntiCheat_EOS.exe", "BEService.exe", "FACEIT.exe"):
        if not anticheat.is_anticheat_process(name):
            problems.append(f"core/anticheat.py: {name} is not recognised as an anti-cheat process")

    if problems:
        print("SAFETY RULE VIOLATION:")
        for problem in problems:
            print("  " + problem)
        return 1
    print("OK: processes are terminated only in core/process_control.py, deletion — only after confirmation; no injection / foreign memory access / priority changes (anti-cheat safe).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
