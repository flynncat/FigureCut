param(
    [switch]$SetupOnly,
    [switch]$CheckOnly,
    [switch]$Repair,
    [string]$StateRoot
)

$ErrorActionPreference = 'Stop'
$script:AppRoot = $PSScriptRoot
$script:PythonVersion = '3.13.17'
$script:PythonHash = '5da012101ba2e17113a9a1cd901e9ce1441086f2b7ecb5de06ec94163899a190'

function Invoke-Python {
    param([string]$Python, [string[]]$Arguments)
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $Python -X utf8 @Arguments 2>&1 | ForEach-Object { Write-Host $_ }
        $result = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    return $result
}

function Test-Python {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    if ($Path -match '\\WindowsApps\\') { return $false }
    $probe = Join-Path $script:AppRoot 'python_probe.py'
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = & $Path -X utf8 -I $probe 2>$null
        return ($LASTEXITCODE -eq 0 -and $output -contains 'CUTOUT_PYTHON_OK')
    } finally {
        $ErrorActionPreference = $previousPreference
    }
}

function Find-Python {
    param([string]$ManagedPath)
    $candidates = @($ManagedPath)
    foreach ($registryRoot in @('HKCU:\Software\Python\PythonCore', 'HKLM:\Software\Python\PythonCore')) {
        if (Test-Path -LiteralPath $registryRoot) {
            foreach ($version in Get-ChildItem -LiteralPath $registryRoot) {
                $key = Join-Path $version.PSPath 'InstallPath'
                if (Test-Path -LiteralPath $key) {
                    $directory = (Get-Item -LiteralPath $key).GetValue('')
                    if ($directory) { $candidates += Join-Path $directory 'python.exe' }
                }
            }
        }
    }
    foreach ($minor in @('313', '312', '311')) {
        $candidates += Join-Path $env:LOCALAPPDATA "Programs\Python\Python$minor\python.exe"
        if ($env:ProgramFiles) { $candidates += Join-Path $env:ProgramFiles "Python$minor\python.exe" }
    }
    foreach ($command in @('python.exe', 'python3.exe')) {
        $resolved = Get-Command $command -ErrorAction SilentlyContinue
        if ($resolved -and $resolved.CommandType -eq 'Application') { $candidates += $resolved.Source }
    }
    foreach ($candidate in $candidates | Select-Object -Unique) {
        if (Test-Python $candidate) { return $candidate }
    }
    return $null
}

function Get-VerifiedInstaller {
    param([string]$Url, [string]$Path, [string]$Hash, [string]$Publisher)
    $validHash = (Test-Path -LiteralPath $Path) -and $Hash -and
        ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -eq $Hash)
    if (-not $validHash) {
        $partial = "$Path.download"
        for ($attempt = 1; $attempt -le 3; $attempt++) {
            try {
                Write-Host "Downloading installer ($attempt/3): $Url"
                Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $partial -TimeoutSec 180
                if ($Hash -and (Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash -ne $Hash) {
                    throw 'Installer SHA-256 check failed.'
                }
                Move-Item -LiteralPath $partial -Destination $Path -Force
                break
            } catch {
                if ($attempt -eq 3) { throw }
                Start-Sleep -Seconds $attempt
            }
        }
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch $Publisher) {
        throw "Installer signature validation failed: $Path"
    }
}

function Install-VCRuntime {
    param([string]$Cache)
    $installer = Join-Path $Cache 'vc_redist.x64.exe'
    Get-VerifiedInstaller 'https://aka.ms/vc14/vc_redist.x64.exe' $installer '' 'Microsoft Corporation'
    Write-Host 'Installing Microsoft Visual C++ runtime. Windows may request administrator approval.'
    $process = Start-Process -FilePath $installer -ArgumentList '/install /quiet /norestart' -Wait -PassThru -WindowStyle Hidden
    if ($process.ExitCode -notin @(0, 1638, 3010)) {
        throw "Visual C++ runtime installation failed (exit $($process.ExitCode))."
    }
}

function Install-Python {
    param([string]$Destination, [string]$Cache)
    $installer = Join-Path $Cache "python-$script:PythonVersion-amd64.exe"
    $url = "https://www.python.org/ftp/python/$script:PythonVersion/python-$script:PythonVersion-amd64.exe"
    Get-VerifiedInstaller $url $installer $script:PythonHash 'Python Software Foundation'
    Write-Host "Installing Python $script:PythonVersion with Tcl/Tk and pip ..."
    $arguments = @('/quiet', '/norestart', 'InstallAllUsers=0', "TargetDir=`"$Destination`"", 'Include_pip=1',
        'Include_tcltk=1', 'Include_test=0', 'Include_doc=0', 'Include_launcher=0',
        'Include_dev=0', 'AssociateFiles=0', 'Shortcuts=0', 'PrependPath=0', 'AppendPath=0')
    $process = Start-Process -FilePath $installer -ArgumentList $arguments -Wait -PassThru -WindowStyle Hidden
    if ($process.ExitCode -notin @(0, 3010)) {
        throw "Python installation failed (exit $($process.ExitCode))."
    }
    # The official installer may reuse an existing per-user registration.
    $python = Find-Python (Join-Path $Destination 'python.exe')
    if (-not $python) { throw 'Python was installed, but the 64-bit Python/Tcl/Tk check still failed.' }
    return $python
}

function Install-Packages {
    param([string]$Python, [switch]$Force)
    if ((Invoke-Python $Python @('-I', '-m', 'ensurepip', '--upgrade')) -ne 0) {
        throw 'Could not prepare pip.'
    }
    $common = @('-I', '-m', 'pip', '--isolated', '--disable-pip-version-check')
    if ((Invoke-Python $Python ($common + @('install', '--upgrade', 'pip', '--index-url', 'https://pypi.org/simple', '--retries', '3', '--timeout', '60'))) -ne 0) {
        throw 'pip update failed. Check the network connection and retry.'
    }
    $arguments = $common + @('install', '--only-binary=:all:', '--prefer-binary', '--retries', '3', '--timeout', '60',
        '--index-url', 'https://pypi.org/simple', '--extra-index-url', 'https://download.pytorch.org/whl/cpu',
        '-r', (Join-Path $script:AppRoot 'requirements.txt'), '-r', (Join-Path $script:AppRoot 'requirements-runtime.txt'))
    if ($Force) { $arguments += '--force-reinstall' }
    Write-Host 'Installing missing or incompatible dependencies (CPU inference; no GPU driver needed) ...'
    if ((Invoke-Python $Python $arguments) -ne 0) {
        throw 'Dependency installation failed. See the download error above, then double-click the launcher to retry.'
    }
}

function Invoke-Bootstrap {
    $transcript = $false
    $lock = $null
    $log = $null
    try {
        if (-not [Environment]::Is64BitOperatingSystem -or [Environment]::OSVersion.Version.Major -lt 10) {
            throw 'This tool requires 64-bit Windows 10/11.'
        }
        $architecture = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
        if ($architecture -ne 'AMD64') { throw 'This build supports Windows x64 (AMD64/Intel64) computers.' }
        if (-not $StateRoot) { $StateRoot = Join-Path $env:LOCALAPPDATA 'PersonCutoutTool' }
        $StateRoot = [IO.Path]::GetFullPath($StateRoot)
        New-Item -ItemType Directory -Path $StateRoot -Force | Out-Null
        $log = Join-Path $StateRoot 'startup.log'
        Start-Transcript -LiteralPath $log -Append -Force | Out-Null
        $transcript = $true
        Write-Host 'Person Cutout - automatic environment setup'
        Write-Host "Environment and log directory: $StateRoot"
        [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
        $OutputEncoding = [Console]::OutputEncoding
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        foreach ($file in @('requirements.txt', 'requirements-runtime.txt', 'python_probe.py', 'environment_check.py', 'cutout_tool.py', 'cutout_engine.py')) {
            if (-not (Test-Path -LiteralPath (Join-Path $script:AppRoot $file))) {
                throw "Tool files are incomplete ($file). Copy the entire tool folder to this computer."
            }
        }
        $specification = 'bootstrap-v1' + (Get-Content -LiteralPath (Join-Path $script:AppRoot 'requirements.txt') -Raw) +
            (Get-Content -LiteralPath (Join-Path $script:AppRoot 'requirements-runtime.txt') -Raw)
        $hasher = [Security.Cryptography.SHA256]::Create()
        try { $fingerprint = [BitConverter]::ToString($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($specification))).Replace('-', '').Substring(0, 12) }
        finally { $hasher.Dispose() }
        $environmentDir = Join-Path $StateRoot "env-$fingerprint"
        $python = Join-Path $environmentDir 'Scripts\python.exe'
        $helper = Join-Path $script:AppRoot 'environment_check.py'
        $models = Join-Path $StateRoot 'models'
        $cache = Join-Path $StateRoot 'downloads'
        $env:PYTHONUTF8 = '1'
        $env:PYTHONIOENCODING = 'utf-8'
        $env:CUTOUT_MODEL_DIR = $models
        $env:YOLO_CONFIG_DIR = Join-Path $StateRoot 'ultralytics'
        $env:YOLO_OFFLINE = 'true'
        if ($CheckOnly) {
            if (-not (Test-Python $python)) { throw 'The managed environment is missing or damaged. Run the launcher without -CheckOnly to repair it.' }
            $code = Invoke-Python $python @('-I', $helper, '--models', $models)
            if ($code -ne 0) { throw "Environment check failed (code $code). Run the launcher to repair it." }
            Write-Host 'All checks passed. No downloads or installation were performed.'
            return 0
        }
        $lockPath = Join-Path $StateRoot 'setup.lock'
        for ($attempt = 0; $attempt -lt 1800; $attempt++) {
            try { $lock = [IO.File]::Open($lockPath, 'OpenOrCreate', 'ReadWrite', 'None'); break }
            catch [IO.IOException] {
                if ($attempt % 10 -eq 0) { Write-Host 'Another launcher is preparing the environment; waiting ...' }
                Start-Sleep -Milliseconds 500
            }
        }
        if (-not $lock) { throw 'Timed out waiting for another setup process. Close it and retry.' }
        New-Item -ItemType Directory -Path $cache -Force | Out-Null
        if (-not (Test-Path -LiteralPath (Join-Path $env:WINDIR 'System32\msvcp140.dll'))) {
            Install-VCRuntime $cache
        }
        if (-not (Test-Python $python)) {
            $managedPython = Join-Path $StateRoot 'Python313\python.exe'
            $base = Find-Python $managedPython
            if (-not $base) { $base = Install-Python (Join-Path $StateRoot 'Python313') $cache }
            Write-Host "Creating isolated environment with: $base"
            if (Test-Path -LiteralPath $environmentDir) {
                $backup = "$environmentDir.broken-$([Guid]::NewGuid().ToString('N').Substring(0, 8))"
                Move-Item -LiteralPath $environmentDir -Destination $backup
            }
            if ((Invoke-Python $base @('-I', '-m', 'venv', $environmentDir)) -ne 0) { throw 'Could not create the isolated Python environment.' }
        }
        Write-Host '[1/3] Checking Python, Tcl/Tk and CPU inference dependencies ...'
        $code = Invoke-Python $python @('-I', $helper, '--packages-only')
        if ($code -ne 0 -or $Repair) {
            Install-Packages $python -Force:($Repair -or $code -eq 20)
            $code = Invoke-Python $python @('-I', $helper, '--packages-only')
            if ($code -eq 20) {
                Install-VCRuntime $cache
                $code = Invoke-Python $python @('-I', $helper, '--packages-only')
            }
            if ($code -ne 0) { throw "The inference or GUI runtime check still failed (code $code). See the log for the exact module or DLL." }
        }
        Write-Host '[2/3] Checking and preparing model files ...'
        if ((Invoke-Python $python @('-I', $helper, '--models', $models, '--download-models')) -ne 0) {
            throw 'Could not prepare model files. Check the network connection and retry.'
        }
        $lock.Dispose()
        $lock = $null
        Write-Host '[3/3] Environment ready.'
        if ($SetupOnly) { return 0 }
        Write-Host 'Opening the cutout tool. Keep this window open while the tool is running.'
        return (Invoke-Python $python @('-E', '-s', (Join-Path $script:AppRoot 'cutout_tool.py')))
    } catch {
        Write-Host ''
        Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
        if ($log) { Write-Host "Detailed log: $log" }
        Write-Host 'Nothing was removed from your photos or output folders. Retry after resolving the error.'
        return 1
    } finally {
        if ($lock) { $lock.Dispose() }
        if ($transcript) { Stop-Transcript | Out-Null }
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    exit (Invoke-Bootstrap)
}
