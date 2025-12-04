<#
ci-build.ps1
Used by GitHub Actions to create a Windows exe with PyInstaller.
#>

param(
    [string]$pythonVersion = "3.10",
    [string]$script = "rs_fr_lookup_v9.py",
    [string]$outputName = "rs_fr_lookup_v9.exe",
    [switch]$onefile = $true,
    [string]$addData = ""  # example: 'input.csv;.'
)

Write-Host "=== CI Build script ==="
Write-Host "Python: $pythonVersion  script: $script outputName: $outputName"

# Prepare venv (optional) - GitHub runner already has Python runtime, we'll install into the runner
python -m pip install --upgrade pip
pip install --upgrade wheel
pip install pyinstaller requests beautifulsoup4 pandas certifi

# Clean previous build directories
if (Test-Path "dist") { Remove-Item -Recurse -Force dist }
if (Test-Path "build") { Remove-Item -Recurse -Force build }
if (Test-Path "$script.spec") { Remove-Item -Force "$script.spec" }

# Build args
$pyiArgs = @()
if ($onefile) { $pyiArgs += "--onefile" }
$pyiArgs += "--noconfirm"
$pyiArgs += "--console"
# If you want to include input.csv from repo into the exe (uncomment):
if ($addData) { $pyiArgs += "--add-data"; $pyiArgs += $addData }
# Optionally add an icon:
# $pyiArgs += "--icon"; $pyiArgs += "assets/myicon.ico"

$pyiArgs += $script

Write-Host "Running pyinstaller with args:" $pyiArgs
pyinstaller @pyiArgs

# Move built exe to a predictable name/location for artifact upload
$exePath = Join-Path -Path "dist" -ChildPath ((Split-Path -Leaf $script).Replace(".py",".exe"))
if (-Not (Test-Path $exePath)) {
    Throw "Build failed; expected exe not found: $exePath"
}
# rename and copy to top-level output
$finalExe = Join-Path -Path "." -ChildPath $outputName
Copy-Item -Force $exePath $finalExe
Write-Host "Created:" $finalExe
# list file details
Get-Item $finalExe | Format-List Name,Length,LastWriteTime
