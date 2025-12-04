<#
ci-build.ps1
Used by GitHub Actions to create a Windows exe with PyInstaller.
#>

param(
    [string]$pythonVersion = "3.10",
    [string]$script = "rs_fr_lookup_v10.py",
    [string]$outputName = "rs_fr_lookup_v10.exe",
    [switch]$onefile = $true
)

Write-Host "=== CI Build script ==="
Write-Host "Python: $pythonVersion  script: $script outputName: $outputName"

python -m pip install --upgrade pip
pip install --upgrade wheel
# install runtime + build deps
pip install pyinstaller requests beautifulsoup4 pandas certifi

# Clean previous build directories
if (Test-Path "dist") { Remove-Item -Recurse -Force dist }
if (Test-Path "build") { Remove-Item -Recurse -Force build }
if (Test-Path "$script.spec") { Remove-Item -Force "$script.spec" }

$pyiArgs = @()
if ($onefile) { $pyiArgs += "--onefile" }
$pyiArgs += "--noconfirm"
$pyiArgs += "--console"

$pyiArgs += $script

Write-Host "Running pyinstaller with args:" $pyiArgs
pyinstaller @pyiArgs

# Move built exe to predictable name/location for artifact upload
$exePath = Join-Path -Path "dist" -ChildPath ((Split-Path -Leaf $script).Replace(".py",".exe"))
if (-Not (Test-Path $exePath)) {
    Throw "Build failed; expected exe not found: $exePath"
}
$finalExe = Join-Path -Path "." -ChildPath $outputName
Copy-Item -Force $exePath $finalExe
Write-Host "Created:" $finalExe
Get-Item $finalExe | Format-List Name,Length,LastWriteTime
