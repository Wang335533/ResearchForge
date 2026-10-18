#requires -Version 7.0
# Run manually from PowerShell 7. This script checks ownership and active work
# before stopping the portable server; it never removes application data.
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$rfRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot) -ErrorAction Stop).Path
$rfPython = Join-Path $rfRoot 'runtime\python\python.exe'
$rfConfigPath = Join-Path $rfRoot 'config.json'
if (-not (Test-Path -LiteralPath $rfConfigPath)) {
    $rfConfigPath = Join-Path $rfRoot 'config.example.json'
}
$rfConfig = Get-Content -LiteralPath $rfConfigPath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
$rfWorkspace = if ($rfConfig.PSObject.Properties['workspace']) { [string]$rfConfig.workspace } else { 'user_data' }
if (-not [IO.Path]::IsPathRooted($rfWorkspace)) { $rfWorkspace = Join-Path $rfRoot $rfWorkspace }
$rfStatePath = Join-Path $rfWorkspace 'portable-state.json'
$rfPort = if ($rfConfig.PSObject.Properties['port']) { [int]$rfConfig.port } else { 8331 }
if (Test-Path -LiteralPath $rfStatePath) {
    $rfState = Get-Content -LiteralPath $rfStatePath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
    $rfPort = [int]$rfState.port
}
if ($rfPort -lt 1024 -or $rfPort -gt 65535) { throw 'Invalid service port; nothing was stopped.' }
if (-not (Test-Path -LiteralPath $rfPython)) { throw 'Bundled Python was not found.' }

$rfWork = Join-Path $rfRoot 'work'
New-Item -ItemType Directory -Path $rfWork -Force -ErrorAction Stop | Out-Null
$rfLog = Join-Path $rfWork ('restart-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
$rfSavedTasks = @()
$rfListeners = @(Get-NetTCPConnection -LocalPort $rfPort -State Listen -ErrorAction SilentlyContinue)
if ($rfListeners.Count -gt 1) { throw 'Multiple listeners found; nothing was stopped.' }

if ($rfListeners.Count -eq 1) {
    $rfProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($rfListeners[0].OwningProcess)" -ErrorAction Stop
    if ($rfProcess.ExecutablePath -ne $rfPython -or $rfProcess.CommandLine -notmatch 'portable\.py') {
        throw 'The port belongs to another program; nothing was stopped.'
    }
    $rfUrl = "http://127.0.0.1:$rfPort"
    $rfTasks = (Invoke-RestMethod "$rfUrl/api/tasks" -TimeoutSec 10 -ErrorAction Stop).tasks
    $rfJobs = Invoke-RestMethod "$rfUrl/papers/api/jobs" -TimeoutSec 10 -ErrorAction Stop
    $rfExports = Invoke-RestMethod "$rfUrl/api/report-exports/active" -TimeoutSec 10 -ErrorAction Stop
    if (@($rfTasks | Where-Object status -in 'queued','running','stopping').Count -or
        @($rfJobs.jobs | Where-Object status -in 'queued','running','stopping').Count -or $rfJobs.memory_active -or $rfExports.active) {
        throw 'There are active tasks. Wait for them to finish before restarting.'
    }
    $rfSavedTasks = @(foreach ($rfTask in $rfTasks) {
        $rfDetail = (Invoke-RestMethod "$rfUrl/api/tasks/$($rfTask.id)" -TimeoutSec 10 -ErrorAction Stop).task
        [pscustomobject]@{id=$rfDetail.id; status=$rfDetail.status; ideas=@($rfDetail.ideas).Count; proposals=@($rfDetail.proposals).Count}
    })
    ConvertTo-Json -InputObject $rfSavedTasks -Depth 5 | Set-Content -LiteralPath "$rfLog.before.json" -Encoding utf8 -ErrorAction Stop
    # Check again immediately before acting, including process creation time.
    $rfCurrent = Get-CimInstance Win32_Process -Filter "ProcessId = $($rfProcess.ProcessId)" -ErrorAction Stop
    if (-not $rfCurrent -or $rfCurrent.ExecutablePath -ne $rfPython -or $rfCurrent.CreationDate -ne $rfProcess.CreationDate) {
        throw 'The service process changed. Run the script again to inspect it safely.'
    }
    $rfLatestTasks = (Invoke-RestMethod "$rfUrl/api/tasks" -TimeoutSec 10 -ErrorAction Stop).tasks
    $rfLatestJobs = Invoke-RestMethod "$rfUrl/papers/api/jobs" -TimeoutSec 10 -ErrorAction Stop
    $rfLatestExports = Invoke-RestMethod "$rfUrl/api/report-exports/active" -TimeoutSec 10 -ErrorAction Stop
    if (@($rfLatestTasks | Where-Object status -in 'queued','running','stopping').Count -or
        @($rfLatestJobs.jobs | Where-Object status -in 'queued','running','stopping').Count -or
        $rfLatestJobs.memory_active -or $rfLatestExports.active) {
        throw 'New work started during verification. Nothing was stopped; wait for it to finish.'
    }
    Write-Host "Stopping the verified idle service (PID $($rfCurrent.ProcessId))..."
    Stop-Process -Id $rfCurrent.ProcessId -ErrorAction Stop
    Wait-Process -Id $rfCurrent.ProcessId -Timeout 10 -ErrorAction SilentlyContinue
    if (Get-Process -Id $rfCurrent.ProcessId -ErrorAction SilentlyContinue) { throw 'The old process has not exited.' }
} else {
    $rfExisting = @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
        $_.ExecutablePath -eq $rfPython -and $_.CommandLine -match 'portable\.py'
    })
    if ($rfExisting.Count) { throw 'A portable process is starting or unresponsive. Nothing was stopped; check it before retrying.' }
}

$rfStart = @{
    FilePath = $rfPython
    ArgumentList = @('-I','-B','-X','utf8','portable.py','--no-browser','--port',"$rfPort")
    WorkingDirectory = $rfRoot
    WindowStyle = 'Hidden'
    RedirectStandardOutput = "$rfLog.stdout.log"
    RedirectStandardError = "$rfLog.stderr.log"
    PassThru = $true
    ErrorAction = 'Stop'
}
$rfNew = Start-Process @rfStart
Write-Host "Started PID $($rfNew.Id). Waiting for the web service and literature index..."
$rfReady = $false
for ($rfAttempt = 0; $rfAttempt -lt 120; $rfAttempt++) {
    Start-Sleep -Seconds 2
    $rfNew.Refresh()
    if ($rfNew.HasExited) { throw "Startup process exited. See $rfLog.stderr.log" }
    try {
        $rfState = Get-Content -LiteralPath $rfStatePath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        if ([int]$rfState.pid -ne $rfNew.Id) { continue }
        $rfUrl = "http://127.0.0.1:$([int]$rfState.port)"
        $rfHealth = Invoke-RestMethod "$rfUrl/api/health" -TimeoutSec 3 -ErrorAction Stop
    } catch { continue }
    if ($rfHealth.retrieval.error) { throw "Web service started, but the literature index reported: $($rfHealth.retrieval.error)" }
    if ($rfHealth.ok -and $rfHealth.retrieval.ready) { $rfReady = $true; break }
    if ($rfAttempt % 5 -eq 0) { Write-Host "Preparing literature index: $($rfHealth.retrieval.message)" }
}
if (-not $rfReady) { throw "Still waiting for startup. The process was left running. See $rfLog.stderr.log" }
foreach ($rfSaved in $rfSavedTasks) {
    $rfAfter = (Invoke-RestMethod "$rfUrl/api/tasks/$($rfSaved.id)" -TimeoutSec 10 -ErrorAction Stop).task
    if ($rfAfter.status -ne $rfSaved.status -or @($rfAfter.ideas).Count -ne $rfSaved.ideas -or @($rfAfter.proposals).Count -ne $rfSaved.proposals) {
        throw 'The service restarted, but a saved task changed. Inspect the saved before.json verification record.'
    }
}
Write-Host "Restart complete. PID: $($rfNew.Id); papers: $($rfHealth.corpus); saved tasks verified: $($rfSavedTasks.Count)."
$rfRuntime = Invoke-RestMethod "$rfUrl/api/portable/health" -TimeoutSec 10 -ErrorAction Stop
if (-not $rfRuntime.runtime.windows_proxy_fallback -or $rfRuntime.runtime.response_read_timeout_seconds -ne 1800) {
    throw 'The service is running but the updated network configuration was not loaded.'
}
Write-Host 'Verified Windows proxy fallback and streaming read timeout (1800 seconds).'
if ($rfConfig.PSObject.Properties['fulltext_roots'] -and @($rfConfig.fulltext_roots).Count) {
    # The first read of a large index right after startup can be slow; wait instead of failing at once.
    $rfLibrary = $null
    for ($rfTry = 1; $rfTry -le 4 -and -not $rfLibrary; $rfTry++) {
        try { $rfLibrary = Invoke-RestMethod "$rfUrl/papers/api/library/status" -TimeoutSec 60 -ErrorAction Stop }
        catch { Write-Host "Full-text library check $rfTry/4 has not answered yet; retrying..." }
    }
    if (-not $rfLibrary) { throw "The service is running, but the full-text library check did not answer within 4 minutes. Open $rfUrl and check the library search; see $rfLog.stderr.log" }
    if (-not $rfLibrary.ready) { throw "Service restarted, but the full-text library is not ready: $($rfLibrary.message)" }
    if (-not $rfLibrary.PSObject.Properties['roots']) { throw 'The service has not loaded the new full-text configuration.' }
    foreach ($rfSource in $rfConfig.fulltext_roots) {
        $rfExpected = if ([IO.Path]::IsPathRooted($rfSource)) { $rfSource } else { Join-Path $rfRoot $rfSource }
        $rfExpected = (Resolve-Path -LiteralPath $rfExpected -ErrorAction Stop).Path
        if ($rfExpected -notin @($rfLibrary.roots)) { throw "The full-text directory was not loaded: $rfExpected" }
    }
    Write-Host "Full-text library ready: $($rfLibrary.count) files; configured directories verified."
}
Write-Host "Open $rfUrl and press Ctrl+F5."
