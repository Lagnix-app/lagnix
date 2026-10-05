"""Правила сумісності з античитами (Vanguard, VAC, FACEIT, EasyAntiCheat, BattlEye та ін.).

Lagnix ніколи не втручається в процеси ігор: без ін'єкцій, хуків, DLL, читання/запису пам'яті,
без хендлів із правом читання/запису пам'яті, без зміни пріоритету й affinity. Це перевіряє tools/check_safety.py.

* ANTICHEAT_PROCESS_PARTS — процеси самих античитів: їх не можна завершити ніколи
  (це жорсткий захист, не залежить від списку «ніколи не закривати», який користувач редагує).
* ANTICHEAT_GAMES — ігри з античитом: для них Ігровий режим лише перемикає план живлення
  й закриває фонові програми (пріоритет і affinity не змінює взагалі, в жодної гри).
"""

# підрядки в назві exe (нижній регістр)
ANTICHEAT_PROCESS_PARTS = (
    "vanguard",                                           # Riot Vanguard (Valorant, LoL)
    "easyanticheat", "eosanticheat",                      # Epic Easy Anti-Cheat
    "beservice", "bedaisy", "battleye",                   # BattlEye
    "faceit",                                             # FACEIT
    "punkbuster", "pnkbstr", "xigncode", "gameguard", "nprotect",
    "ricochet",                                           # Call of Duty
)

# короткі назви — лише на початку імені exe (щоб "esea" не збігалось із "research.exe")
ANTICHEAT_PROCESS_PREFIXES = ("vgc", "vgtray", "esea")  # Vanguard, ESEA

# точні назви exe ігор з античитом (нижній регістр)
ANTICHEAT_GAMES = frozenset({
    "valorant.exe", "valorant-win64-shipping.exe",
    "league of legends.exe", "leagueclient.exe",
    "cs2.exe",
    "fortniteclient-win64-shipping.exe",
    "r5apex.exe", "r5apex_dx12.exe",
    "rainbowsix.exe", "rainbowsix_vulkan.exe", "rainbowsixgame.exe",
    "tslgame.exe",
})


def is_anticheat_process(name: str) -> bool:
    n = (name or "").strip().lower()
    return bool(n) and (any(part in n for part in ANTICHEAT_PROCESS_PARTS)
                        or n.startswith(ANTICHEAT_PROCESS_PREFIXES))


def is_anticheat_game(name: str) -> bool:
    return (name or "").strip().lower() in ANTICHEAT_GAMES
