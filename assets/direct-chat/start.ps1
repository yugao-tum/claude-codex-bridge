$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$taskPython = (Get-Command py.exe -ErrorAction Stop).Source
$taskPortText = & $taskPython -X utf8 (Join-Path $taskRoot 'config.py') --port
if ($LASTEXITCODE -ne 0) { throw 'Complete config.json before starting Claude direct.' }
$taskPort = [int]$taskPortText
$taskUrl = "http://127.0.0.1:$taskPort"
$taskState = Join-Path $taskRoot 'state'
New-Item -ItemType Directory -Path $taskState -Force | Out-Null
$taskReady = $false
try {
    $taskResponse = Invoke-WebRequest -Uri $taskUrl -TimeoutSec 2 -UseBasicParsing
    $taskReady = $taskResponse.StatusCode -eq 200 -and $taskResponse.Content.Contains('Claude')
} catch { }
if (-not $taskReady) {
    Start-Process -FilePath $taskPython -ArgumentList @('-X','utf8',('"' + (Join-Path $taskRoot 'app.py') + '"')) -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskState 'server.stdout.log') -RedirectStandardError (Join-Path $taskState 'server.stderr.log') | Out-Null
    for ($taskWait = 0; $taskWait -lt 30; $taskWait++) {
        Start-Sleep -Milliseconds 500
        try {
            $taskResponse = Invoke-WebRequest -Uri $taskUrl -TimeoutSec 2 -UseBasicParsing
            if ($taskResponse.StatusCode -eq 200) { $taskReady = $true; break }
        } catch { }
    }
}
if (-not $taskReady) { throw ('Claude direct did not start. See ' + (Join-Path $taskState 'server.stderr.log')) }
Write-Output $taskUrl
if ($env:CLAUDE_DIRECT_NO_BROWSER -ne '1') { Start-Process $taskUrl }
