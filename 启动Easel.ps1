param([switch]$Restart)

$ErrorActionPreference = 'Stop'
$easelRoot = $PSScriptRoot
$easelUrl = 'http://127.0.0.1:7860'
$env:PYTHONUTF8 = '1'
$env:EASEL_PORT = '7860'
# Prefer the CLI shipped with the desktop app over an older global npm shim.
# This changes PATH for Easel and its children only.
$easelCodexBin = Join-Path $env:LOCALAPPDATA 'OpenAI\Codex\bin'
$easelBundledCodex = Get-ChildItem -LiteralPath $easelCodexBin -Filter 'codex.exe' -Recurse -File -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($easelBundledCodex) { $env:Path = $easelBundledCodex.DirectoryName + ';' + $env:Path }
function Test-EaselReady {
    try {
        $response = Invoke-WebRequest -Uri $easelUrl -UseBasicParsing -TimeoutSec 2
        return ($response.StatusCode -eq 200 -and $response.Content -match 'Easel')
    } catch { return $false }
}
function Stop-EaselService {
    # The venv python shim re-executes uvicorn in a second python.exe, so kill
    # every process running our command line instead of trusting web.pid.
    for ($easelSweep = 0; $easelSweep -lt 3; $easelSweep++) {
        $easelStale = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -and $_.CommandLine -match 'uvicorn\s+web\.app:app' })
        if ($easelStale.Count -eq 0) { break }
        foreach ($easelProc in $easelStale) {
            Stop-Process -Id $easelProc.ProcessId -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Milliseconds 300
    }
    for ($easelWait = 0; $easelWait -lt 24; $easelWait++) {
        $easelBusy = Get-NetTCPConnection -LocalPort 7860 -State Listen -ErrorAction SilentlyContinue
        if (-not $easelBusy) { return }
        Start-Sleep -Milliseconds 250
    }
    $easelOwner = (Get-NetTCPConnection -LocalPort 7860 -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1).OwningProcess
    throw ('Port 7860 is still in use by PID ' + $easelOwner + '. Stop that process, then rerun.')
}
if ($Restart) {
    Write-Host 'Restarting Easel: stopping the running instance...'
    Stop-EaselService
}
if (-not (Test-EaselReady)) {
    $easelPython = Join-Path $easelRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $easelPython)) { throw 'Easel virtual environment is missing.' }
    $easelLogs = Join-Path $easelRoot 'outputs\_local'
    New-Item -ItemType Directory -Path $easelLogs -Force | Out-Null
    $easelArgs = @('-m', 'uvicorn', 'web.app:app', '--host', '127.0.0.1', '--port', '7860')
    $easelStdout = Join-Path $easelLogs 'web.log'
    $easelStderr = Join-Path $easelLogs 'web.err.log'
    for ($easelTry = 1; $easelTry -le 2; $easelTry++) {
        try {
            $easelProcess = Start-Process -FilePath $easelPython -ArgumentList $easelArgs `
                -WorkingDirectory $easelRoot -WindowStyle Hidden -PassThru `
                -RedirectStandardOutput $easelStdout -RedirectStandardError $easelStderr
            break
        } catch {
            # PowerShell 5.1 cannot build the child environment when the block
            # holds case-duplicate keys (HTTP_PROXY together with http_proxy is
            # common in Git Bash / VS Code / wrapper shells). Drop the lowercase
            # twins and retry once.
            $easelEnvConflict = ($_.Exception -is [System.ArgumentException]) -or
                ($_.Exception.InnerException -is [System.ArgumentException])
            if (-not $easelEnvConflict -or $easelTry -eq 2) { throw }
            foreach ($easelDuplicate in 'http_proxy', 'https_proxy', 'no_proxy', 'all_proxy') {
                Remove-Item ('Env:\' + $easelDuplicate) -ErrorAction SilentlyContinue
            }
        }
    }
    $easelProcess.Id | Set-Content -LiteralPath (Join-Path $easelLogs 'web.pid')
    $easelReady = $false
    for ($easelAttempt = 0; $easelAttempt -lt 30; $easelAttempt++) {
        if (Test-EaselReady) { $easelReady = $true; break }
        if ($easelProcess.HasExited) { break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $easelReady) { throw ('Easel did not start. Check logs: ' + $easelLogs) }
}
try { Start-Process $easelUrl } catch { Write-Host ('Open this URL manually: ' + $easelUrl) }
Write-Host ('Easel ready: ' + $easelUrl)
