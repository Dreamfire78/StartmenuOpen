# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [1.0.1] - 2026-11-13

### Fixed

- `Win + drag` with [AltSnap](https://github.com/RamonUnch/AltSnap) (Windows key as the mouse
  modifier) opened Start on secondary monitors: AltSnap swallows the drag clicks before other hooks
  see them, so the release looked like "Windows key alone". The app now also watches mouse buttons
  and waits 50 ms after the release: an injected key from another program (AltSnap's dummy key)
  cancels the Start menu.

## [1.0.0] - 2026-11-08

### Added

- Windows key opens the Start menu on the monitor with the mouse cursor (low-level keyboard hook,
  clicks the Start button of that monitor's taskbar); primary monitor and `Win + key` shortcuts unchanged.
- Tray icon: left-click switches the feature on/off.
- Command line option `--open`.
- "Start with Windows" option.
- English and German translations; follows the Windows display language, selectable in the tray menu;
  additional languages can be dropped into a `lang` folder next to the exe.
- Tray icon adapts to light/dark taskbar.
- Single-instance protection; tray icon is restored after an Explorer restart.

[1.0.1]: https://github.com/Dreamfire78/StartmenuOpen/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/Dreamfire78/StartmenuOpen/releases/tag/v1.0.0
