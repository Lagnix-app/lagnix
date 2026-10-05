# Code Signing Policy

Free code signing provided by [SignPath.io](https://signpath.io), certificate by [SignPath Foundation](https://signpath.org).

This policy applies to the Windows binaries of [Lagnix](https://github.com/Lagnix-app/lagnix)
(`Lagnix.exe` and `Lagnix-Setup-<version>.exe`), built from this repository.

## Team roles

- **Committers and reviewers:** [Lagnix-app](https://github.com/Lagnix-app)
- **Approvers:** [Lagnix-app](https://github.com/Lagnix-app)

## Privacy policy

This program will not transfer any information to other networked systems unless specifically
requested by the user or the person installing or operating it.

Lagnix contains no analytics, telemetry, crash reporting, advertising or automatic update checks.
The only network activity in the program is the following, and each item starts only from an explicit
user action:

1. **Ping tests (Network tab).** When the user presses a start button, Lagnix sends ICMP echo requests
   (ping) to `8.8.8.8` (Google DNS), `1.1.1.1` (Cloudflare) and, optionally, a host the user types in.
   Only the standard ICMP packets are sent; results stay on the computer.
2. **PawnIO driver download (Settings, CPU temperature sensor).** After the user confirms a dialog,
   Lagnix downloads the official installer from
   `https://github.com/namazso/PawnIO.Setup/releases/latest/download/PawnIO_setup.exe`
   (request carries only the header `User-Agent: Lagnix`), verifies its digital signature and runs it.
   The Lagnix installer can instead bundle this file and install it only if the user ticks the option.
3. **Opening links in the default browser.** The "Support the author" links
   (`https://ko-fi.com/lagnix`, `https://lagnixapp.itch.io/lagnix`) and the GPU driver pages
   (nvidia.com, amd.com, intel.com) from the System tab hint "Fix" button open in the user's browser;
   Lagnix itself makes no request to them.

Settings, statistics, backups and logs are stored locally in `%APPDATA%\Lagnix` and are never uploaded.
