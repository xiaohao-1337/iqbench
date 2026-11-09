param([string]$Python)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if (-not $Python) { $Python = Join-Path $root '.venv\Scripts\python.exe' }
$Python = (Resolve-Path -LiteralPath $Python).Path

Push-Location $root
try {
    & $Python -c 'import platform,sys; sys.exit(platform.system() != "Windows" or platform.machine() != "AMD64")'
    if ($LASTEXITCODE) { throw 'Use 64-bit x86 Python on Windows to build this package.' }
    $name = 'RKAIQWorkbench'
    $bundle = Join-Path $root "dist\$name"
    $archive = Join-Path $root "dist\$name-win-x64.zip"
    & $Python -m PyInstaller --noconfirm --workpath 'build/pyinstaller' --distpath 'dist' (Join-Path $PSScriptRoot "$name.spec")
    if ($LASTEXITCODE) { throw 'PyInstaller failed.' }

    & $Python (Join-Path $PSScriptRoot 'notices.py') $bundle
    if ($LASTEXITCODE) { throw 'Could not collect license notices.' }
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'START-HERE.txt') -Destination $bundle
    $stagingArchive = Join-Path $root "dist\$name-pending.zip"
    Compress-Archive -LiteralPath $bundle -DestinationPath $stagingArchive -Force
    Move-Item -LiteralPath $stagingArchive -Destination $archive -Force
    Get-Item -LiteralPath $archive
}
finally {
    Pop-Location
}
