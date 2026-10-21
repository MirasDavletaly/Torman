param(
    [ValidatePattern('^[A-Za-z0-9._-]+$')]
    [string]$ExecutableName = 'Torman'
)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
Set-Location -LiteralPath $projectRoot
& .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --onefile --windowed --name $ExecutableName --icon 'static/brand/torman.ico' --add-data 'static;static' --hidden-import pypdf --distpath dist --workpath build launcher.py
if ($LASTEXITCODE -ne 0) { throw 'Executable build failed.' }
Copy-Item -LiteralPath 'USER_GUIDE.txt' -Destination 'dist/USER_GUIDE.txt' -Force
$packageDirectory = Join-Path $projectRoot "build/package-$ExecutableName"
New-Item -ItemType Directory -Path $packageDirectory -Force | Out-Null
Copy-Item -LiteralPath "dist/$ExecutableName.exe" -Destination (Join-Path $packageDirectory 'Torman.exe') -Force
Copy-Item -LiteralPath 'USER_GUIDE.txt' -Destination (Join-Path $packageDirectory 'USER_GUIDE.txt') -Force
Compress-Archive -LiteralPath (Join-Path $packageDirectory 'Torman.exe'),(Join-Path $packageDirectory 'USER_GUIDE.txt') -DestinationPath 'dist/Torman-Windows.zip' -Force
Write-Output 'Ready: dist/Torman-Windows.zip'
