# StartmenuOpen

A tiny Windows 11 tray tool that opens the **Start menu on the monitor where the mouse is** when you press
the Windows key – instead of always on the primary monitor.

Windows 11 shows the taskbar on every monitor, and clicking a Start button opens Start on that monitor.
The Windows key, however, always opens it on the primary monitor, and there is no setting to change
that. StartmenuOpen fixes exactly this and leaves everything else alone.

## Features

- **Windows key** with the mouse on a secondary monitor → Start opens there
- Mouse on the primary monitor, or `Win + key` shortcuts (`Win + E`, `Win + R`, …) → unchanged
- **One click** on the tray icon switches the feature on/off (faded icon = off)
- **Command line**: `StartmenuOpen.exe --open` opens Start once on the monitor under the cursor and
  exits – handy for a mouse button or a shortcut
- **Start with Windows** (optional)
- **Multilingual**: English and German included, follows the Windows display language by default,
  can be changed in the tray menu; more languages can be added with a simple JSON file
- Tray icon adapts to light/dark taskbar
- Single small `.exe`, no installation, no admin rights, no background service
- Pure Python + Win32 API via `ctypes` – no third-party runtime dependencies

## Download

Get `StartmenuOpen.exe` from the [Releases](../../releases) page and run it. The icon appears in the
notification area (you may have to drag it out of the overflow area `^` to keep it visible).

> **Note:** The exe is not code-signed. Windows SmartScreen may show *"Windows protected your PC"* on
> first launch – click *More info → Run anyway*. Some antivirus programs flag PyInstaller-built
> executables – especially ones with a keyboard hook – as false positives; if in doubt,
> [build it yourself](#building-from-source).

## Usage

| Action | Result |
|---|---|
| Windows key, mouse on a secondary monitor | Start opens on that monitor |
| Windows key, mouse on the primary monitor | Start opens on the primary monitor (Windows default) |
| `Win + key` | Unchanged |
| Left-click tray icon | Feature on/off |
| Right-click tray icon | Menu: on/off, start with Windows, language, exit |

### Command line

```
StartmenuOpen.exe --open
```

Opens Start once on the monitor under the mouse cursor (primary monitor included) and exits.
Works independently of a running tray instance.

## Languages

The UI language follows the Windows display language. If no translation exists for it, English is used.
You can override it via right-click → **Language**.

Translations are JSON files in the [`lang`](lang) folder (`en.json`, `de.json`).

### Adding a translation

1. Copy [`lang/en.json`](lang/en.json) to `lang/<code>.json`, where `<code>` is a language code such as
   `fr` or `pt-br` (matched against the Windows display language, e.g. `fr-FR` → `fr`).
2. Translate the values (not the keys). `language_name` is the name shown in the menu, written in that
   language. Keep placeholders like `{language}` unchanged.
3. Either rebuild the exe, or put the file into a `lang` folder **next to** `StartmenuOpen.exe` – it is
   picked up on the next start without rebuilding.

Missing keys fall back to English. Pull requests with new translations are welcome.

## Settings

Settings are stored per user in the registry under `HKEY_CURRENT_USER\Software\StartmenuOpen`
(on/off, language). "Start with Windows" creates the value `StartmenuOpen` under
`HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run`.

### Uninstall

1. Right-click the tray icon, uncheck **Start with Windows**, then **Exit**.
2. Delete `StartmenuOpen.exe`.
3. Optional: delete the registry key `HKEY_CURRENT_USER\Software\StartmenuOpen`.

## How it works

Windows offers no API to open Start on a particular monitor, but clicking the Start button of a
secondary taskbar does exactly that. So the app

1. watches the Windows key with a low-level keyboard hook (`WH_KEYBOARD_LL`),
2. on its release – if no other key was pressed meanwhile, Start is not already open and the cursor
   is on a secondary monitor with a visible taskbar – swallows the release and injects an unassigned
   key (`vkE8`) plus the release instead, so Windows sees a `Win + key` combination and does not open
   Start on the primary monitor,
3. clicks the Start button of the taskbar on that monitor (its position comes from the hidden `Start`
   child window of `Shell_SecondaryTrayWnd`) and moves the cursor straight back.

Everything else passes through untouched.

## Building from source

Requirements: Windows 10/11 and [uv](https://docs.astral.sh/uv/).

```powershell
git clone https://github.com/Dreamfire78/StartmenuOpen.git
cd StartmenuOpen
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

The result is `dist\StartmenuOpen.exe`. The script

1. renders the icons from [`startmenu_icon.svg`](startmenu_icon.svg) (`make_icon.py`, uses PyMuPDF and Pillow),
2. builds a single-file exe with PyInstaller.

Build dependencies are pinned in [`requirements-build.txt`](requirements-build.txt); the Python version
in [`.python-version`](.python-version).

To run directly from source without building (Python 3.10+ on Windows, no packages needed; the icons
must have been generated once with `make_icon.py`, otherwise a default icon is used):

```powershell
pythonw startmenu_open.py
```

### Project layout

| Path | Purpose |
|---|---|
| `startmenu_open.py` | The application |
| `lang/*.json` | Translations |
| `startmenu_icon.svg` | Icon source ([Boxicons](https://github.com/box-icons/boxicons-core) `grid`, MIT) |
| `make_icon.py` | Generates `startmenu_open.ico` (program) and the tray icons (light/dark, on/off) |
| `build.ps1` | Build script |
| `.github/workflows/build.yml` | CI build; attaches the exe to releases for `v*` tags |

## Known limitations

- While a window of a program running **as administrator** has the focus, Windows does not pass keys
  to the hook of a normal program – the Windows key then behaves as usual (Start on the primary
  monitor). Run StartmenuOpen as administrator too if that matters to you.
- With an **auto-hidden** taskbar on the secondary monitor the Start button is off-screen; Start then
  opens on the primary monitor as usual.
- The mouse cursor jumps to the Start button for a split second.
- It relies on the hidden `Start` window that Explorer keeps on each taskbar. Tested with Windows 11
  (build 26300, vertical taskbar); a future Windows update may change this.

## Credits

Icon: [`grid`](https://icon-sets.iconify.design/boxicons/grid/) from
[Boxicons](https://github.com/box-icons/boxicons-core) (MIT), found via [Iconify](https://iconify.design/).
See [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) for all third-party licenses.

## License

[MIT](LICENSE)
