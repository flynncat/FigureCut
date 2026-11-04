$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'bootstrap.ps1')

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
    Write-Host "PASS: $Message"
}

$candidate = (Get-Command python.exe).Source
Assert-True (Test-Python $candidate) 'Existing 64-bit Python with Tcl/Tk is detected by Windows PowerShell'
Assert-True (-not (Test-Python 'C:\missing-cutout-python\python.exe')) 'Missing interpreter is rejected'
Assert-True (-not (Test-Python 'C:\Users\unused\AppData\Local\Microsoft\WindowsApps\python.exe')) 'Store alias is never launched'
$detected = Find-Python $candidate
Assert-True ($detected -eq $candidate) 'A working managed candidate is reused before other Python installs'

$script:CapturedArguments = $null
function Get-VerifiedInstaller { param($Url, $Path, $Hash, $Publisher) }
function Start-Process {
    param($FilePath, $ArgumentList, [switch]$Wait, [switch]$PassThru, $WindowStyle)
    $script:CapturedArguments = $ArgumentList
    return [PSCustomObject]@{ ExitCode = 0 }
}
function Find-Python { param($ManagedPath) return $ManagedPath }
$script:InstallTarget = Join-Path $env:TEMP 'cutout bootstrap tests\Python313'
$installed = Install-Python $script:InstallTarget $env:TEMP
Assert-True ($installed -eq (Join-Path $script:InstallTarget 'python.exe')) 'Missing Python branch returns the discovered interpreter'
Assert-True ($script:CapturedArguments -contains '/norestart') 'Python installation cannot automatically restart Windows'
Assert-True ($script:CapturedArguments -contains 'InstallAllUsers=0') 'Python installation is scoped to the current user'
Assert-True ($script:CapturedArguments -contains 'Include_tcltk=1') 'Python installation includes the GUI runtime'
Assert-True ($script:CapturedArguments -contains "TargetDir=`"$script:InstallTarget`"") 'Installer target preserves spaces'

function Start-Process {
    param($FilePath, $ArgumentList, [switch]$Wait, [switch]$PassThru, $WindowStyle)
    return [PSCustomObject]@{ ExitCode = 1603 }
}
$failed = $false
try { Install-Python $script:InstallTarget $env:TEMP } catch { $failed = $true }
Assert-True $failed 'Installer failure prevents application startup'

$script:Commands = [Collections.Generic.List[object]]::new()
function Invoke-Python {
    param($Python, $Arguments)
    $script:Commands.Add(@($Arguments))
    return 0
}
Install-Packages $candidate
$install = $script:Commands[$script:Commands.Count - 1]
Assert-True ($install -contains '--only-binary=:all:') 'New computers never need a C/C++ build toolchain for pip packages'
Assert-True ($install -contains 'https://download.pytorch.org/whl/cpu') 'Matching CPU inference wheels come from the official PyTorch index'
Assert-True ($install -contains (Join-Path $PSScriptRoot 'requirements-runtime.txt')) 'Pinned torch and torchvision versions are installed together'
Write-Host 'Bootstrap regression tests passed.'
