$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

Write-Host ""
Write-Host "FORTERA TELL0 BRIDGE - WINDOWS INSTALLER" -ForegroundColor Cyan
Write-Host "This installs a private Python runtime and the bridge locally for this Windows user."
Write-Host ""

$arch = $env:PROCESSOR_ARCHITECTURE
if ($env:PROCESSOR_ARCHITEW6432) { $arch = $env:PROCESSOR_ARCHITEW6432 }
if ($arch -notmatch 'AMD64') {
    throw "This installer currently supports Windows x64 only. Detected: $arch"
}

$packageBridge = Join-Path $PSScriptRoot "bridge"
if (-not (Test-Path (Join-Path $packageBridge "gateway.py"))) {
    throw "Cannot find bridge/gateway.py. Extract the complete Fortera package before running this installer."
}

$homeDir = Join-Path $env:LOCALAPPDATA "ForteraTelloBridge"
$pythonDir = Join-Path $homeDir "python"
$venvDir = Join-Path $homeDir "venv"
$targetBridge = Join-Path $homeDir "bridge"
New-Item -ItemType Directory -Force -Path $homeDir | Out-Null

$pythonExe = Join-Path $pythonDir "python.exe"
if (-not (Test-Path $pythonExe)) {
    Write-Host "[1/4] Downloading Python 3.13.17 from python.org..." -ForegroundColor Yellow
    $installer = Join-Path $env:TEMP "python-3.13.17-amd64.exe"
    $url = "https://www.python.org/ftp/python/3.13.17/python-3.13.17-amd64.exe"
    Invoke-WebRequest -Uri $url -OutFile $installer -UseBasicParsing
    Write-Host "[2/4] Installing private Python runtime..." -ForegroundColor Yellow
    $args = @(
        "/quiet", "InstallAllUsers=0", "TargetDir=`"$pythonDir`"",
        "Include_pip=1", "Include_tcltk=1", "Include_test=0",
        "PrependPath=0", "Shortcuts=0", "Include_launcher=0"
    )
    $p = Start-Process -FilePath $installer -ArgumentList $args -Wait -PassThru
    if ($p.ExitCode -ne 0 -and $p.ExitCode -ne 3010) {
        throw "Python installer failed with exit code $($p.ExitCode)."
    }
} else {
    Write-Host "[1/4] Private Python runtime already installed." -ForegroundColor Green
}

if (-not (Test-Path $pythonExe)) { throw "Python was not installed at $pythonExe" }

Write-Host "[3/4] Copying the current Fortera bridge source..." -ForegroundColor Yellow
if (Test-Path $targetBridge) { Remove-Item -Recurse -Force $targetBridge }
New-Item -ItemType Directory -Force -Path $targetBridge | Out-Null
Copy-Item -Path (Join-Path $packageBridge "*") -Destination $targetBridge -Recurse -Force

$venvPython = Join-Path $venvDir "Scripts\python.exe"
$venvPythonw = Join-Path $venvDir "Scripts\pythonw.exe"
if (-not (Test-Path $venvPython)) {
    & $pythonExe -m venv $venvDir
    if ($LASTEXITCODE -ne 0) { throw "Could not create the private Python environment." }
}
Write-Host "[4/4] Installing bridge libraries (WebSockets, NumPy and OpenCV)..." -ForegroundColor Yellow
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed." }
& $venvPython -m pip install -r (Join-Path $targetBridge "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Bridge dependency installation failed." }

# Desktop shortcut starts the manager GUI, not a terminal window.
$manager = Join-Path $targetBridge "windows\manager.py"
if (-not (Test-Path $manager)) { throw "The Windows bridge manager is missing." }
$shell = New-Object -ComObject WScript.Shell
$shortcutPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "Fortera Tello Bridge.lnk"
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $venvPythonw
$shortcut.Arguments = '"' + $manager + '"'
$shortcut.WorkingDirectory = $targetBridge
$shortcut.Description = "Fortera local Tello bridge manager"
$shortcut.Save()

Write-Host ""
Write-Host "Installation complete." -ForegroundColor Green
Write-Host "A 'Fortera Tello Bridge' shortcut was placed on your Desktop."
Write-Host "The first start asks for the exact published Fortera website origin."
Write-Host ""
Start-Process -FilePath $venvPythonw -ArgumentList @($manager) -WorkingDirectory $targetBridge
