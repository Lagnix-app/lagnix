"""Каталог фонових програм для «Ігрового режиму»: що пропонувати закрити на
кожному рівні, у якій групі показувати і що ніколи не закривати.

Рівні накопичуються: «Збалансований» = «М'який» + свої програми, «Максимальний» =
«Збалансований» + свої. Програма в каталозі — це exe КОРЕНЯ групи процесів
(як на «Моніторі»: Chrome з 40 процесів = одна програма).

Тексти (назви рівнів, груп, попередження) зібрано тут, в одному місці: системи
перекладів у проєкті поки немає, тож саме цей модуль і стане її основою.
"""

SOFT, BALANCED, MAX = "soft", "balanced", "max"
LEVELS = (SOFT, BALANCED, MAX)
DEFAULT_LEVEL = BALANCED
LEVEL_RANK = {SOFT: 1, BALANCED: 2, MAX: 3}
LEVEL_LABELS = {SOFT: "М'який", BALANCED: "Збалансований", MAX: "Максимальний"}
LEVEL_HINTS = {
    SOFT: "Браузери, лаунчери не поточної гри, оновлювачі.",
    BALANCED: "+ месенджери (крім Discord), хмари, музика й відео, офіс і редактори, "
              "фонові служби Adobe/Office/лаунчерів, віджети, Copilot, Phone Link.",
    MAX: "+ Discord, утиліти периферії та RGB, оверлеї (Game Bar, NVIDIA Overlay).",
}

# Групи для показу (порядок = порядок на екрані)
G_BROWSERS, G_MESSENGERS, G_LAUNCHERS, G_CLOUD, G_PERIPHERALS, G_OTHER = (
    "browsers", "messengers", "launchers", "cloud", "peripherals", "other")
GROUPS = (G_BROWSERS, G_MESSENGERS, G_LAUNCHERS, G_CLOUD, G_PERIPHERALS, G_OTHER)
GROUP_LABELS = {
    G_BROWSERS: "Браузери", G_MESSENGERS: "Месенджери", G_LAUNCHERS: "Лаунчери",
    G_CLOUD: "Хмари", G_PERIPHERALS: "Периферія", G_OTHER: "Інше",
}

# Підписи категорій (підказка на чіпі)
CATEGORY_LABELS = {
    "browser": "Браузер", "launcher": "Лаунчер", "launcher_service": "Фонова служба лаунчера",
    "updater": "Оновлювач", "messenger": "Месенджер / дзвінки", "cloud": "Хмарний клієнт",
    "media": "Музика / відео", "office": "Офіс / редактор", "helper": "Допоміжний процес",
    "windows": "Фонова програма Windows", "peripheral": "Утиліта периферії", "rgb": "Підсвітка RGB",
    "overlay": "Оверлей", "user": "Додано вами", "profile": "Позначено вручну (профіль)",
}
CATEGORY_GROUP = {
    "browser": G_BROWSERS, "launcher": G_LAUNCHERS, "launcher_service": G_LAUNCHERS, "updater": G_OTHER,
    "messenger": G_MESSENGERS, "cloud": G_CLOUD, "media": G_OTHER, "office": G_OTHER, "helper": G_OTHER,
    "windows": G_OTHER, "peripheral": G_PERIPHERALS, "rgb": G_PERIPHERALS, "overlay": G_OTHER,
    "user": G_OTHER, "profile": G_OTHER,
}

PERIPHERAL_WARNING = "Підсвітка, макроси й профілі кнопок можуть перестати працювати до повторного запуску."
OVERLAY_WARNING = "Оверлей зникне; якщо йде запис чи Instant Replay — він зупиниться."
DOCUMENT_NOTE = "Лише м'яке закриття: якщо програма спитає «Зберегти?», вона залишиться відкритою."

# exe (нижній регістр) -> (категорія, мінімальний рівень, платформа лаунчера або None)
_CATALOG: dict[str, tuple[str, str, str | None]] = {}


def _add(category: str, level: str, names, platform: str | None = None) -> None:
    for name in names:
        _CATALOG[name] = (category, level, platform)


# ---------------------------------------------------------------- М'який
_add("browser", SOFT, (
    "chrome.exe", "msedge.exe", "firefox.exe", "opera.exe", "brave.exe", "vivaldi.exe", "browser.exe",
    "waterfox.exe", "librewolf.exe", "chromium.exe", "arc.exe", "thorium.exe", "floorp.exe", "zen.exe",
))
_add("launcher", SOFT, ("steam.exe", "steamwebhelper.exe"), "Steam")
_add("launcher", SOFT, ("epicgameslauncher.exe",), "Epic")
_add("launcher", SOFT, ("battle.net.exe", "battle.net launcher.exe"), "Battle.net")
_add("launcher", SOFT, ("eadesktop.exe", "origin.exe"), "EA")
_add("launcher", SOFT, ("ubisoftconnect.exe", "upc.exe"), "Ubisoft")
_add("launcher", SOFT, ("riotclientservices.exe", "riotclientux.exe", "riot client.exe"), "Riot")
_add("launcher", SOFT, ("galaxyclient.exe",), "GOG")
_add("launcher", SOFT, ("xboxpcapp.exe",), "Xbox")
_add("launcher", SOFT, ("rockstarservice.exe", "socialclubhelper.exe"), "Rockstar")
_add("updater", SOFT, (
    "googleupdate.exe", "microsoftedgeupdate.exe", "braveupdate.exe", "opera_autoupdate.exe", "adobearm.exe",
    "jusched.exe", "ituneshelper.exe", "softwareupdate.exe", "lghub_updater.exe",
))

# --------------------------------------------------------- Збалансований
_add("messenger", BALANCED, (
    "telegram.exe", "teams.exe", "ms-teams.exe", "msteams.exe", "slack.exe", "viber.exe", "whatsapp.exe",
    "whatsapp.root.exe", "skype.exe", "skypeapp.exe", "zoom.exe", "signal.exe", "element.exe",
))
_add("cloud", BALANCED, (
    "onedrive.exe", "googledrivefs.exe", "googledrive.exe", "dropbox.exe", "icloudservices.exe",
    "icloudphotos.exe", "iclouddrive.exe", "icloudhome.exe", "megasync.exe", "yandexdisk2.exe",
    "nextcloud.exe", "pcloud.exe", "syncthing.exe",
))
_add("media", BALANCED, (
    "spotify.exe", "vlc.exe", "itunes.exe", "applemusic.exe", "aimp.exe", "foobar2000.exe", "deezer.exe",
    "tidal.exe", "wmplayer.exe", "potplayermini64.exe", "potplayermini.exe", "mpc-hc64.exe", "mpc-be64.exe",
    "music.ui.exe", "yandex music.exe", "soundcloud.exe",
))
_add("office", BALANCED, (
    "winword.exe", "excel.exe", "powerpnt.exe", "onenote.exe", "outlook.exe", "olk.exe", "msaccess.exe",
    "mspub.exe", "visio.exe", "soffice.exe", "soffice.bin", "acrobat.exe", "acrord32.exe", "foxitpdfeditor.exe",
    "photoshop.exe", "illustrator.exe", "afterfx.exe", "adobe premiere pro.exe", "indesign.exe",
    "lightroom.exe", "gimp-2.10.exe", "gimp.exe", "krita.exe", "paintdotnet.exe", "notepad++.exe",
    "sublime_text.exe", "notepad.exe", "wordpad.exe", "obsidian.exe", "notion.exe", "figma.exe",
    "capcut.exe", "davinci resolve.exe", "resolve.exe", "blender.exe",
))
_add("helper", BALANCED, (
    "creative cloud.exe", "adobe desktop service.exe", "ccxprocess.exe", "cclibrary.exe", "adobeipcbroker.exe",
    "creative cloud ui helper.exe", "adobecollabsync.exe", "adobe cef helper.exe", "coresync.exe",
    "officeclicktorun.exe", "officec2rclient.exe",
))
_add("launcher_service", BALANCED, ("eabackgroundservice.exe", "ealocalhostsvc.exe"), "EA")
_add("launcher_service", BALANCED, ("uplaywebcore.exe", "upc_service.exe"), "Ubisoft")
_add("launcher_service", BALANCED, ("epicwebhelper.exe", "epiconlineservicesuserhelper.exe"), "Epic")
_add("launcher_service", BALANCED, ("battle.net helper.exe",), "Battle.net")
_add("windows", BALANCED, (
    "widgets.exe", "widgetservice.exe", "copilot.exe", "microsoft.copilot.exe", "m365copilot.exe",
    "phoneexperiencehost.exe", "yourphone.exe", "crossdeviceresume.exe", "cortana.exe", "winstore.app.exe",
))

# ------------------------------------------------------------ Максимальний
_add("messenger", MAX, ("discord.exe", "discordptb.exe", "discordcanary.exe"))
_add("peripheral", MAX, (
    "lghub.exe", "lghub_agent.exe", "lghub_system_tray.exe", "razer synapse 3.exe", "razer synapse.exe",
    "razer synapse service.exe", "razercentralservice.exe", "razer central.exe", "icue.exe",
    "corsair.service.exe", "steelseriesgg.exe", "steelseriesengine.exe", "steelseriesprism.exe",
    "hyperx ngenuity.exe", "wootility.exe", "glorious core.exe",
))
_add("rgb", MAX, (
    "signalrgb.exe", "openrgb.exe", "lightingservice.exe", "armourycrate.exe", "armourycrate.usersessionhelper.exe",
    "nzxt cam.exe", "mysticlight.exe", "msi center.exe", "rgbfusion.exe", "polychrome rgb.exe",
))
_add("overlay", MAX, (
    "gamebar.exe", "gamebarftserver.exe", "nvidia overlay.exe", "nvidia share.exe",
))

# Зрозумілі назви там, де опис exe порожній чи технічний
NICE_TITLES = {
    "widgets.exe": "Віджети Windows", "widgetservice.exe": "Віджети Windows (служба)",
    "copilot.exe": "Copilot", "microsoft.copilot.exe": "Copilot", "m365copilot.exe": "Microsoft 365 Copilot",
    "phoneexperiencehost.exe": "Phone Link", "yourphone.exe": "Phone Link",
    "crossdeviceresume.exe": "Phone Link (продовження на ПК)", "cortana.exe": "Cortana",
    "winstore.app.exe": "Microsoft Store", "officeclicktorun.exe": "Office Click-to-Run",
    "gamebar.exe": "Xbox Game Bar", "gamebarftserver.exe": "Xbox Game Bar (служба)",
    "lghub_updater.exe": "Logitech G HUB Updater", "lghub_agent.exe": "Logitech G HUB Agent",
    "nvidia overlay.exe": "NVIDIA Overlay", "nvidia share.exe": "NVIDIA Share",
}

# Оверлеї NVIDIA: якщо вони навантажують CPU — схоже, що йде запис/Instant Replay, не чіпаємо
RECORDING_SENSITIVE = {"nvidia overlay.exe", "nvidia share.exe"}
RECORDING_CPU_THRESHOLD = 2.0

# Лише м'яке закриття — можуть бути незбережені документи
DOCUMENT_CATEGORIES = {"office"}

# «Ніколи не закривати» — типовий список (підрядки в назві exe, нижній регістр).
# Користувач може додавати й прибирати; «Відновити типові» повертає цей список.
DEFAULT_NEVER_CLOSE = (
    # античити
    "easyanticheat", "battleye", "beservice", "faceit", "vgc", "vgtray", "vanguard", "xigncode", "gameguard",
    "nprotect", "punkbuster", "pnkbstr", "eosanticheat", "ricochet", "atvi-",
    # запис і стрім
    "obs64", "obs32", "obs-", "streamlabs", "slobs", "xsplit", "medal", "outplayed", "bandicam", "fraps",
    "action_x64", "shadowplay",
    # сам PulseFPS і середовище розробки / термінали
    "pulsefps", "claude", "windowsterminal", "openconsole", "wt.exe", "cmd.exe", "powershell", "pwsh",
    "conhost", "bash.exe", "mintty", "node.exe", "python", "code.exe", "cursor.exe", "devenv",
    # драйвери й панелі відеокарт
    "nvcontainer", "nvdisplay", "nvcplui", "radeonsoftware", "amdrsserv", "amdow", "igfx", "intelgraphics",
    "rtss", "msiafterburner",
    # антивіруси
    "msmpeng", "mpdefender", "securityhealth", "avp.exe", "avastui", "avgui", "ekrn", "egui", "mbam",
    "bdagent", "norton", "mcafee", "kav", "drweb",
    # аудіо
    "voicemeeter", "nahimic", "rtkaud", "ravbg", "sonicstudio", "dolby", "equalizerapo", "eartrumpet",
)

TEXT_PLATFORM_PROTECTED = "потрібен для запущеної гри «{game}»"
TEXT_RECORDING = "схоже, йде запис чи Instant Replay"


def entry(exe_name: str) -> tuple[str, str, str | None] | None:
    return _CATALOG.get(exe_name.lower())


def all_names() -> set[str]:
    return set(_CATALOG)
