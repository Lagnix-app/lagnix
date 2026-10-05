# Changelog / Журнал змін

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
