# Builds dist\StartmenuOpen.exe (requires uv: https://docs.astral.sh/uv/)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# Generate the icons from startmenu_icon.svg
uv run --no-project --with-requirements requirements-build.txt python make_icon.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

uv run --no-project --with-requirements requirements-build.txt pyinstaller --noconfirm --clean --onefile --windowed `
    --name StartmenuOpen --icon startmenu_open.ico `
    --add-data "startmenu_open.ico;." `
    --add-data "startmenu_open_light.ico;." --add-data "startmenu_open_dark.ico;." `
    --add-data "startmenu_open_light_paused.ico;." --add-data "startmenu_open_dark_paused.ico;." `
    --add-data "lang;lang" `
    startmenu_open.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Done: $PSScriptRoot\dist\StartmenuOpen.exe"
