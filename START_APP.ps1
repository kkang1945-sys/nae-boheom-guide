param(
    [switch]$NoBrowser,
    [int]$HealthTimeoutSeconds = 15
)

$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $MyInvocation.MyCommand.Path
$appUrl = 'http://127.0.0.1:8000'
$healthUrl = "$appUrl/health"
$storedHiraKey = [Environment]::GetEnvironmentVariable('HIRA_DISEASE_API_KEY', 'User')
if ($storedHiraKey) {
    $env:HIRA_DISEASE_API_KEY = $storedHiraKey
}

$projectPython = Join-Path $projectPath '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $projectPython) {
    $pythonPath = $projectPython
} else {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        Write-Host '앱을 실행할 Python을 찾지 못했습니다.'
        Read-Host 'Enter 키를 누르면 닫힙니다'
        exit 1
    }
    $pythonPath = $pythonCommand.Source
}

Set-Location -LiteralPath $projectPath
$env:PYTHONPATH = Join-Path $projectPath 'src'

try {
    $existingHealth = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 1
} catch {
    $existingHealth = $null
}

if ($existingHealth.status -eq 'ok') {
    Write-Host "보험앱이 이미 실행 중입니다: $appUrl"
    if (-not $NoBrowser) {
        Start-Process $appUrl | Out-Null
    }
    exit 0
}

$browserJob = $null
if (-not $NoBrowser) {
    $browserJob = Start-Job -ScriptBlock {
        param($targetHealthUrl, $targetAppUrl, $timeoutSeconds)

        $deadline = (Get-Date).AddSeconds($timeoutSeconds)
        while ((Get-Date) -lt $deadline) {
            try {
                $health = Invoke-RestMethod -Uri $targetHealthUrl -TimeoutSec 1
                if ($health.status -eq 'ok') {
                    Start-Process $targetAppUrl | Out-Null
                    return
                }
            } catch {
                Start-Sleep -Milliseconds 200
            }
        }
    } -ArgumentList $healthUrl, $appUrl, $HealthTimeoutSeconds
}

Write-Host "보험앱을 시작합니다: $appUrl"
if (-not $NoBrowser) {
    Write-Host '서버 준비가 끝나면 브라우저가 자동으로 열립니다.'
}
Write-Host '이 창을 닫으면 앱도 종료됩니다.'

try {
    & $pythonPath -m uvicorn insurance_app.main:app --host 127.0.0.1 --port 8000
    exit $LASTEXITCODE
} finally {
    if ($browserJob) {
        Stop-Job -Job $browserJob -ErrorAction SilentlyContinue
        Remove-Job -Job $browserJob -Force -ErrorAction SilentlyContinue
    }
}
