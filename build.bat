@echo off
REM ============================================================
REM  Auto-Pilot 빌드 스크립트 (Windows)
REM  - 의존성 설치 후 PyInstaller로 단일 exe 생성
REM  - 결과물: dist\auto_pilot.exe
REM ============================================================
setlocal
cd /d "%~dp0"

echo.
echo ============================================
echo  Auto-Pilot 빌드를 시작합니다.
echo ============================================
echo.

REM --- 파이썬 확인 ---
where python >nul 2>nul
if errorlevel 1 (
    echo [오류] python 을 찾을 수 없습니다. Python 3.10+ 설치 후 PATH에 추가하세요.
    pause
    exit /b 1
)

REM --- 의존성 설치 ---
echo [1/3] 의존성을 설치/업데이트합니다...
python -m pip install --upgrade pip
python -m pip install pyinstaller PyQt6 pyautogui pygetwindow pyperclip
if errorlevel 1 (
    echo [오류] 필수 패키지 설치에 실패했습니다.
    pause
    exit /b 1
)

REM --- 선택 의존성 (OCR / 전역 단축키) ---
echo [2/3] 선택 의존성(easyocr, numpy, keyboard)을 설치합니다...
echo       (한도 시간 OCR 판독과 F12 긴급정지에 필요 - 실패해도 빌드는 진행)
python -m pip install easyocr numpy keyboard

REM --- 이전 산출물 정리 ---
if exist build  rmdir /s /q build
if exist dist    rmdir /s /q dist

REM --- 빌드 ---
echo [3/3] PyInstaller 로 빌드합니다...
python -m PyInstaller auto_pilot.spec --noconfirm --clean
if errorlevel 1 (
    echo.
    echo [오류] 빌드에 실패했습니다. 위 로그를 확인하세요.
    pause
    exit /b 1
)

echo.
echo ============================================
echo  빌드 완료!  ^=^>  dist\auto_pilot.exe
echo ============================================
echo.
echo  ※ exe 와 같은 폴더에 다음 파일이 있어야 정상 동작합니다:
echo     - telegram_config.json (텔레그램 사용 시, 앱에서 설정하면 자동 생성)
echo     - steps.json / window_config.json (앱이 자동 생성)
echo  ※ 화면 인식용 PNG와 기본 steps.json 은 exe 안에 번들됩니다.
echo.
pause
endlocal
