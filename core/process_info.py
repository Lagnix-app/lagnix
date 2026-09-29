"""Пояснення до процесів для «Монітора» та «Ігрового режиму».

KNOWN_PROCESSES: назва exe (у нижньому регістрі) -> пояснення простими
словами, порада й вид процесу:
  * "system"    — частина Windows, закривати не можна / не варто;
  * "anticheat" — античит гри: не закривати перед грою й під час неї;
  * "safe"      — звичайна програма, закрити безпечно (звільнить ресурси);
  * None        — нейтральний (закривати можна, але є нюанси — див. пораду).

Для невідомих процесів підказка показує шлях до exe й видавця з
метаданих файлу (VERSIONINFO), тож користувач може сам зрозуміти, що це.

Системи перекладів у проєкті поки немає — тексти українською тут, в одному
місці, щоб їх легко було винести в переклади, коли вона з'явиться.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

import psutil

from core.system_processes import is_protected

SYSTEM, ANTICHEAT, SAFE = "system", "anticheat", "safe"

BADGES = {
    SYSTEM: "системний",
    ANTICHEAT: "античит",
    SAFE: "можна закрити",
}

ANTICHEAT_WARNING = (
    "Це античит. Не закривай його перед грою чи під час гри — інакше гра "
    "не запуститься або тебе викине з матчу."
)


def _p(kind, desc: str, tip: str) -> dict:
    return {"kind": kind, "desc": desc, "tip": tip}


KNOWN_PROCESSES: dict[str, dict] = {
    # ------------------------------------------------------------ Windows
    "memory compression": _p(
        SYSTEM,
        "Стиснута пам'ять Windows: сюди система «упаковує» дані, які не влазять в оперативну пам'ять.",
        "Якщо тут багато (понад 1 ГБ) — бракує RAM: закрий браузер або увімкни Ігровий режим.",
    ),
    "system": _p(
        SYSTEM,
        "Ядро Windows: драйвери й робота з пристроями.",
        "Високе навантаження зазвичай означає проблемний драйвер — онови драйвери відеокарти й чипсета.",
    ),
    "registry": _p(
        SYSTEM,
        "Реєстр Windows у пам'яті — база налаштувань системи й програм.",
        "Нічого робити не треба, це нормальний процес.",
    ),
    "svchost.exe": _p(
        SYSTEM,
        "Контейнер для служб Windows (оновлення, мережа, звук тощо). Таких процесів завжди багато.",
        "Не закривай. Якщо один із них довго вантажить CPU — часто це Windows Update, зачекай.",
    ),
    "dwm.exe": _p(
        SYSTEM,
        "Диспетчер вікон: малює все, що ти бачиш на екрані, разом з анімаціями й прозорістю.",
        "Не закривай — зникне зображення. Навантаження росте з кількістю моніторів і частотою оновлення.",
    ),
    "explorer.exe": _p(
        SYSTEM,
        "Провідник Windows: панель задач, меню «Пуск», робочий стіл і вікна папок.",
        "Якщо панель задач зависла — його можна перезапустити, але не просто закривати.",
    ),
    "csrss.exe": _p(
        SYSTEM,
        "Критична служба Windows, що керує консольними вікнами й завершенням роботи.",
        "Закриття миттєво призведе до «синього екрана».",
    ),
    "lsass.exe": _p(
        SYSTEM,
        "Відповідає за вхід у систему, паролі та безпеку.",
        "Закриття перезавантажить комп'ютер.",
    ),
    "services.exe": _p(
        SYSTEM,
        "Запускає й зупиняє всі служби Windows.",
        "Не чіпай — без нього система не працюватиме.",
    ),
    "wininit.exe": _p(
        SYSTEM,
        "Запускає основні процеси Windows під час старту системи.",
        "Не чіпай — закриття призведе до збою системи.",
    ),
    "winlogon.exe": _p(
        SYSTEM,
        "Екран входу, блокування (Win+L) і вихід із системи.",
        "Не чіпай — це частина входу в Windows.",
    ),
    "smss.exe": _p(
        SYSTEM,
        "Менеджер сеансів — одним із перших стартує під час завантаження Windows.",
        "Нормальний системний процес, нічого робити не треба.",
    ),
    "msmpeng.exe": _p(
        SYSTEM,
        "Антивірус Windows Defender: перевіряє файли, які відкриваються й завантажуються.",
        "Якщо гальмує в іграх — додай папку з іграми у винятки Defender, але не вимикай захист.",
    ),
    "nissrv.exe": _p(
        SYSTEM,
        "Мережевий захист Windows Defender — перевіряє мережевий трафік на атаки.",
        "Частина антивіруса, залиш як є.",
    ),
    "securityhealthservice.exe": _p(
        SYSTEM,
        "Служба «Безпека Windows», що стежить за станом захисту.",
        "Нормальний процес, ресурсів майже не бере.",
    ),
    "audiodg.exe": _p(
        SYSTEM,
        "Обробка звуку Windows: ефекти, еквалайзер, змішування звуку програм.",
        "Якщо вантажить CPU — вимкни зайві звукові ефекти в налаштуваннях звуку.",
    ),
    "searchindexer.exe": _p(
        SYSTEM,
        "Індексація файлів для швидкого пошуку в Windows.",
        "Може навантажувати диск після встановлення програм — це мине само.",
    ),
    "searchhost.exe": _p(
        SYSTEM,
        "Вікно пошуку Windows у панелі задач.",
        "Нормальний процес, заважати не має.",
    ),
    "runtimebroker.exe": _p(
        SYSTEM,
        "Перевіряє дозволи програм із Microsoft Store (камера, мікрофон, геолокація).",
        "Кілька копій — нормально. Високе навантаження дає якась Store-програма.",
    ),
    "wmiprvse.exe": _p(
        SYSTEM,
        "Відповідає програмам на запити про систему (датчики, обладнання, стан).",
        "Навантаження значить, що якась програма часто опитує систему (монітори, RGB-софт).",
    ),
    "ctfmon.exe": _p(
        SYSTEM,
        "Мовна панель і введення тексту: розкладки клавіатури, рукописне введення.",
        "Не закривай — може перестати працювати перемикання мов.",
    ),
    "fontdrvhost.exe": _p(
        SYSTEM,
        "Відображення шрифтів у Windows.",
        "Нормальний процес, ресурсів майже не бере.",
    ),
    "sihost.exe": _p(
        SYSTEM,
        "Оболонка Windows: сповіщення, центр дій, частина робочого столу.",
        "Не закривай — зникнуть сповіщення й частина інтерфейсу.",
    ),
    "spoolsv.exe": _p(
        SYSTEM,
        "Служба друку (черга принтера).",
        "Якщо принтера немає — службу можна вимкнути в «Службах», але закривати процес не треба.",
    ),
    "taskhostw.exe": _p(
        SYSTEM,
        "Виконує фонові завдання Windows за розкладом.",
        "Нормальний процес.",
    ),
    "conhost.exe": _p(
        SYSTEM,
        "Вікно консолі для командних програм. Запускається разом із ними.",
        "Закриється само разом зі своєю програмою.",
    ),
    "dllhost.exe": _p(
        SYSTEM,
        "Допоміжний процес Windows, напр. для мініатюр фото й відео у Провіднику.",
        "Нормальний процес.",
    ),
    "startmenuexperiencehost.exe": _p(
        SYSTEM,
        "Меню «Пуск».",
        "Не закривай — меню «Пуск» перестане відкриватися до перезапуску.",
    ),
    "shellexperiencehost.exe": _p(
        SYSTEM,
        "Частини інтерфейсу Windows: годинник, календар, центр сповіщень.",
        "Нормальний процес.",
    ),
    "textinputhost.exe": _p(
        SYSTEM,
        "Сенсорна клавіатура, емодзі-панель (Win+.) і підказки введення.",
        "Нормальний процес.",
    ),
    "applicationframehost.exe": _p(
        SYSTEM,
        "Рамки вікон програм із Microsoft Store (Налаштування, Калькулятор тощо).",
        "Нормальний процес.",
    ),
    "smartscreen.exe": _p(
        SYSTEM,
        "Фільтр SmartScreen: попереджає про підозрілі файли й сайти.",
        "Частина захисту Windows, залиш як є.",
    ),
    "lsaiso.exe": _p(
        SYSTEM,
        "Захищене сховище паролів (Credential Guard).",
        "Нормальний системний процес.",
    ),
    # ---------------------------------------------------------- античити
    "vgc.exe": _p(
        ANTICHEAT,
        "Riot Vanguard — античит Valorant і League of Legends.",
        ANTICHEAT_WARNING + " Після закриття Vanguard знадобиться перезавантаження ПК.",
    ),
    "vgtray.exe": _p(
        ANTICHEAT,
        "Значок Riot Vanguard у треї.",
        ANTICHEAT_WARNING,
    ),
    "easyanticheat.exe": _p(
        ANTICHEAT,
        "Easy Anti-Cheat — античит багатьох ігор (Fortnite, Apex Legends тощо).",
        ANTICHEAT_WARNING,
    ),
    "easyanticheat_eos.exe": _p(
        ANTICHEAT,
        "Easy Anti-Cheat від Epic Online Services.",
        ANTICHEAT_WARNING,
    ),
    "beservice.exe": _p(
        ANTICHEAT,
        "BattlEye — античит (PUBG, Rainbow Six Siege, DayZ тощо).",
        ANTICHEAT_WARNING,
    ),
    "faceit.exe": _p(
        ANTICHEAT,
        "Клієнт і античит FACEIT для Counter-Strike.",
        ANTICHEAT_WARNING,
    ),
    # --------------------------------------------------------- програми
    "chrome.exe": _p(
        SAFE,
        "Google Chrome. Кожна вкладка й розширення — окремий процес, тому їх багато.",
        "Браузер часто з'їдає найбільше RAM — закрий його перед грою.",
    ),
    "msedge.exe": _p(
        SAFE,
        "Microsoft Edge. Може працювати у фоні, навіть коли вікно закрите.",
        "Закрий перед грою; фонову роботу вимкни в налаштуваннях Edge («Система»).",
    ),
    "msedgewebview2.exe": _p(
        None,
        "Вбудований Edge, через який інші програми показують свій інтерфейс (Teams, віджети тощо).",
        "Закриється разом із програмою, що його використовує.",
    ),
    "firefox.exe": _p(
        SAFE,
        "Браузер Mozilla Firefox.",
        "Закрий перед грою, щоб звільнити оперативну пам'ять.",
    ),
    "discord.exe": _p(
        SAFE,
        "Discord — голосовий чат і месенджер.",
        "Можна закрити, якщо не спілкуєшся. Вимкни апаратне прискорення в налаштуваннях, якщо гальмує гра.",
    ),
    "steam.exe": _p(
        SAFE,
        "Клієнт Steam.",
        "Не закривай, поки граєш у гру зі Steam — гра може закритися. Без гри — можна.",
    ),
    "steamwebhelper.exe": _p(
        None,
        "Вбудований браузер Steam: магазин, бібліотека, оверлей. Процесів кілька — це нормально.",
        "Закриється разом зі Steam. Менше RAM займе, якщо ввімкнути «малий режим» Steam.",
    ),
    "epicgameslauncher.exe": _p(
        SAFE,
        "Лаунчер Epic Games Store.",
        "Після запуску гри його зазвичай можна закрити.",
    ),
    "riotclientservices.exe": _p(
        SAFE,
        "Клієнт Riot Games (лаунчер Valorant і League of Legends).",
        "Можна закрити, коли не граєш. Античит Vanguard (vgc.exe) — окремо, його не чіпай.",
    ),
    "telegram.exe": _p(
        SAFE,
        "Месенджер Telegram.",
        "Закрити безпечно — повідомлення прийдуть на телефон.",
    ),
    "onedrive.exe": _p(
        SAFE,
        "OneDrive — синхронізація файлів із хмарою Microsoft.",
        "Можна закрити на час гри: синхронізація продовжиться після запуску.",
    ),
    "teams.exe": _p(
        SAFE,
        "Microsoft Teams — робочі чати й дзвінки.",
        "Закрий, якщо не на роботі — займає багато RAM.",
    ),
    "ms-teams.exe": _p(
        SAFE,
        "Microsoft Teams (нова версія) — робочі чати й дзвінки.",
        "Закрий, якщо не на роботі — займає багато RAM.",
    ),
    "spotify.exe": _p(
        SAFE,
        "Музичний сервіс Spotify.",
        "Закрити безпечно. Якщо слухаєш під час гри — ресурсів він бере небагато.",
    ),
    "nvcontainer.exe": _p(
        None,
        "Служби драйвера NVIDIA (панель керування, оновлення, GeForce Experience / NVIDIA App).",
        "Закривати не рекомендується — може зламатися оверлей і налаштування відеокарти.",
    ),
    "nvidia overlay.exe": _p(
        SAFE,
        "Оверлей NVIDIA (Alt+Z): запис відео, скріншоти, лічильник FPS.",
        "Якщо не записуєш відео — можна закрити або вимкнути оверлей у NVIDIA App.",
    ),
    "nvdisplay.container.exe": _p(
        None,
        "Служба драйвера дисплея NVIDIA.",
        "Не закривай — потрібна для роботи панелі керування NVIDIA.",
    ),
    "lghub.exe": _p(
        SAFE,
        "Logitech G HUB — налаштування мишки, клавіатури й навушників Logitech.",
        "Закрити безпечно, але перестануть працювати макроси й профілі підсвітки.",
    ),
    "lghub_agent.exe": _p(
        None,
        "Фонова служба Logitech G HUB — застосовує профілі пристроїв.",
        "Без неї профілі Logitech не працюватимуть.",
    ),
    "claude.exe": _p(
        SAFE,
        "Claude — застосунок-асистент від Anthropic.",
        "Можна закрити, якщо зараз не користуєшся.",
    ),
    "python.exe": _p(
        None,
        "Python. Серед інших — на ньому працює й сам PulseFPS (його в списку не показано).",
        "Якщо не знаєш, що це за скрипт, — подивись шлях до файлу перед закриттям.",
    ),
    "pythonw.exe": _p(
        None,
        "Python без вікна консолі — так часто запускається й PulseFPS.",
        "Якщо не знаєш, що це за скрипт, — подивись шлях до файлу перед закриттям.",
    ),
}


def info_for(name: str) -> dict | None:
    return KNOWN_PROCESSES.get((name or "").strip().lower())


def kind_for(name: str) -> str | None:
    """Вид процесу для позначки: system / anticheat / safe / None."""
    info = info_for(name)
    if info is not None and info["kind"]:
        return info["kind"]
    return SYSTEM if is_protected(name) else None


def is_anticheat(name: str) -> bool:
    return kind_for(name) == ANTICHEAT


# ------------------------------------------------ невідомі: шлях і видавець

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_details_cache: dict = {}

if sys.platform == "win32":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def exe_path(pid: int) -> str | None:
    """Повний шлях до exe процесу (права «обмеженого запиту» є майже для всіх)."""
    if sys.platform == "win32":
        handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            try:
                size = wintypes.DWORD(32768)
                buf = ctypes.create_unicode_buffer(size.value)
                if _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                    return buf.value
            finally:
                _kernel32.CloseHandle(handle)
    try:
        return psutil.Process(pid).exe() or None
    except (psutil.Error, OSError):
        return None


def _publisher(path: str) -> str | None:
    if sys.platform != "win32":
        return None
    try:
        from core.autostart import read_version_field
    except ImportError:
        return None
    return read_version_field(path, "CompanyName")


def unknown_details(pid: int | None, name: str) -> str:
    """«Шлях: …\\nВидавець: …» для процесу без пояснення (кешується за pid+назвою)."""
    key = (pid, name)
    text = _details_cache.get(key)
    if text is None:
        path = exe_path(pid) if pid else None
        publisher = _publisher(path) if path else None
        text = f"Шлях: {path or 'недоступний (немає прав)'}\nВидавець: {publisher or 'невідомий'}"
        if len(_details_cache) > 500:
            _details_cache.clear()
        _details_cache[key] = text
    return text


def tooltip_text(name: str, pid: int | None = None) -> str:
    """Текст підказки: пояснення + порада, або шлях і видавець для невідомих."""
    info = info_for(name)
    if info is not None:
        return f"{name}\n{info['desc']}\n\nПорада: {info['tip']}"
    details = unknown_details(pid, name)
    if is_protected(name):
        return f"{name}\nСистемний процес Windows.\n\n{details}"
    return f"{name}\nНевідомий процес — пояснення немає.\n\n{details}"
