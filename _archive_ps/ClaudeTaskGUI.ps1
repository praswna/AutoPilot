# ==============================================================================
# 1. 관리자 권한 확인 및 자동 상승 (작업 스케줄러 제어는 관리자 권한 필수)
# ==============================================================================
# ※ 주의: 파일 저장 시 인코딩을 반드시 [UTF-8(BOM)] 또는 [ANSI]로 지정해야 합니다.

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    # 관리자 권한이 아니라면, 관리자 권한으로 자기 자신을 다시 실행
    try {
        Start-Process powershell.exe -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb RunAs
    } catch {
        Write-Warning "Admin run cancelled."
        Read-Host "Press Enter to exit..."
    }
    exit
}

try {
# ==============================================================================
# 2. GUI 폼(Form) 및 디자인 설정 로드
# ==============================================================================
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# --- 환경 변수 설정 (기존 스크립트와 동일) ---
$taskName   = "ClaudeAutoResume"
$scriptPath = "$env:USERPROFILE\Dropbox\Cluade\AutoPilot\claude_resume.ps1"
$userName   = $env:USERNAME

# 메인 창 생성
$form = New-Object System.Windows.Forms.Form
$form.Text = "Claude 작업 스케줄러 관리기"
$form.Size = New-Object System.Drawing.Size(350, 300)
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false

# 폰트 설정
$font = New-Object System.Drawing.Font("Malgun Gothic", 10)
$form.Font = $font

# --- UI 요소 생성 ---

# 1) 제목 라벨
$lblTitle = New-Object System.Windows.Forms.Label
$lblTitle.Text = "🤖 Claude 자동 재개 스케줄 관리"
$lblTitle.Font = New-Object System.Drawing.Font("Malgun Gothic", 12, [System.Drawing.FontStyle]::Bold)
$lblTitle.AutoSize = $true
$lblTitle.Location = New-Object System.Drawing.Point(35, 20)
$form.Controls.Add($lblTitle)

# 2) 상태 표시 라벨
$lblStatus = New-Object System.Windows.Forms.Label
$lblStatus.Text = "현재 상태: 확인 중..."
$lblStatus.AutoSize = $true
$lblStatus.Location = New-Object System.Drawing.Point(35, 60)
$form.Controls.Add($lblStatus)

# 3) 시간 설정 라벨 & TimePicker
$lblTime = New-Object System.Windows.Forms.Label
$lblTime.Text = "실행 시간:"
$lblTime.AutoSize = $true
$lblTime.Location = New-Object System.Drawing.Point(35, 100)
$form.Controls.Add($lblTime)

$timePicker = New-Object System.Windows.Forms.DateTimePicker
$timePicker.Format = [System.Windows.Forms.DateTimePickerFormat]::Custom
$timePicker.CustomFormat = "HH:mm"
$timePicker.ShowUpDown = $true
$timePicker.Location = New-Object System.Drawing.Point(115, 98)
$timePicker.Size = New-Object System.Drawing.Size(80, 25)
$timePicker.Value = (Get-Date).Date.AddHours(9) # 기본값 오전 9시
$form.Controls.Add($timePicker)

# 4) 등록 버튼
$btnRegister = New-Object System.Windows.Forms.Button
$btnRegister.Text = "스케줄 등록/수정"
$btnRegister.Location = New-Object System.Drawing.Point(35, 150)
$btnRegister.Size = New-Object System.Drawing.Size(130, 40)
$btnRegister.BackColor = [System.Drawing.Color]::LightGreen
$form.Controls.Add($btnRegister)

# 5) 해제 버튼
$btnUnregister = New-Object System.Windows.Forms.Button
$btnUnregister.Text = "스케줄 삭제"
$btnUnregister.Location = New-Object System.Drawing.Point(175, 150)
$btnUnregister.Size = New-Object System.Drawing.Size(120, 40)
$btnUnregister.BackColor = [System.Drawing.Color]::LightPink
$form.Controls.Add($btnUnregister)

# 6) 새로고침 버튼
$btnRefresh = New-Object System.Windows.Forms.Button
$btnRefresh.Text = "상태 새로고침"
$btnRefresh.Location = New-Object System.Drawing.Point(35, 200)
$btnRefresh.Size = New-Object System.Drawing.Size(260, 30)
$form.Controls.Add($btnRefresh)


# ==============================================================================
# 3. 기능 함수 (로직) 정의
# ==============================================================================

# 상태 업데이트 함수
function Update-Status {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($task) {
        $trigger = $task.Triggers[0].StartBoundary
        $state = $task.State
        $lblStatus.Text = "현재 상태: 등록됨 ($state) - $trigger"
        $lblStatus.ForeColor = [System.Drawing.Color]::Blue
        
        # 설정된 시간이 있으면 시간 픽커에 반영
        if ($trigger) {
            try { $timePicker.Value = [datetime]::Parse($trigger) } catch {}
        }
    } else {
        $lblStatus.Text = "현재 상태: 미등록 (작업 없음)"
        $lblStatus.ForeColor = [System.Drawing.Color]::Red
    }
}

# 등록 버튼 클릭 이벤트
$btnRegister.Add_Click({
    $selectedTime = $timePicker.Value.ToString("HH:mm")
    
    try {
        # 기존 작업 제거
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

        # 새 작업 생성
        $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$scriptPath`"" -WorkingDirectory $env:USERPROFILE
        $trigger = New-ScheduledTaskTrigger -Daily -At $selectedTime
        $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 4) -MultipleInstances IgnoreNew -StartWhenAvailable
        $principal = New-ScheduledTaskPrincipal -UserId $userName -LogonType Interactive -RunLevel Highest

        # 등록
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description "Claude Code 한도 리셋 후 직전 세션 자동 재개" -Force | Out-Null
        
        [System.Windows.Forms.MessageBox]::Show("매일 $selectedTime 에 실행되도록 작업이 등록되었습니다.", "성공", [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Information)
        Update-Status
    } catch {
        [System.Windows.Forms.MessageBox]::Show("등록 중 오류가 발생했습니다:`n$($_.Exception.Message)", "오류", [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Error)
    }
})

# 해제 버튼 클릭 이벤트
$btnUnregister.Add_Click({
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($task) {
        try {
            Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
            [System.Windows.Forms.MessageBox]::Show("작업 스케줄이 성공적으로 삭제되었습니다.", "성공", [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Information)
            Update-Status
        } catch {
            [System.Windows.Forms.MessageBox]::Show("삭제 중 오류가 발생했습니다:`n$($_.Exception.Message)", "오류", [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Error)
        }
    } else {
        [System.Windows.Forms.MessageBox]::Show("삭제할 작업이 등록되어 있지 않습니다.", "알림", [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Warning)
    }
})

# 새로고침 버튼 클릭 이벤트
$btnRefresh.Add_Click({
    Update-Status
})


# ==============================================================================
# 4. 프로그램 실행
# ==============================================================================
$form.Add_Load({ Update-Status }) # 창이 열릴 때 최초 1회 상태 업데이트
$form.ShowDialog() | Out-Null

} catch {
    Write-Host "Error: $($_.Exception.Message)" -ForegroundColor Red
    Read-Host "Press Enter to exit..."
}