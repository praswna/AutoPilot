# 관리자 PowerShell에서 실행하세요.
# 방법: 시작 → PowerShell → 마우스 우클릭 → "관리자 권한으로 실행"
# 실행: powershell -ExecutionPolicy Bypass -File "C:\Users\prasw\Dropbox\Cluade\ClaudeAutoPilot\register_task.ps1"

$taskName   = "ClaudeAutoResume"
$scriptPath = "$env:USERPROFILE\Dropbox\Cluade\AutoPilot\claude_resume.ps1"
$userName   = $env:USERNAME           # 실행할 사용자 이름

# 기존 작업 제거
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

$action = New-ScheduledTaskAction `
    -Execute   "powershell.exe" `
    -Argument  "-NonInteractive -ExecutionPolicy Bypass -File `"$scriptPath`"" `
    -WorkingDirectory $env:USERPROFILE

# ── 트리거: 매일 오전 9시 ─────────────────────────────────────
# PC가 꺼져 있다가 켜지면 StartWhenAvailable에 의해 즉시 실행됨
$trigger = New-ScheduledTaskTrigger -Daily -At "09:00"

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit  (New-TimeSpan -Hours 4) `
    -MultipleInstances   IgnoreNew `
    -StartWhenAvailable

$principal = New-ScheduledTaskPrincipal `
    -UserId    $userName `
    -LogonType Interactive `
    -RunLevel  Highest

Register-ScheduledTask `
    -TaskName    $taskName `
    -Action      $action `
    -Trigger     $trigger `
    -Settings    $settings `
    -Principal   $principal `
    -Description "Claude Code 한도 리셋 후 직전 세션 자동 재개" `
    -Force

Write-Host ""
Write-Host "✔ 등록 완료" -ForegroundColor Green
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State
Write-Host ""
Write-Host "트리거:"
(Get-ScheduledTask -TaskName $taskName).Triggers | Select-Object StartBoundary, DaysInterval
Write-Host ""
Write-Host "시각을 바꾸려면 이 파일의 -At `"09:00`" 부분을 수정 후 재실행하세요."
