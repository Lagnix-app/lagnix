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
Код виходу 1 — є порушення.
"""

import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PROCESS_CONTROL = os.path.join("core", "process_control.py")
# функції, що видаляють лише власні тимчасові файли, створені тут же (не дані користувача)
OWN_TEMP_FILE_FUNCS = {"set_enabled", "download_and_verify"}
KILL_RE = re.compile(r"\.terminate\(|\.kill\(|TerminateProcess|taskkill|os\.kill\(|Stop-Process|"
                     r"WM_CLOSE|PostMessage|SendMessage|EndTask")
DELETE_CALLS = {"remove", "unlink", "rmdir", "rmtree", "removedirs", "SHEmptyRecycleBinW"}

# модуль -> (захищені функції, що мусять викликати require; помічники, які видаляють)
DELETE_ALLOWED = {
    os.path.join("core", "cleanup.py"): ({"clean_target", "clean_many"}, {"_clean_dir_contents", "_empty_recycle_bin"}),
    os.path.join("core", "app_cache.py"): ({"clean_group", "close_group"}, {"_remove_empty_dirs"}),
    # тимчасовий XML власного завдання планувальника, створений у цій же функції
    os.path.join("core", "launch_on_windows.py"): (set(), {"set_enabled"}),
    # власний завантажений інсталятор PawnIO, якщо його підпис недійсний
    os.path.join("core", "pawnio.py"): (set(), {"download_and_verify"}),
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

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node)
            if name == "UserAction" and rel != PROCESS_CONTROL:
                problems.append(f"{rel}:{node.lineno}: UserAction is created outside process_control")
            if name == "ask_user_action" and not rel.startswith("ui" + os.sep):
                problems.append(f"{rel}:{node.lineno}: ask_user_action outside the UI (not from a button press)")
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

    if problems:
        print("SAFETY RULE VIOLATION:")
        for problem in problems:
            print("  " + problem)
        return 1
    print("OK: processes are terminated only in core/process_control.py, deletion — only after confirmation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
