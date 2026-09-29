"""Групування процесів у програми — як на вкладці «Процеси» Диспетчера завдань.

Процес приєднується до батьківського (і далі вгору по дереву), якщо батько —
та сама програма:
  * та сама назва exe (msedge.exe -> msedge.exe, Discord.exe -> Discord.exe);
  * допоміжний процес із тим самим «коренем» назви, щонайменше 4 літери
    (steamwebhelper.exe -> steam.exe, EpicWebHelper -> EpicGamesLauncher);
  * conhost.exe — вікно консолі, що належить своїй програмі.
Оболонка й служби Windows (explorer, services, svchost…) ніколи не стають
«коренем» для чужих процесів — інакше все, що запустив користувач, злилося б
у групу Провідника. Гра, запущена зі Steam, лишається окремою групою: її назва
не схожа на steam.exe.

Назва групи — FileDescription з exe кореня («Microsoft Edge», «Discord»,
«Steam»), як у Диспетчері; якщо її немає — назва exe без розширення.
"""

from __future__ import annotations

import os

from core.system_processes import is_protected

_SHELL_PARENTS = {
    "explorer.exe", "services.exe", "svchost.exe", "wininit.exe", "winlogon.exe", "smss.exe",
    "csrss.exe", "userinit.exe", "sihost.exe", "system", "registry", "taskhostw.exe",
    "runtimebroker.exe", "dllhost.exe", "cmd.exe", "powershell.exe", "pwsh.exe",
    "windowsterminal.exe", "openconsole.exe",
}
_MIN_COMMON_PREFIX = 4

_title_cache: dict = {}  # (pid, create_time) -> (назва, шлях до exe)


def _stem(name: str) -> str:
    name = name.lower()
    return name[:-4] if name.endswith(".exe") else name


def _belongs_to(child: dict, parent: dict | None) -> bool:
    if parent is None:
        return False
    # PID міг звільнитися й дістатися новому процесу: справжній батько старший
    if child.get("create_time") and parent.get("create_time") and parent["create_time"] > child["create_time"]:
        return False
    parent_name, child_name = parent["name"].lower(), child["name"].lower()
    if child_name == parent_name:
        return True
    if child_name == "conhost.exe":
        return parent_name not in ("csrss.exe", "services.exe", "svchost.exe", "system")
    if parent_name in _SHELL_PARENTS:
        return False
    common = os.path.commonprefix([_stem(child_name), _stem(parent_name)])
    return len(common) >= _MIN_COMMON_PREFIX


def _title_and_path(root: dict) -> tuple[str, str | None]:
    key = (root["pid"], root.get("create_time"))
    cached = _title_cache.get(key)
    if cached is not None:
        return cached
    from core import process_info  # тут, щоб не тягнути ctypes-залежності при імпорті

    path = process_info.exe_path(root["pid"])
    title = None
    if path:
        try:
            from core.autostart import read_version_field
            title = read_version_field(path, "FileDescription")
        except ImportError:
            title = None
    if not title or len(title) > 48:
        title = _stem(root["name"]) if root["name"] != "—" else root["name"]
        title = title[:1].upper() + title[1:]
    if len(_title_cache) > 2000:
        _title_cache.clear()
    _title_cache[key] = (title, path)
    return title, path


def group_processes(procs: list[dict]) -> list[dict]:
    """Групи: key, name (exe кореня), title, exe_path, pid (кореня), members,
    cpu_percent/memory_mb (суми), protected (корінь — системний процес)."""
    by_pid = {p["pid"]: p for p in procs}
    root_of: dict[int, int] = {}

    def find_root(p: dict) -> int:
        chain = []
        cur = p
        while True:
            cached = root_of.get(cur["pid"])
            if cached is not None:
                root = cached
                break
            parent = by_pid.get(cur.get("ppid") or 0)
            if parent is None or parent is cur or len(chain) > 64 or not _belongs_to(cur, parent):
                root = cur["pid"]
                chain.append(cur["pid"])
                break
            chain.append(cur["pid"])
            cur = parent
        for pid in chain:
            root_of[pid] = root
        return root

    groups: dict[int, dict] = {}
    for p in procs:
        root_pid = find_root(p)
        group = groups.get(root_pid)
        if group is None:
            root = by_pid[root_pid]
            group = groups[root_pid] = {
                "key": (root_pid, root.get("create_time")),
                "pid": root_pid,
                "name": root["name"],
                "members": [],
                "cpu_percent": 0.0,
                "memory_mb": 0.0,
                "protected": is_protected(root["name"]),
            }
        group["members"].append(p)
        group["cpu_percent"] += p["cpu_percent"]
        group["memory_mb"] += p["memory_mb"]

    for group in groups.values():
        root = by_pid[group["pid"]]
        group["title"], group["exe_path"] = _title_and_path(root)
        group["cpu_percent"] = min(group["cpu_percent"], 100.0)
    return list(groups.values())
