# Changelog / Журнал змін

## 0.9.4 (beta) — 2026-10-06

- Fix: disabling a startup item no longer moves its shortcut into `Startup\Lagnix_Disabled` (Windows opened that folder in Explorer at every sign-in). Lagnix now uses the standard Windows mechanism (StartupApproved, same as Task Manager): shortcuts and Run entries stay in place, only the enabled/disabled flag changes. The state matches Task Manager → Startup apps.
- Automatic migration on first launch: shortcuts from `Lagnix_Disabled` are returned and marked disabled, the empty folder is removed (an existing shortcut with the same name is never overwritten); Run entries disabled by older versions are restored and marked disabled. The uninstaller also returns leftover shortcuts.
- README: the code-signing note now says the installer is not signed yet.

## 0.9.3 (beta) — 2026-10-05

- Anti-cheat safety: processes of anti-cheats (Vanguard, Easy Anti-Cheat, BattlEye, FACEIT, …) can never be closed by Lagnix; `tools/check_safety.py` now also forbids injection, foreign memory access and priority/affinity changes.
- Overlay: detects exclusive fullscreen and tells you once to switch the game to Windowed Fullscreen (with "Don't show again"); same hint in Settings.
- Verified the network-latency tweak (Nagle) writes and rolls back exactly, with correct backups.

## 0.9.2 (beta) — 2026-10-05

- Fix: the "widgets and Copilot" tweak failed with "Could not change the value AllowNewsAndInterests". Windows 11 blocks writing this value even for an administrator. It is now two tweaks: "widgets" (shown as unavailable with an explanation on builds where Windows protects it, and left out of presets) and "Copilot" (works).
- Tweaks are all-or-nothing: if any value of a tweak fails to write, the values already written are rolled back.
- Tweaks whose values Windows refuses to change are detected up front: greyed out instead of an error.

## 0.9.1 (beta) — 2026-10-05

- Settings → About: "GitHub" and "Report a bug" buttons (shown only when the link is set).
- Uninstaller: removed the emoji from the title, in all 12 languages.
- Version bump for the code-signing application.

## 0.9.0 (beta) — 2026-10-05

First public beta release / Перший публічний бета-реліз.

- Live monitor: CPU / GPU / RAM / disk / network, temperatures, processes grouped by app.
- Game Mode: closes background apps, switches power plan, per-game profiles, auto-enable with notification.
- Network: ping, jitter and packet loss tests with history.
- Cleanup: temp files, caches, large files finder.
- Programs, Autostart manager, registry tweaks with backups and restore points.
- System info, in-game overlay, global hotkeys, tray icon, Windows notifications.
- 12 interface languages, auto-detected from Windows.
- "Support" button and window (Ko-fi / itch.io); nothing pops up by itself.
- Personal data (`settings.json`, `data.json`, logs, backups) is no longer tracked in git; defaults are created on first run.
- `LAGNIX_DEBUG=1` enables debug mode (the old `PULSEFPS_DEBUG` still works).
