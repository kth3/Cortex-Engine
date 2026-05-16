<#
.SYNOPSIS
  Cortex 좀비 프로세스·VRAM 누수 진단 (Windows PowerShell).

.DESCRIPTION
  cortex-ctl 라이프사이클을 정상 종료/강제 종료 두 시나리오로 돌려
  자식 프로세스(워커, 워처) 잔존 여부와 VRAM 해제 여부를 보고합니다.

  사용: PowerShell에서 .\zombie-check.ps1 실행.
  요구사항: PATH에 cortex-ctl, psutil 동작 가능한 python.

.OUTPUTS
  표준출력에 단계별 PID 목록과 잔존 검사 결과를 출력합니다. 종료 코드 0=정상,
  1=잔존 프로세스 또는 VRAM 누수 감지.
#>

$ErrorActionPreference = "Stop"

function Get-CortexPids {
    $procs = Get-CimInstance Win32_Process |
        Where-Object { $_.CommandLine -match 'cortex|vector_engine_server|watch[\\/]daemon\.py' }
    return $procs | Select-Object ProcessId, Name, CommandLine
}

function Get-VramUsedMb {
    try {
        $out = & nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) {
            return [int]($out -split "`n" | Select-Object -First 1).Trim()
        }
    } catch {}
    return $null
}

function Stage($title) {
    Write-Host ""
    Write-Host "=== $title ===" -ForegroundColor Cyan
}

$failures = @()

# 0. 기준선
Stage "0. 기준선 (cortex 미기동)"
$baselinePids = Get-CortexPids
$baselineVram = Get-VramUsedMb
Write-Host "잔존 cortex 프로세스: $($baselinePids.Count)"
Write-Host "VRAM 사용량(MiB): $(if ($baselineVram -ne $null) { $baselineVram } else { 'n/a (nvidia-smi 부재)' })"
if ($baselinePids.Count -gt 0) {
    Write-Host "[경고] 기준선에 이미 잔존 프로세스가 있습니다. 결과 신뢰도 저하." -ForegroundColor Yellow
}

# 1. 정상 start → stop
Stage "1. cortex-ctl start"
& cortex-ctl start
Start-Sleep -Seconds 4
$afterStart = Get-CortexPids
Write-Host "기동된 cortex 프로세스 수: $($afterStart.Count)"
$afterStart | Format-Table ProcessId, Name -AutoSize | Out-Host
$vramAfterStart = Get-VramUsedMb
Write-Host "VRAM 사용량(MiB): $(if ($vramAfterStart -ne $null) { $vramAfterStart } else { 'n/a' })"

Stage "2. cortex-ctl stop"
& cortex-ctl stop
Start-Sleep -Seconds 4
$afterStop = Get-CortexPids
Write-Host "stop 직후 잔존 프로세스 수: $($afterStop.Count)"
$afterStop | Format-Table ProcessId, Name -AutoSize | Out-Host
if ($afterStop.Count -gt 0) {
    $failures += "정상 stop 후 잔존 프로세스 $($afterStop.Count)개"
}
$vramAfterStop = Get-VramUsedMb
if ($baselineVram -ne $null -and $vramAfterStop -ne $null) {
    $diff = $vramAfterStop - $baselineVram
    Write-Host "VRAM 차이(MiB, 기준선 대비): $diff"
    if ($diff -gt 100) {
        $failures += "정상 stop 후 VRAM $diff MiB 누수"
    }
}

# 2. 강제 종료 시나리오
Stage "3. cortex-ctl start (재기동)"
& cortex-ctl start
Start-Sleep -Seconds 4
$beforeKill = Get-CortexPids
Write-Host "재기동된 프로세스: $($beforeKill.Count)"

Stage "4. cortex-ctl 부모 프로세스 강제 종료 (taskkill /F)"
# control 프로세스 자체는 cortex-ctl 명령이 끝나면 사라지므로,
# 여기서는 워커/워처 PID만 추출해 부모 chain을 끊는 시나리오를 시뮬레이션합니다.
$workerPids = $beforeKill | Where-Object { $_.CommandLine -match 'vector_engine_server|watch[\\/]daemon\.py' }
foreach ($p in $workerPids) {
    Write-Host "taskkill /F /PID $($p.ProcessId) ($($p.Name))"
    & taskkill /F /PID $p.ProcessId | Out-Null
}
Start-Sleep -Seconds 3
$afterKill = Get-CortexPids
Write-Host "강제 종료 후 잔존: $($afterKill.Count)"
$afterKill | Format-Table ProcessId, Name -AutoSize | Out-Host
if ($afterKill.Count -gt 0) {
    $failures += "강제 종료 후 자식 잔존 $($afterKill.Count)개"
}
$vramAfterKill = Get-VramUsedMb
if ($baselineVram -ne $null -and $vramAfterKill -ne $null) {
    $diff = $vramAfterKill - $baselineVram
    Write-Host "VRAM 차이(MiB): $diff"
    if ($diff -gt 100) {
        $failures += "강제 종료 후 VRAM $diff MiB 누수"
    }
}

# 결과
Stage "결과"
if ($failures.Count -eq 0) {
    Write-Host "OK — 잔존 프로세스·VRAM 누수 감지 없음." -ForegroundColor Green
    exit 0
} else {
    Write-Host "FAIL:" -ForegroundColor Red
    $failures | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    exit 1
}
