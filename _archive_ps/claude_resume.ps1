# Claude Code 자동 재개 스크립트
# 작업 스케줄러에서 한도 리셋 시각에 호출됨
# 사용법: 작업 스케줄러가 이 스크립트를 트리거하거나,
#         직접 실행: .\claude_resume.ps1 [-WorkDir "C:\내프로젝트"] [-Prompt "이어서 계속해줘"]

param(
    [string]$WorkDir = $env:USERPROFILE,   # 작업 디렉터리 (기본값: 홈)
    [string]$Prompt  = "직전 작업을 이어서 완료해줘. 아직 안 끝난 부분이 있으면 끝까지 진행하고, 다 됐으면 완료 요약만 해줘.",
    [string]$ClaudeCLI = ""                 # 비워두면 자동 탐색 (버전 하드코딩 금지)
)

$LogFile = "$PSScriptRoot\claude_resume.log"

function Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    Write-Host $line
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

function Resolve-ClaudeCLI {
    # 1) claude-code 버전 폴더 중 가장 최신(버전 내림차순) exe 탐색
    $baseDir = "$env:APPDATA\Claude\claude-code"
    if (Test-Path $baseDir) {
        $versionDirs = Get-ChildItem -Path $baseDir -Directory -ErrorAction SilentlyContinue |
            Sort-Object {
                $v = $null
                if ([version]::TryParse($_.Name, [ref]$v)) { $v } else { [version]"0.0.0" }
            } -Descending
        foreach ($dir in $versionDirs) {
            $candidate = Join-Path $dir.FullName "claude.exe"
            if (Test-Path $candidate) { return $candidate }
        }
    }
    # 2) PATH 에 등록된 claude 사용
    $onPath = Get-Command claude -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }
    return $null
}

Log "=== Claude 자동 재개 시작 ==="
Log "WorkDir : $WorkDir"
Log "Prompt  : $Prompt"

if ([string]::IsNullOrWhiteSpace($ClaudeCLI)) {
    $ClaudeCLI = Resolve-ClaudeCLI
}

if ([string]::IsNullOrWhiteSpace($ClaudeCLI) -or -not (Test-Path $ClaudeCLI)) {
    Log "ERROR: claude CLI를 찾을 수 없습니다 (자동 탐색 실패): '$ClaudeCLI'"
    exit 1
}
Log "ClaudeCLI: $ClaudeCLI"

if (-not (Test-Path $WorkDir)) {
    Log "ERROR: 작업 디렉터리가 없습니다: $WorkDir"
    exit 1
}

Set-Location $WorkDir
Log "작업 디렉터리로 이동: $WorkDir"

# --continue  : 가장 최근 세션 이어받기
# -p          : 헤드리스(1회 실행 후 종료)
# --output-format stream-json : 결과 스트림 출력(로그 확인용)
Log "claude -p --continue 실행 중..."
& $ClaudeCLI -p $Prompt --continue 2>&1 | ForEach-Object {
    $line = $_
    Log "  [claude] $line"
}

$exit = $LASTEXITCODE
Log "=== 완료 (exit=$exit) ==="
exit $exit
