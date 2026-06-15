# ==========================================
# Auto-Pilot v2.0.0
# ==========================================
VERSION = "2.0.0"

import os
import sys
import time
import datetime
import re
import logging
import logging.handlers
import glob
import json
import tempfile
import threading
import urllib.request
import urllib.parse
import urllib.error
from enum import Enum
from collections import namedtuple

# 전역 단축키 (선택적 의존성 — pip install keyboard)
try:
    import keyboard as _keyboard_lib
    HAS_KEYBOARD = True
except ImportError:
    HAS_KEYBOARD = False

os.environ["QT_LOGGING_RULES"] = "qt.qpa.window=false"

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QCheckBox, QPlainTextEdit, QLabel, QDialog, QSpinBox,
    QFormLayout, QLineEdit, QDialogButtonBox, QGroupBox,
    QMessageBox, QScrollArea, QFrame, QSizePolicy,
)
from PyQt6.QtCore import QThread, pyqtSignal, Qt, QTimer, QObject
from PyQt6.QtGui import QFont, QIcon, QPixmap, QPainter, QColor, QBrush

import pyautogui
import pygetwindow as gw
import pyperclip

try:
    import easyocr
    import numpy as np
    HAS_OCR = True
    ocr_reader = None
except ImportError:
    HAS_OCR = False

# ---------------------------------------------------------
# 1. 설정 및 상수
# ---------------------------------------------------------
APP_NAME = "Auto-Pilot"
TARGET_WINDOW_TITLE = "Claude"
EXCLUDE_TITLE_KEYWORDS = [
    "Visual Studio Code", "Cursor", "Sublime", "Notepad++",
    "메모장", "Notepad", ".py", ".log", ".txt", "탐색기", "Explorer",
]
CHECK_INTERVAL = 5
FALLBACK_WAIT_MINUTES = 30
PAST_TIME_GRACE_MINUTES = 5
MAX_INPUT_LEN = 4000
MAX_CONTINUE_DEFAULT = 3       # 최대 "계속" 횟수 기본값

STABLE_TOKEN = "LOOPSTABLE"   # 클래식 모드 종료 토큰

GENERATING_CONFIDENCE      = 0.80
READY_CONFIDENCE           = 0.80
LIMIT_CONFIDENCE           = 0.90

LOG_MAX_BYTES   = 2 * 1024 * 1024
LOG_BACKUP_COUNT = 3

TELEGRAM_POLL_INTERVAL = 5      # getUpdates 폴링 주기(초)
TELEGRAM_CMD_TTL       = 30     # 명령 유효 시간(초) — 이보다 오래된 명령은 폐기

ScreenBox = namedtuple("ScreenBox", "left top width height")

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
TELEGRAM_CONFIG_PATH = os.path.join(APP_DIR, "telegram_config.json")
STEPS_CONFIG_PATH    = os.path.join(APP_DIR, "steps.json")
WINDOW_CONFIG_PATH   = os.path.join(APP_DIR, "window_config.json")

# 클래식 모드용 스마트 프롬프트 (스텝 목록이 없을 때 사용)
SMART_PROMPT = """[중요 지시사항: 아래 판단 흐름에 따라 현재 상황에 맞는 **단 하나의 STEP만** 실행할 것. 절대 여러 STEP을 한 번에 섞어서 실행하지 마라.]

STEP 1: 미완성 답변 이어쓰기 (우선순위 1)
- 직전 네가 작성하던 코드나 설명이 글자 수 제한 등으로 중간에 끊겼는지 확인해라.
- 끊겼다면 절대 다른 말은 덧붙이지 말고, 끊긴 지점부터 정확히 이어서 작성해라. (이 STEP을 수행하기로 했다면 여기서 즉시 답변을 종료하라)

STEP 2: 코드 치명적 결함 1개 수정 (우선순위 2)
- 직전 답변이 완전히 끝났고, 현재 작성 중인 코드가 있다면 전체 코드에서 다음 3가지 중 해당하는 가장 심각한 문제 **딱 1개**만 찾아 수정해라.
  (1. 예외/에러 처리 누락, 2. 심각한 메모리/성능 비효율, 3. 보안 취약점)
- 억지로 문제를 지어내거나 단순한 스타일 리팩토링은 절대 하지 마라.
- 결함을 수정했다면 거기서 답변을 종료해라 (STEP 3으로 넘어가지 말 것).
- 고칠 치명적 결함이 없다면, 이 STEP을 완전히 건너뛰고 아무 출력 없이 곧바로 STEP 3을 수행해라.

STEP 3: 계획서 갱신 + 새 제안 (우선순위 3)
- STEP 1과 STEP 2에서 할 일이 전혀 없을 때만 이 STEP을 수행해라.
- '계획서' 아티팩트를 찾아라. 없으면 다음 구조로 신규 생성해라:
  목표 / 우선순위 백로그 / 변경 이력.
- (a) 직전 주기까지 STEP 1·2에서 완료된 작업이 있으면 해당 백로그 항목을 ✅로 갱신하고,
  변경 이력에 한 줄 추가해라.
- (b) 다음에 가치 있는 개선 아이디어를 1~5개 도출해 백로그에 `🔵 제안` 상태로 추가해라.
  각 제안에는 고유 번호를 붙여라 (예: `🔵 제안 #7`). 번호는 기존 백로그의 최대 번호 다음부터
  순차로 부여하고, 한 번 쓴 번호는 재사용하지 마라.
  (이미 있는 제안과 내용이 겹치면 중복 금지. 더 제안할 게 없으면 새로 만들지 말 것)
  단, 백로그의 활성 `🔵 제안`이 이미 20개 이상이면 새 제안을 추가하지 말 것.
- 실제 코드 구현은 절대 금지.
- (a)나 (b)에서 실제로 갱신하거나 추가한 게 있으면 아래 형식으로 출력하고 답변을 종료해라.
  둘 다 전혀 변경할 게 없었다면 아무 출력 없이 곧바로 STEP 4를 수행해라.

---
## 💡 새 제안
(이번에 추가한 🔵 제안 항목을 번호와 함께 나열. 없으면 "없음")

## 📋 전체 백로그
(계획서 아티팩트의 우선순위 백로그 표 전체를 그대로 출력)
---

STEP 4: 문서/주석 정비 (우선순위 4)
- STEP 1~3에서 할 일이 전혀 없을 때만 이 STEP을 수행해라.
- 코드 동작은 절대 바꾸지 말고, 코드와 실제로 어긋나거나 빠진 문서를 딱 한 군데만 바로잡아라:
  docstring / 주석 / README 중 하나. (오타·낡은 설명·누락된 설명 보강)
- 무엇을 어디서 고쳤는지 1~2줄로 요약해라.
- 억지로 만들지 말 것. 고칠 문서가 전혀 없다면(= 완전히 안정된 상태):
  먼저 '계획서' 아티팩트의 전체 우선순위 백로그 표를 출력하고,
  그 다음 응답의 맨 마지막 줄에 다른 말 없이 정확히 한 단어 `LOOPSTABLE` 만 단독으로 출력해라.
  (조금이라도 문서를 고쳤다면 이 단어를 절대 출력하지 마라.)

※ 커밋/푸시 (가능할 때만 — 위 STEP과 별개로 적용)
- 이번 주기에 코드·문서 파일을 실제로 수정·생성했고, 지금 작업 환경에서 git을 직접 실행할 수 있다면
  변경분을 한 번의 의미 있는 메시지로 커밋하고, 원격(remote)이 설정돼 있으면 push 해라.
- git을 실행할 수 없는 환경이거나(예: 일반 채팅) 변경한 파일이 없으면 이 작업은 조용히 건너뛰어라.
- 강제 푸시(--force), 히스토리 변경(rebase/amend), 브랜치 삭제는 절대 하지 마라.

※ 공통 금지 사항 (절대 엄수)
- "이 계획을 실행할까요?", "검토해 주세요" 등 사용자의 확인이나 허락을 구하는 질문 절대 금지.
- 이번 턴에 실행할 작업은 오직 네가 스스로 판단해서 진행한다."""

# 스마트 프롬프트의 4개 STEP을 개별 스텝으로 분리한 기본값
DEFAULT_STEPS = [
    # STEP 1 — 미완성 답변 이어쓰기
    """미완성 답변 이어쓰기 (우선순위 1)
- 직전 네가 작성하던 코드나 설명이 글자 수 제한 등으로 중간에 끊겼는지 확인해라.
- 끊겼다면 절대 다른 말은 덧붙이지 말고, 끊긴 지점부터 정확히 이어서 작성해라.
- 이 작업을 수행하기로 했다면 여기서 즉시 답변을 종료해라.""",

    # STEP 2 — 코드 치명적 결함 수정
    """코드 치명적 결함 1개 수정 (우선순위 2)
- 현재 작성 중인 코드가 있다면 전체 코드에서 아래 3가지 중 가장 심각한 문제 딱 1개만 찾아 수정해라.
  (1. 예외/에러 처리 누락  2. 심각한 메모리/성능 비효율  3. 보안 취약점)
- 억지로 문제를 지어내거나 단순한 스타일 리팩토링은 절대 하지 마라.
- 결함을 수정했다면 거기서 답변을 종료해라.
- 고칠 치명적 결함이 없다면 아무 출력 없이 STEP 3으로 넘어가라.""",

    # STEP 3 — 계획서 갱신 + 새 제안
    """계획서 갱신 + 새 제안 (우선순위 3)
- STEP 1·2에서 할 일이 전혀 없을 때만 수행해라.
- '계획서' 아티팩트를 찾아 (없으면 목표/우선순위 백로그/변경 이력 구조로 신규 생성):
  (a) 이번 주기에 완료된 작업을 ✅로 갱신하고 변경 이력에 한 줄 추가해라.
  (b) 다음 개선 아이디어 1~5개를 🔵 제안 상태로 추가해라 (고유 번호, 중복 금지, 활성 제안 20개 이상이면 추가 금지).
- 실제 코드 구현은 절대 금지.
- 갱신·추가한 내용이 있으면 아래 형식으로 출력하고 종료:
  ## 💡 새 제안 / ## 📋 전체 백로그
- 전혀 변경할 게 없으면 아무 출력 없이 STEP 4로 넘어가라.""",

    # STEP 4 — 문서/주석 정비
    """문서/주석 정비 (우선순위 4)
- STEP 1~3에서 할 일이 전혀 없을 때만 수행해라.
- 코드 동작은 절대 바꾸지 말고, 어긋나거나 빠진 문서를 딱 한 군데만 바로잡아라.
  (docstring / 주석 / README 중 하나 — 오타·낡은 설명·누락 보강)
- 무엇을 어디서 고쳤는지 1~2줄로 요약해라.
- 고칠 문서가 전혀 없다면(= 완전히 안정된 상태):
  계획서 전체 백로그 표를 출력하고, 응답 맨 마지막 줄에 정확히 `LOOPSTABLE` 한 단어만 출력해라.
  (조금이라도 문서를 고쳤다면 이 단어를 절대 출력하지 마라.)""",
]

# ---------------------------------------------------------
# 2. 유틸리티 함수
# ---------------------------------------------------------
def resource_path(relative_path: str) -> str:
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)

def app_icon() -> QIcon:
    ico_path = resource_path("icon.ico")
    if os.path.exists(ico_path):
        icon = QIcon(ico_path)
        if not icon.isNull():
            return icon
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QBrush(QColor(43, 187, 131)))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(2, 2, 60, 60)
    painter.end()
    return QIcon(pixmap)

def safe_clipboard_paste() -> str:
    try:
        content = pyperclip.paste()
        return content if isinstance(content, str) else ""
    except Exception as e:
        logging.debug(f"클립보드 읽기 실패: {e}")
        return ""

def save_steps(steps: list[str]) -> bool:
    """스텝 텍스트 목록을 steps.json에 저장한다."""
    try:
        with open(STEPS_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"steps": steps}, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logging.warning(f"스텝 저장 실패: {e}")
        return False

def load_steps() -> list[str] | None:
    """steps.json에서 스텝 텍스트 목록을 복원한다. 없으면 None.
    APP_DIR(실행 파일 옆)을 먼저 보고, 없으면 번들된 기본 steps.json을 사용한다."""
    for path in (STEPS_CONFIG_PATH, resource_path("steps.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            continue
        except Exception as e:
            logging.warning(f"스텝 읽기 실패({path}): {e}")
            continue
        steps = data.get("steps")
        if isinstance(steps, list):
            return [str(s) for s in steps]
    return None

def save_window_geometry(x: int, y: int, w: int, h: int) -> bool:
    """메인 창 위치·크기를 window_config.json에 저장한다."""
    try:
        with open(WINDOW_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"x": x, "y": y, "w": w, "h": h}, f)
        return True
    except Exception as e:
        logging.debug(f"창 위치 저장 실패: {e}")
        return False

def load_window_geometry() -> dict | None:
    """저장된 메인 창 위치·크기를 복원한다. 없으면 None."""
    try:
        with open(WINDOW_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:
        logging.debug(f"창 위치 읽기 실패: {e}")
        return None
    if all(k in data for k in ("x", "y", "w", "h")):
        return data
    return None

# ---------------------------------------------------------
# 3. 텔레그램 헬퍼
# ---------------------------------------------------------
def load_telegram_config() -> dict | None:
    try:
        with open(TELEGRAM_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:
        logging.warning(f"텔레그램 설정 읽기 실패: {e}")
        return None
    if not cfg.get("enabled"):
        return None
    if not cfg.get("bot_token") or not cfg.get("chat_id"):
        logging.warning("텔레그램 설정에 bot_token 또는 chat_id 가 비어 있습니다.")
        return None
    return cfg

def _multipart_encode(fields: dict, file_field: str, filename: str,
                      file_bytes: bytes, content_type: str) -> tuple[bytes, str]:
    boundary = "----AutoPilot" + os.urandom(16).hex()
    out = bytearray()
    for name, value in fields.items():
        out += f"--{boundary}\r\n".encode()
        out += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        out += f"{value}\r\n".encode()
    out += f"--{boundary}\r\n".encode()
    out += (f'Content-Disposition: form-data; name="{file_field}"; '
            f'filename="{filename}"\r\n').encode()
    out += f"Content-Type: {content_type}\r\n\r\n".encode()
    out += file_bytes + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), boundary

def send_telegram_message(bot_token: str, chat_id: str, text: str) -> tuple[bool, str]:
    if not bot_token or not chat_id:
        return False, "bot_token 또는 chat_id 가 비어 있습니다."
    try:
        params = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        req = urllib.request.Request(url, data=params, method="POST")
        with urllib.request.urlopen(req, timeout=20) as resp:
            if 200 <= resp.status < 300:
                return True, "전송 성공"
            return False, f"응답 코드 {resp.status}"
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "ignore")
        except Exception:
            pass
        return False, f"HTTP {e.code}: {detail[:200]}"
    except Exception as e:
        return False, f"오류: {e}"

def send_telegram_text(text: str) -> bool:
    cfg = load_telegram_config()
    if not cfg:
        return False
    ok, msg = send_telegram_message(cfg["bot_token"], cfg["chat_id"], text)
    if not ok:
        logging.warning(f"텔레그램 텍스트 전송 실패: {msg}")
    return ok

def send_telegram_photo(image_path: str, caption: str) -> bool:
    cfg = load_telegram_config()
    if not cfg:
        logging.info("텔레그램 설정이 없거나 비활성화됨 — 알림 전송 건너뜀.")
        return False
    try:
        with open(image_path, "rb") as f:
            data = f.read()
        body, boundary = _multipart_encode(
            {"chat_id": cfg["chat_id"], "caption": caption[:1024]},
            "photo", "backlog.png", data, "image/png",
        )
        url = f"https://api.telegram.org/bot{cfg['bot_token']}/sendPhoto"
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        with urllib.request.urlopen(req, timeout=30) as resp:
            ok = 200 <= resp.status < 300
        if ok:
            logging.info("📨 텔레그램으로 알림(캡처)을 보냈습니다.")
        else:
            logging.warning(f"텔레그램 전송 응답 코드: {resp.status}")
        return ok
    except Exception as e:
        logging.error(f"텔레그램 전송 실패: {e}")
        return False

def telegram_get_updates(bot_token: str, offset: int) -> list:
    try:
        url = (f"https://api.telegram.org/bot{bot_token}/getUpdates"
               f"?offset={offset}&limit=10&timeout=3")
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return data.get("result", [])
    except Exception:
        return []

# ---------------------------------------------------------
# 4. 시간 파싱
# ---------------------------------------------------------
def parse_target_time(text: str) -> datetime.datetime | None:
    if not text:
        return None
    if len(text) > 500:
        keyword_idx = max(text.rfind('재설정'), text.rfind('한도'),
                          text.lower().rfind('limit'), text.lower().rfind('reset'))
        if keyword_idx != -1:
            start = max(0, keyword_idx - 100)
            end   = min(len(text), keyword_idx + 100)
            text  = text[start:end]
        else:
            text = text[-500:]
    text = re.sub(r'[​‌‍‎‏﻿]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    hour, minute = None, None
    is_pm = '오후' in text or 'pm' in text.lower()
    is_am = '오전' in text or 'am' in text.lower()
    match = re.search(r'(\d{1,2})\s*[:\.]\s*(\d{2})', text)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
    else:
        m = re.search(r'(\d{1,2})\s*시\s*(\d{1,2})\s*분', text)
        if m:
            hour, minute = int(m.group(1)), int(m.group(2))
        else:
            m2 = re.search(r'(\d{1,2})\s*시', text)
            if m2:
                hour, minute = int(m2.group(1)), 0
    if hour is None or minute is None:
        return None
    if not (0 <= hour <= 24) or not (0 <= minute <= 59):
        return None
    if is_pm and hour < 12:
        hour += 12
    elif is_am and hour == 12:
        hour = 0
    elif hour == 24:
        hour = 0
    now = datetime.datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target < now:
        if (now - target) <= datetime.timedelta(minutes=PAST_TIME_GRACE_MINUTES):
            target = now
        elif not is_am and not is_pm and hour < 12:
            pm_time = target.replace(hour=hour + 12)
            target = pm_time if pm_time > now else target + datetime.timedelta(days=1)
        else:
            target += datetime.timedelta(days=1)
    return target

# ---------------------------------------------------------
# 5. 로깅 시그널 및 핸들러
# ---------------------------------------------------------
class Signaller(QObject):
    signal = pyqtSignal(str, logging.LogRecord)

class QTextEditLogger(logging.Handler):
    def __init__(self, parent):
        super().__init__()
        self.widget = parent
        self.signaller = Signaller()
        self.signaller.signal.connect(self.widget.append_log)

    def emit(self, record):
        msg = self.format(record)
        self.signaller.signal.emit(msg, record)

# ---------------------------------------------------------
# 6. 토스트 알림
# ---------------------------------------------------------
class ToastNotification(QDialog):
    def __init__(self, message, duration=3000):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        layout = QVBoxLayout()
        label = QLabel(message)
        label.setStyleSheet("""
            QLabel {
                background-color: #2D2D2D; color: #FFFFFF;
                padding: 15px; border-radius: 5px;
                font-family: 'Malgun Gothic', sans-serif;
                font-size: 11pt; font-weight: bold;
            }
        """)
        layout.addWidget(label)
        self.setLayout(layout)
        screen = QApplication.primaryScreen().geometry()
        self.move(screen.width() - 350, screen.height() - 150)
        QTimer.singleShot(duration, self.close)

# ---------------------------------------------------------
# 7. 상태 머신 및 기본 메시지
# ---------------------------------------------------------
class State(Enum):
    IDLE      = "IDLE"
    MONITORING = "MONITORING"
    GENERATING = "GENERATING"
    WAITING   = "WAITING"
    RESUMING  = "RESUMING"
    PAUSED    = "PAUSED"    # 자동 일시 정지 (수동 개입 필요)

DEFAULT_MESSAGES = {
    State.IDLE:       "클로드 앱이 닫혀 있습니다. 창이 켜지기를 대기 중입니다.",
    State.MONITORING: "클로드가 '대기 중(입력 가능)' 상태입니다. (연속 모드: {mode})",
    State.GENERATING: "클로드가 현재 '답변을 작성'하고 있습니다.",
    State.WAITING:    "사용량 한도 초과 상태입니다. 휴식 중 (재개 예정: {time})",
    State.RESUMING:   "클로드에게 다음 작업을 지시(프롬프트 전송)하는 중입니다.",
    State.PAUSED:     "⚠️ 자동 일시 정지 — [강제 다음 스텝] 또는 [재개]를 눌러주세요. ({reason})",
}

# ---------------------------------------------------------
# 8. 텔레그램 설정 다이얼로그
# ---------------------------------------------------------
class TelegramSettingsDialog(QDialog):
    GUIDE = (
        "<b>📨 텔레그램 알림 + 양방향 제어 설정</b><br><br>"
        "정지·완료 시 캡처를 전송하고, 봇 명령으로 원격 제어할 수 있습니다.<br><br>"
        "<b>① 봇 토큰</b>: @BotFather → /newbot 으로 발급<br>"
        "<b>② chat id</b>: 봇에게 아무 메시지 전송 후 @userinfobot 으로 확인<br><br>"
        "<b>명령어 목록</b> (양방향 사용 시):<br>"
        "&nbsp;&nbsp;/status — 현재 상태 조회<br>"
        "&nbsp;&nbsp;/screen — 현재 화면 캡처 전송<br>"
        "&nbsp;&nbsp;/send [메시지] — 클로드에 직접 메시지 전송<br>"
        "&nbsp;&nbsp;/pause — 연속 모드 중단<br>"
        "&nbsp;&nbsp;/resume — 연속 모드 재개<br>"
        "&nbsp;&nbsp;/next — 강제 다음 스텝<br>"
        "&nbsp;&nbsp;/stop — 감시 중지<br>"
        "&nbsp;&nbsp;/help — 명령어 목록 표시<br><br>"
        "<i>※ chat id 가 일치하는 본인 대화만 명령을 처리하며,<br>"
        "&nbsp;&nbsp;&nbsp;전송된 지 30초가 지난 명령은 자동 폐기됩니다.<br>"
        "&nbsp;&nbsp;&nbsp;봇 토큰은 외부에 공유하지 마세요.</i>"
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("텔레그램 설정")
        self.resize(520, 600)
        layout = QVBoxLayout(self)

        guide = QLabel(self.GUIDE)
        guide.setWordWrap(True)
        guide.setTextFormat(Qt.TextFormat.RichText)
        guide.setStyleSheet("font-size: 10pt; padding: 4px;")
        layout.addWidget(guide)

        form = QFormLayout()
        self.cb_enabled = QCheckBox("알림 사용 (정지·완료 시 캡처 전송)")
        form.addRow(self.cb_enabled)

        self.cb_bidirectional = QCheckBox("양방향 제어 사용 (봇 명령 수신)")
        form.addRow(self.cb_bidirectional)

        self.edit_token = QLineEdit()
        self.edit_token.setPlaceholderText("123456789:ABCd...")
        self.edit_token.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("봇 토큰:", self.edit_token)

        self.edit_chat = QLineEdit()
        self.edit_chat.setPlaceholderText("987654321")
        form.addRow("내 chat id:", self.edit_chat)
        layout.addLayout(form)

        self.cb_show_token = QCheckBox("토큰 표시")
        self.cb_show_token.stateChanged.connect(self._toggle_token_echo)
        layout.addWidget(self.cb_show_token)

        self.lbl_result = QLabel("")
        self.lbl_result.setWordWrap(True)
        layout.addWidget(self.lbl_result)

        btn_row = QHBoxLayout()
        self.btn_test = QPushButton("✈️ 테스트 전송")
        self.btn_test.clicked.connect(self._on_test)
        btn_row.addWidget(self.btn_test)
        self.btn_save = QPushButton("💾 저장")
        self.btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(self.btn_save)
        self.btn_close = QPushButton("닫기")
        self.btn_close.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_close)
        layout.addLayout(btn_row)

        self._load_existing()

    def _toggle_token_echo(self, _):
        self.edit_token.setEchoMode(
            QLineEdit.EchoMode.Normal if self.cb_show_token.isChecked()
            else QLineEdit.EchoMode.Password
        )

    def _load_existing(self):
        try:
            with open(TELEGRAM_CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            self.cb_enabled.setChecked(bool(cfg.get("enabled")))
            self.cb_bidirectional.setChecked(bool(cfg.get("bidirectional")))
            self.edit_token.setText(str(cfg.get("bot_token", "")))
            self.edit_chat.setText(str(cfg.get("chat_id", "")))
        except Exception:
            pass

    def _on_test(self):
        ok, msg = send_telegram_message(
            self.edit_token.text().strip(), self.edit_chat.text().strip(),
            "[Auto-Pilot] 테스트 메시지입니다. 이 메시지가 보이면 설정 성공!",
        )
        self.lbl_result.setText(("✅ " if ok else "❌ ") + msg)
        self.lbl_result.setStyleSheet(f"color: {'#2ecc71' if ok else '#e74c3c'}; padding: 2px;")

    def _on_save(self):
        cfg = {
            "enabled":       self.cb_enabled.isChecked(),
            "bidirectional": self.cb_bidirectional.isChecked(),
            "bot_token":     self.edit_token.text().strip(),
            "chat_id":       self.edit_chat.text().strip(),
        }
        try:
            with open(TELEGRAM_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            self.lbl_result.setText(f"✅ 저장됨: {TELEGRAM_CONFIG_PATH}")
            self.lbl_result.setStyleSheet("color: #2ecc71; padding: 2px;")
        except Exception as e:
            self.lbl_result.setText(f"❌ 저장 실패: {e}")
            self.lbl_result.setStyleSheet("color: #e74c3c; padding: 2px;")

# ---------------------------------------------------------
# 9. 텔레그램 양방향 폴러 스레드
# ---------------------------------------------------------
class TelegramPollerThread(QThread):
    """Telegram getUpdates를 5초마다 폴링해 봇 명령을 수신한다."""
    command_received = pyqtSignal(str)   # "/status", "/stop", ...

    def __init__(self, bot_token: str, chat_id: str):
        super().__init__()
        self.bot_token = bot_token
        self.chat_id   = chat_id
        self.running   = True
        self._last_update_id = 0

    def run(self):
        while self.running:
            try:
                updates = telegram_get_updates(self.bot_token, self._last_update_id + 1)
                for upd in updates:
                    uid = upd.get("update_id", 0)
                    msg  = upd.get("message", {})
                    text = msg.get("text", "").strip()
                    chat_id = str(msg.get("chat", {}).get("id", ""))
                    # chat_id 필터(보안) + 만료 필터(오래된 명령 폭주 방지)
                    if text.startswith("/") and chat_id == self.chat_id:
                        date = msg.get("date")
                        # date 누락 시(비정상 페이로드)엔 만료 검사를 건너뛰고 처리한다.
                        age  = (time.time() - date) if date else 0
                        if age <= TELEGRAM_CMD_TTL:
                            # 전체 메시지를 전달 — /send 등 인자 있는 명령을 위해
                            self.command_received.emit(text)
                        else:
                            logging.info(f"⏰ 만료된 텔레그램 명령 무시 ({int(age)}초 경과): {text.split()[0]}")
                    if uid > self._last_update_id:
                        self._last_update_id = uid
            except Exception as e:
                logging.debug(f"텔레그램 폴링 오류: {e}")
            self._sleep(TELEGRAM_POLL_INTERVAL)

    def stop(self):
        self.running = False

    def _sleep(self, seconds: float):
        end = time.time() + seconds
        while self.running and time.time() < end:
            time.sleep(0.1)

# ---------------------------------------------------------
# 10. 환경 설정 다이얼로그
# ---------------------------------------------------------
class MessageSettingsDialog(QDialog):
    def __init__(self, current_prompt, parent=None):
        super().__init__(parent)
        self.setWindowTitle("환경 설정")
        self.resize(560, 460)
        layout = QVBoxLayout(self)

        # 클래식 모드 스마트 프롬프트
        prompt_group = QGroupBox("클래식 모드 스마트 프롬프트 (스텝 없을 때)")
        pl = QVBoxLayout()
        self.prompt_edit = QPlainTextEdit(current_prompt)
        pl.addWidget(self.prompt_edit)
        prompt_group.setLayout(pl)
        layout.addWidget(prompt_group)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        layout.addWidget(bb)

    def get_values(self):
        return self.prompt_edit.toPlainText()

# ---------------------------------------------------------
# 11. 스텝 아이템 위젯
# ---------------------------------------------------------
class StepItemWidget(QFrame):
    delete_requested = pyqtSignal(object)

    def __init__(self, index: int, text: str = "", parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._index = index

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)

        self.lbl_num = QLabel(f"#{index}")
        self.lbl_num.setFixedWidth(28)
        self.lbl_num.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_num.setStyleSheet("font-weight: bold; color: #8be9fd;")
        layout.addWidget(self.lbl_num)

        self.text_edit = QPlainTextEdit(text)
        self.text_edit.setFixedHeight(75)
        self.text_edit.setPlaceholderText(f"Step {index} 프롬프트를 입력하세요...")
        self.text_edit.setStyleSheet(
            "background-color: #252526; color: #d4d4d4; border: 1px solid #3c3c3c;"
            "border-radius: 3px; font-family: Consolas, monospace; font-size: 9pt;"
        )
        layout.addWidget(self.text_edit)

        self.btn_del = QPushButton("✕")
        self.btn_del.setFixedSize(26, 26)
        self.btn_del.setStyleSheet(
            "QPushButton { background-color: #c0392b; color: white; border-radius: 4px; font-weight: bold; }"
            "QPushButton:hover { background-color: #e74c3c; }"
        )
        self.btn_del.clicked.connect(lambda: self.delete_requested.emit(self))
        layout.addWidget(self.btn_del)

    def get_text(self) -> str:
        return self.text_edit.toPlainText()

    def set_index(self, index: int):
        self._index = index
        self.lbl_num.setText(f"#{index}")

    def set_locked(self, locked: bool):
        self.text_edit.setReadOnly(locked)
        self.btn_del.setEnabled(not locked)

    def set_active(self, active: bool, done: bool = False):
        if done:
            self.setStyleSheet("QFrame { border: 2px solid #555; border-radius: 4px; }")
            self.lbl_num.setStyleSheet("font-weight: bold; color: #555; text-decoration: line-through;")
        elif active:
            self.setStyleSheet("QFrame { border: 2px solid #2ecc71; border-radius: 4px; background: rgba(46,204,113,0.05); }")
            self.lbl_num.setStyleSheet("font-weight: bold; color: #2ecc71;")
        else:
            self.setStyleSheet("")
            self.lbl_num.setStyleSheet("font-weight: bold; color: #8be9fd;")

# ---------------------------------------------------------
# 12. 스텝 관리 다이얼로그
# ---------------------------------------------------------
class StepManagerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("📋 스텝 관리")
        self.resize(500, 480)
        self._step_items: list[StepItemWidget] = []

        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        layout.setContentsMargins(10, 10, 10, 10)

        # 스크롤 영역
        self._step_scroll = QScrollArea()
        self._step_scroll.setWidgetResizable(True)
        self._step_scroll.setStyleSheet(
            "QScrollArea { border:1px solid #3c3c3c; background:#1a1a1a; }"
        )
        self._step_container = QWidget()
        self._step_layout    = QVBoxLayout(self._step_container)
        self._step_layout.setSpacing(4)
        self._step_layout.addStretch()
        self._step_scroll.setWidget(self._step_container)
        layout.addWidget(self._step_scroll, stretch=1)

        # 하단 컨트롤
        step_ctrl = QHBoxLayout()
        self.btn_add_step = QPushButton("+ 스텝 추가")
        self.btn_add_step.setStyleSheet(
            "QPushButton { background:#27ae60; color:white; font-weight:bold;"
            " padding:4px 14px; border-radius:4px; }"
            "QPushButton:hover { background:#2ecc71; }"
        )
        self.btn_add_step.clicked.connect(self.add_step)
        step_ctrl.addWidget(self.btn_add_step)
        step_ctrl.addStretch()
        layout.addLayout(step_ctrl)

    def closeEvent(self, event):
        # 창을 닫을 때(숨길 때) 현재 스텝을 저장해 다음 실행에 복원한다.
        save_steps([item.get_text() for item in self._step_items])
        self.hide()
        event.ignore()

    def add_step(self, _checked=False, text: str = ""):
        idx  = len(self._step_items) + 1
        item = StepItemWidget(idx, text, self._step_container)
        item.delete_requested.connect(self._remove_step)
        count = self._step_layout.count()
        self._step_layout.insertWidget(count - 1, item)
        self._step_items.append(item)
        self._update_step_labels()

    def _remove_step(self, item: StepItemWidget):
        if item in self._step_items:
            self._step_items.remove(item)
            self._step_layout.removeWidget(item)
            item.deleteLater()
            self._update_step_labels()

    def _update_step_labels(self):
        for i, item in enumerate(self._step_items, start=1):
            item.set_index(i)

    def get_steps(self) -> list[str]:
        return [item.get_text().strip()
                for item in self._step_items
                if item.get_text().strip()]

    def set_locked(self, locked: bool):
        self.btn_add_step.setEnabled(not locked)
        for item in self._step_items:
            item.set_locked(locked)

    def set_step_active(self, current: int, total: int):
        for i, item in enumerate(self._step_items, start=1):
            item.set_active(i == current, done=(i < current))


# ---------------------------------------------------------
# 13. 오토파일럿 워커 스레드
# ---------------------------------------------------------
class ClaudeWorker(QThread):
    toast_signal             = pyqtSignal(str)
    continuous_mode_changed  = pyqtSignal(bool)
    step_progress_signal     = pyqtSignal(int, int)   # (current 1-based, total)
    auto_paused_signal       = pyqtSignal(str)         # pause reason
    all_steps_done_signal    = pyqtSignal()            # 모든 스텝 1회 완료 → 자동 정지

    def __init__(self):
        super().__init__()
        self.state           = State.IDLE
        self.running         = True
        self.target_time     = None
        self.continuous_mode = False
        self.click_y_offset  = 110

        # 신뢰도
        self.generating_confidence = GENERATING_CONFIDENCE
        self.ready_confidence      = READY_CONFIDENCE
        self.limit_confidence      = LIMIT_CONFIDENCE

        # 스텝 모드
        self.steps          : list[str] = []
        self.expected_step  : int = 1    # 1-based
        self.continue_count : int = 0
        self.max_continue   : int = MAX_CONTINUE_DEFAULT
        self.loop_forever   : bool = False   # 모든 스텝 완료 후 Step 1로 무한 반복
        self._pause_reason  : str = ""
        self._step_lock     = threading.Lock()

        # 원격 /send — 텔레그램에서 받은 사용자 메시지 큐 (워커 스레드에서 전송)
        self._pending_sends : list[str] = []
        self._send_lock     = threading.Lock()

        self.last_status_msg  = ""
        self.status_messages  = DEFAULT_MESSAGES.copy()
        self.smart_prompt     = SMART_PROMPT

    # ── 메인 루프 ──────────────────────────────────────────
    def run(self):
        self.running = True
        self.state   = State.IDLE
        self.last_status_msg = ""
        logging.info("=========================================")
        logging.info(f"{APP_NAME} v{VERSION} 감시 스레드를 시작합니다.")
        self._check_resources()
        if self.steps:
            logging.info(f"📋 스텝 모드: 총 {len(self.steps)}개 스텝, Step {self.expected_step}부터 시작.")
        else:
            logging.info("📋 클래식 모드 (스마트 프롬프트).")
        logging.info("=========================================")
        while self.running:
            try:
                self._flush_pending_sends()   # 원격 /send 우선 처리
                self._update_state()
            except pyautogui.FailSafeException:
                logging.error("FailSafe 발동 — 루프를 재개합니다.")
            except Exception as e:
                logging.error(f"루프 실행 중 에러: {e}")
            self._interruptible_sleep(CHECK_INTERVAL)

    def stop(self):
        self.running = False

    def queue_message(self, text: str):
        """텔레그램 /send 로 받은 메시지를 전송 큐에 넣는다 (GUI 스레드에서 호출)."""
        text = (text or "").strip()
        if not text:
            return
        with self._send_lock:
            self._pending_sends.append(text)

    def _flush_pending_sends(self):
        """큐에 쌓인 사용자 메시지를 워커 스레드에서 차례로 클로드에 붙여넣어 전송한다."""
        with self._send_lock:
            if not self._pending_sends:
                return
            pending = self._pending_sends
            self._pending_sends = []
        for text in pending:
            if not self.running:
                return
            self._send_text_to_claude(text)

    def _send_text_to_claude(self, text: str):
        win = self._focus_claude_window()
        if not win:
            logging.error("클로드 창을 찾을 수 없어 /send 전송을 취소합니다.")
            return
        original_clipboard = safe_clipboard_paste()
        try:
            center_x = win.left + (win.width // 2)
            bottom_y = win.bottom - self.click_y_offset
            pyautogui.click(x=center_x, y=bottom_y)
            time.sleep(0.5)
            pyautogui.hotkey('ctrl', 'a')   # 기존 입력창 내용 정리
            time.sleep(0.2)
            pyperclip.copy(text)
            time.sleep(0.2)
            pyautogui.hotkey('ctrl', 'v')
            time.sleep(0.5)
            pyautogui.press('enter')
            time.sleep(1)
            logging.info(f"📨 /send 사용자 메시지를 전송했습니다: {text[:60]}")
        except Exception as e:
            logging.error(f"/send 전송 실패: {e}")
        finally:
            try:
                pyperclip.copy(original_clipboard if original_clipboard else "")
            except Exception as e:
                logging.debug(f"클립보드 복구 실패: {e}")

    def force_next_step(self):
        """수동으로 다음 스텝으로 강제 이동 (GUI 슬롯에서 호출)."""
        if not self.steps:
            return
        with self._step_lock:
            self.continue_count = 0
            self.expected_step  = min(self.expected_step + 1, len(self.steps) + 1)
        logging.info(f"⏭ 강제 다음 스텝 → Step {self.expected_step}/{len(self.steps)}")
        if self.state == State.PAUSED:
            if self.expected_step > len(self.steps):
                self.continuous_mode = False
                self.continuous_mode_changed.emit(False)
                self.state = State.MONITORING
            else:
                self.state = State.RESUMING
        self.step_progress_signal.emit(self.expected_step, len(self.steps))

    # ── 상태 갱신 ──────────────────────────────────────────
    def _update_state(self):
        claude_win    = self.get_claude_window()
        is_claude_open = claude_win is not None

        frame = (self._grab_window_frame(claude_win)
                 if is_claude_open and self.state not in (State.WAITING, State.PAUSED)
                 else None)

        if is_claude_open and self.state not in (State.WAITING, State.PAUSED):
            if self._check_for_rate_limit(claude_win, frame):
                self._log_status_change()
                return

        is_generating  = False
        done_confirmed = True
        if is_claude_open and self.state in (State.MONITORING, State.GENERATING):
            is_generating = self._check_is_generating(claude_win, frame)
            if not is_generating and self._has_ready_templates():
                done_confirmed = self._check_is_ready(claude_win, frame)

        self._log_status_change()

        if self.state == State.IDLE:
            if is_claude_open:
                logging.info(f"[{TARGET_WINDOW_TITLE}] 창이 감지되었습니다. 감시를 시작합니다.")
                self.state = State.MONITORING

        elif self.state in (State.MONITORING, State.GENERATING):
            if not is_claude_open:
                logging.info("클로드 창이 닫혔습니다. 대기 모드로 전환합니다.")
                self.state = State.IDLE
                return
            if is_generating:
                if self.state != State.GENERATING:
                    self.state = State.GENERATING
            elif not done_confirmed:
                pass  # 모호한 상태 — 다음 주기까지 유지
            else:
                if self.state == State.GENERATING:
                    logging.info("답변 작성이 완료되었습니다! 5초 대기 후 다음 단계를 진행합니다.")
                    self._interruptible_sleep(5)
                    self._handle_generation_done(claude_win, frame)
                elif self.state == State.MONITORING and self.continuous_mode:
                    logging.info("연속 모드 ON: 즉시 다음 작업을 지시합니다.")
                    self.state = State.RESUMING

        elif self.state == State.PAUSED:
            if not is_claude_open:
                self.state = State.IDLE
            # 사용자 수동 개입 대기 — 자동으로 상태 전환하지 않음

        elif self.state == State.WAITING:
            if not is_claude_open:
                logging.info("대기 중 클로드 창이 닫혔습니다.")
                self.target_time = None
                self.state = State.IDLE
                return
            if not self.target_time:
                self.state = State.MONITORING
                return
            limit_pos, _ = self._locate_rate_limit(claude_win)
            if limit_pos is None:
                logging.info("한도 화면이 사라졌습니다(리셋). 즉시 재개합니다.")
                self.target_time = None
                self.state = State.RESUMING
                return
            if datetime.datetime.now() >= self.target_time:
                logging.info("대기 시간이 종료되었습니다. 입력창 클릭 후 한도 해제 여부를 확인합니다.")
                try:
                    center_x = claude_win.left + (claude_win.width // 2)
                    bottom_y  = claude_win.bottom - self.click_y_offset
                    pyautogui.click(x=center_x, y=bottom_y)
                    logging.info("입력창 클릭 완료. 3초 대기 후 한도 화면 재확인합니다.")
                except Exception as e:
                    logging.warning(f"입력창 클릭 실패: {e}")
                self._interruptible_sleep(3)
                if not self.running:
                    return
                fresh = self._grab_window_frame(claude_win)
                still_limited, _ = self._locate_rate_limit(claude_win, fresh)
                if still_limited is None:
                    logging.info("한도 화면이 사라졌습니다. 즉시 재개합니다.")
                    self.target_time = None
                    self.state = State.RESUMING
                else:
                    logging.info("한도 화면이 여전히 표시 중입니다. 대기를 계속합니다.")
                    self._setup_wait_timer(claude_win, still_limited)

        elif self.state == State.RESUMING:
            self._execute_smart_resume()
            self.target_time = None
            # 전송 직후 GENERATING으로 전환: Claude가 빠르게 응답해도
            # 다음 사이클에서 ready 감지 → _handle_generation_done → 스텝 완료 체크
            self.state = State.GENERATING

    def _complete_step_cycle(self, claude_win):
        """모든 스텝을 한 바퀴 완료했을 때 호출.
        무한 반복 모드면 Step 1로 루프백해 계속 순환하고,
        아니면 스크린샷 전송 후 감시를 종료한다."""
        if self.loop_forever and self.continuous_mode:
            # 무한 반복 — 매 사이클 알림은 스팸이므로 로그만 남기고 Step 1로 루프백
            logging.info("🔁 모든 스텝 완료 — 무한 반복 모드: Step 1부터 다시 시작합니다.")
            with self._step_lock:
                self.expected_step  = 1
                self.continue_count = 0
            self.step_progress_signal.emit(1, len(self.steps))
            self.state = State.RESUMING
            return
        logging.info("🎉 모든 스텝이 완료되었습니다! 스크린샷을 전송하고 감시를 종료합니다.")
        # 완료 알림(스크린샷) 전송
        self._notify_all_done(claude_win)
        # 한 바퀴 완료 → 연속 모드 해제 후 워커를 정지시킨다.
        self.continuous_mode = False
        self.continuous_mode_changed.emit(False)
        self.running = False          # run 루프 종료 → 스레드 자연 종료
        self.all_steps_done_signal.emit()   # GUI가 버튼/잠금 상태를 정리

    def _handle_generation_done(self, claude_win, frame):
        """답변 완료 후 스텝 모드 / 클래식 모드로 분기.
        스텝 목록이 있으면 continuous_mode와 무관하게 항상 스텝 모드로 동작한다.
        자동 전진(RESUMING)은 continuous_mode가 True일 때만 수행한다."""
        if self.steps:
            # expected_step이 총 스텝 수를 초과한 경우 한 사이클 완료 → 루프백
            if self.expected_step > len(self.steps):
                self._complete_step_cycle(claude_win)
                return
            step_done = self._check_step_complete(claude_win, frame)
            if step_done:
                logging.info(f"✅ Step {self.expected_step}/{len(self.steps)} 완료 확인!")
                with self._step_lock:
                    self.continue_count = 0
                    self.expected_step += 1
                self.step_progress_signal.emit(self.expected_step - 1, len(self.steps))
                if self.expected_step > len(self.steps):
                    self._complete_step_cycle(claude_win)
                elif self.continuous_mode:
                    self.state = State.RESUMING
                else:
                    self.state = State.MONITORING
            else:
                with self._step_lock:
                    self.continue_count += 1
                logging.info(f"Step {self.expected_step} 완료 미감지 ({self.continue_count}/{self.max_continue})")
                if self.continue_count >= self.max_continue:
                    self._pause_reason = (
                        f"Step {self.expected_step} 완료 토큰이 {self.continue_count}회 연속 미감지"
                    )
                    logging.warning(f"⚠️ 자동 일시 정지 — {self._pause_reason}")
                    self.state = State.PAUSED
                    self.auto_paused_signal.emit(self._pause_reason)
                elif self.continuous_mode:
                    self.state = State.RESUMING  # "계속 이어서 작성해 줘" 전송
                else:
                    self.state = State.MONITORING

        elif self.continuous_mode:
            # 클래식 모드
            if self._check_loop_stable(claude_win):
                logging.info("🛑 LOOPSTABLE 감지 — 연속 모드를 끄고 대기합니다.")
                self.continuous_mode = False
                self.continuous_mode_changed.emit(False)
                self._notify_stable(claude_win)
                self.state = State.MONITORING
            else:
                self.state = State.RESUMING
        else:
            self.state = State.MONITORING

    # ── 로깅 ───────────────────────────────────────────────
    def _log_status_change(self):
        s = self.state
        m = self.status_messages
        if s == State.IDLE:
            cur = m[State.IDLE]
        elif s == State.MONITORING:
            cur = m[State.MONITORING].replace("{mode}", "ON" if self.continuous_mode else "OFF")
        elif s == State.GENERATING:
            cur = m[State.GENERATING]
        elif s == State.WAITING:
            t = self.target_time.strftime('%H:%M:%S') if self.target_time else '미정'
            cur = m[State.WAITING].replace("{time}", t)
        elif s == State.RESUMING:
            cur = m[State.RESUMING]
        elif s == State.PAUSED:
            cur = m[State.PAUSED].replace("{reason}", self._pause_reason)
        else:
            cur = str(s)
        if cur != self.last_status_msg:
            logging.info(f"💡 [상태 변경] {cur}")
            self.last_status_msg = cur

    # ── 리소스 확인 ────────────────────────────────────────
    def _check_resources(self):
        missing = []
        if not os.path.exists(resource_path("generating.png")):
            missing.append("generating.png")
        if not glob.glob(resource_path("limit_warning*.png")):
            missing.append("limit_warning*.png")
        if missing:
            logging.warning(f"⚠️ 이미지 파일 누락: {', '.join(missing)}")

    # ── 유틸 ───────────────────────────────────────────────
    def _interruptible_sleep(self, seconds: float):
        end = time.time() + seconds
        while self.running and time.time() < end:
            time.sleep(0.1)

    def get_claude_window(self):
        if sys.platform != 'win32':
            return None
        candidates = []
        try:
            for w in gw.getWindowsWithTitle(TARGET_WINDOW_TITLE):
                title = w.title or ""
                if title == APP_NAME:
                    continue
                if any(bad in title for bad in EXCLUDE_TITLE_KEYWORDS):
                    continue
                candidates.append(w)
        except Exception as e:
            logging.debug(f"창 검색 오류: {e}")
        for w in candidates:
            if w.title == TARGET_WINDOW_TITLE:
                return w
        return candidates[0] if candidates else None

    @staticmethod
    def _window_region(win):
        if not win:
            return None
        try:
            return (max(0, win.left), max(0, win.top),
                    max(1, win.width), max(1, win.height))
        except Exception:
            return None

    def _grab_window_frame(self, win):
        region = self._window_region(win)
        try:
            if region:
                left, top, _w, _h = region
                return (pyautogui.screenshot(region=region), left, top)
            return (pyautogui.screenshot(), 0, 0)
        except Exception as e:
            logging.debug(f"주기 화면 캡처 실패(폴백): {e}")
            return None

    def _locate(self, img_path, win, confidence, use_grayscale=False, frame=None):
        try:
            if frame is not None:
                shot, ox, oy = frame
                box = pyautogui.locate(img_path, shot,
                                       confidence=confidence, grayscale=use_grayscale)
                if box is None:
                    return None
                return ScreenBox(int(box.left) + ox, int(box.top) + oy,
                                 int(box.width), int(box.height))
            region = self._window_region(win)
            if region:
                return pyautogui.locateOnScreen(img_path, confidence=confidence,
                                                region=region, grayscale=use_grayscale)
            return pyautogui.locateOnScreen(img_path, confidence=confidence,
                                            grayscale=use_grayscale)
        except Exception:
            return None

    def _locate_rate_limit(self, claude_win, frame=None):
        for img_path in glob.glob(resource_path("limit_warning*.png")):
            pos = self._locate(img_path, claude_win,
                               confidence=self.limit_confidence,
                               use_grayscale=False, frame=frame)
            if pos:
                return pos, os.path.basename(img_path)
        return None, None

    def _check_for_rate_limit(self, claude_win, frame=None):
        pos, img_name = self._locate_rate_limit(claude_win, frame)
        if pos:
            logging.info(f"사용량 한도 도달! ({img_name}) OCR로 시간을 판독합니다.")
            self._setup_wait_timer(claude_win, pos)
            return True
        return False

    def _match_any(self, prefix: str, claude_win, base_conf: float, frame=None,
                   verbose: bool = False) -> bool:
        ladder = (
            (True,  base_conf),
            (True,  max(0.40, round(base_conf - 0.10, 2))),
            (False, max(0.40, round(base_conf - 0.08, 2))),
        )
        for img_path in glob.glob(resource_path(prefix + "*.png")):
            for use_gray, conf in ladder:
                match = self._locate(img_path, claude_win, conf,
                                     use_grayscale=use_gray, frame=frame)
                if match is not None:
                    if verbose:
                        logging.info(f"✅ 이미지 매칭 성공: {os.path.basename(img_path)} (신뢰도={conf}, gray={use_gray})")
                    return True
        if verbose:
            logging.info(f"❌ 이미지 매칭 실패: {prefix}*.png (신뢰도={base_conf}~{max(0.40, round(base_conf-0.10,2))})")
        return False

    def _check_is_generating(self, claude_win, frame=None):
        return self._match_any("generating", claude_win, self.generating_confidence, frame)

    def _check_is_ready(self, claude_win, frame=None):
        return self._match_any("ready", claude_win, self.ready_confidence, frame)

    @staticmethod
    def _has_ready_templates() -> bool:
        return bool(glob.glob(resource_path("ready*.png")))

    # ── OCR ────────────────────────────────────────────────
    @staticmethod
    def _ensure_ocr() -> bool:
        global HAS_OCR, ocr_reader
        if not HAS_OCR:
            return False
        if ocr_reader is None:
            try:
                logging.info("OCR 엔진을 초기화하는 중입니다... (최초 1회)")
                ocr_reader = easyocr.Reader(['ko', 'en'])
            except Exception as e:
                logging.error(f"OCR 엔진 초기화 실패: {e}")
                HAS_OCR = False
                return False
        return True

    def _check_loop_stable(self, claude_win) -> bool:
        if claude_win is None or not self._ensure_ocr():
            return False
        try:
            band_h = 420
            bottom = int(claude_win.bottom - self.click_y_offset)
            top    = int(max(0, bottom - band_h))
            left   = int(max(0, claude_win.left + 20))
            width  = int(max(1, claude_win.width - 40))
            height = int(max(1, bottom - top))
            shot   = pyautogui.screenshot(region=(left, top, width, height))
            results = ocr_reader.readtext(np.array(shot))
            raw = " ".join(t for _, t, _ in results)
            normalized = re.sub(r'[^A-Z]', '',
                                 "".join(raw.split()).upper().replace("0", "O"))
            found = STABLE_TOKEN in normalized
            logging.info(f"🔍 [안정 OCR] '{raw.strip()[:120]}' → {'감지' if found else '미감지'}")
            return found
        except Exception as e:
            logging.debug(f"안정 토큰 OCR 실패: {e}")
            return False

    # ── 스텝 모드 핵심 ─────────────────────────────────────
    def _check_step_complete(self, claude_win, frame=None) -> bool:
        """현재 expected_step의 완료를 step{N}_complete*.png 이미지 매칭으로만 감지한다."""
        n      = self.expected_step
        prefix = f"step{n}_complete"
        templates = glob.glob(resource_path(f"{prefix}*.png"))
        if not templates:
            logging.warning(f"⚠️ {prefix}*.png 템플릿이 없어 Step {n} 완료를 감지할 수 없습니다.")
            return False
        logging.info(f"🔍 Step {n} 완료 이미지 매칭 시도: {[os.path.basename(t) for t in templates]}")
        result = self._match_any(prefix, claude_win, self.ready_confidence, frame, verbose=True)
        if not result:
            logging.info(f"⏳ Step {n} 완료 미감지 — 신뢰도 {self.ready_confidence} 기준")
        return result

    def _build_step_prompt(self, step_idx: int) -> str:
        """스텝 프롬프트 끝에 완료 표식 지시어를 자동 합성한다 (step_idx: 0-based).
        모델이 출력한 [STEP{N}_DONE] 텍스트를 캡처해 step{N}_complete.png 로 두면
        이미지 매칭으로 완료를 감지한다."""
        n    = step_idx + 1
        base = self.steps[step_idx].strip()
        suffix = (
            f"\n\n---\n"
            f"[필수] 위 내용을 완전히 완료한 뒤, 응답의 **맨 마지막 줄**에 아래 토큰만 "
            f"단독으로 출력하라. 다른 텍스트 없이 정확히 이 형태여야 한다.\n"
            f"[STEP{n}_DONE]"
        )
        return base + suffix

    # ── 알림 ───────────────────────────────────────────────
    def _notify_stable(self, claude_win):
        if not load_telegram_config():
            return
        try:
            region  = self._window_region(claude_win)
            shot    = pyautogui.screenshot(region=region) if region else pyautogui.screenshot()
            tmp     = os.path.join(tempfile.gettempdir(), "autopilot_stable.png")
            shot.save(tmp)
            caption = f"[Auto-Pilot] 계획 안정 — 정지됨 ({datetime.datetime.now():%Y-%m-%d %H:%M})"
            send_telegram_photo(tmp, caption)
        except Exception as e:
            logging.error(f"정지 알림 처리 실패: {e}")

    def _notify_all_done(self, claude_win):
        """모든 스텝 완료 시 텔레그램 알림."""
        self._notify_stable(claude_win)   # 같은 캡처+텍스트 패턴 재사용
        if load_telegram_config():
            logging.info("📨 모든 스텝 완료 알림을 전송했습니다.")

    # ── 한도 대기 타이머 ────────────────────────────────────
    def _setup_wait_timer(self, claude_win, limit_pos):
        if not self._ensure_ocr():
            logging.error("easyocr 없음 — 안전 대기 시간 적용 (pip install easyocr numpy)")
            self.target_time = datetime.datetime.now() + datetime.timedelta(minutes=FALLBACK_WAIT_MINUTES)
            self.state = State.WAITING
            return
        left   = int(limit_pos.left)
        top    = int(max(0, limit_pos.top - 15))
        width  = int(limit_pos.width + 500)
        height = int(limit_pos.height + 30)
        logging.info("OCR을 위해 알림 텍스트 영역을 캡처합니다...")
        try:
            shot    = pyautogui.screenshot(region=(left, top, width, height))
            results = ocr_reader.readtext(np.array(shot))
            text    = " ".join(t for _, t, _ in results)
            logging.info(f"🔍 [OCR 결과]: {text.replace(chr(10),' ').strip()[:200]}")
            parsed  = parse_target_time(text)
        except Exception as e:
            logging.error(f"OCR 실행 실패: {e}")
            parsed = None
        if parsed:
            self.target_time = parsed
            logging.info(f"재개 시각: {self.target_time:%Y-%m-%d %H:%M:%S}")
        else:
            self.target_time = datetime.datetime.now() + datetime.timedelta(minutes=FALLBACK_WAIT_MINUTES)
            logging.info(f"시간 파싱 실패 → 안전 대기({FALLBACK_WAIT_MINUTES}분): {self.target_time:%H:%M:%S}")
        self.state = State.WAITING

    # ── 포커스 및 입력 ─────────────────────────────────────
    def _focus_claude_window(self):
        win = self.get_claude_window()
        if win:
            try:
                if win.isMinimized:
                    win.restore()
                win.activate()
                time.sleep(0.2)
            except Exception as e:
                logging.error(f"클로드 창 활성화 실패: {e}")
            return win
        return None

    def _execute_smart_resume(self):
        win = self._focus_claude_window()
        if not win:
            logging.error("클로드 창을 찾을 수 없어 재개를 취소합니다.")
            return

        self.toast_signal.emit("3초 뒤 클로드가 대화를 재개합니다.")
        self._interruptible_sleep(3.0)
        if not self.running:
            return

        try:
            active_win = gw.getActiveWindow()
            if active_win and win.title != active_win.title:
                logging.warning("사용자 개입 감지 — 자동 전송을 보류합니다.")
                return
        except Exception:
            pass

        # 스텝 모드인 경우 전송할 텍스트를 미리 결정
        step_prompt: str | None = None
        if self.steps:
            step_idx = self.expected_step - 1
            if self.continue_count > 0:
                step_prompt = "계속 이어서 작성해 줘"
            elif 0 <= step_idx < len(self.steps):
                step_prompt = self._build_step_prompt(step_idx)
            else:
                logging.error(f"유효하지 않은 스텝 인덱스({step_idx}) — 전송 중단")
                return

        original_clipboard = safe_clipboard_paste()
        try:
            pyperclip.copy("")
            center_x = win.left + (win.width // 2)
            bottom_y = win.bottom - self.click_y_offset
            pyautogui.click(x=center_x, y=bottom_y)
            time.sleep(0.5)

            if step_prompt is not None:
                # 스텝 모드: 항상 전송할 내용이 정해져 있으므로 입력창을 지우고 붙여넣기
                pyautogui.hotkey('ctrl', 'a')
                time.sleep(0.2)
                pyperclip.copy(step_prompt)
                time.sleep(0.2)
                pyautogui.hotkey('ctrl', 'v')
                time.sleep(0.5)
                pyautogui.press('enter')
                time.sleep(1)
            else:
                # 클래식 모드: 기존 입력창 내용 확인 후 전송 or 스마트 프롬프트
                pyautogui.hotkey('ctrl', 'a')
                time.sleep(0.2)
                pyautogui.hotkey('ctrl', 'c')
                time.sleep(0.5)
                current_input = safe_clipboard_paste().strip()
                if len(current_input) > MAX_INPUT_LEN:
                    logging.warning(f"입력창 비정상({len(current_input)}자) — 전송 건너뜀. Y오프셋을 점검하세요.")
                elif current_input:
                    logging.info("입력창에 텍스트가 있습니다. 기존 메시지를 전송합니다.")
                    pyautogui.press('right')
                    time.sleep(0.2)
                    pyautogui.press('enter')
                else:
                    logging.info("입력창이 비어있습니다. 스마트 프롬프트를 전송합니다.")
                    pyperclip.copy(self.smart_prompt)
                    time.sleep(0.2)
                    pyautogui.hotkey('ctrl', 'v')
                    time.sleep(0.5)
                    pyautogui.press('enter')
                    time.sleep(1)
        finally:
            try:
                pyperclip.copy(original_clipboard if original_clipboard else "")
            except Exception as e:
                logging.debug(f"클립보드 복구 실패: {e}")

        self._interruptible_sleep(5.0)

# ---------------------------------------------------------
# 14. 메인 GUI 창
# ---------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{VERSION}")
        self.setWindowIcon(app_icon())
        self.resize(820, 440)
        self._tg_poller: TelegramPollerThread | None = None
        self._screen_busy = False   # /screen 중복 캡처 방지 플래그

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(8)
        root.setContentsMargins(12, 12, 12, 12)

        # ── 상단 툴바 ─────────────────────────────────────
        top_bar = QHBoxLayout()
        self.cb_continuous = QCheckBox("연속 작업 모드 (ON/OFF)")
        self.cb_continuous.setFont(QFont("Malgun Gothic", 10, QFont.Weight.Bold))
        self.cb_continuous.stateChanged.connect(self.toggle_continuous_mode)
        top_bar.addWidget(self.cb_continuous)

        self.cb_loop_forever = QCheckBox("🔁 무한 반복")
        self.cb_loop_forever.setFont(QFont("Malgun Gothic", 10, QFont.Weight.Bold))
        self.cb_loop_forever.setToolTip(
            "체크 시 모든 스텝 완료 후 Step 1부터 무한 반복합니다.\n"
            "해제 시 한 바퀴 완료하면 스크린샷 전송 후 자동 정지합니다."
        )
        self.cb_loop_forever.stateChanged.connect(self.toggle_loop_forever)
        top_bar.addWidget(self.cb_loop_forever)

        self.cb_always_on_top = QCheckBox("📌 항상 위 고정")
        self.cb_always_on_top.setFont(QFont("Malgun Gothic", 10, QFont.Weight.Bold))
        self.cb_always_on_top.stateChanged.connect(self.toggle_always_on_top)
        top_bar.addWidget(self.cb_always_on_top)

        top_bar.addStretch()
        for label, slot in [
            ("⚙️ 환경 설정", self.open_settings),
            ("🗑️ 로그 지우기", self.clear_logs),
            ("📨 텔레그램", self.open_telegram_settings),
        ]:
            btn = QPushButton(label)
            btn.setStyleSheet(
                "QPushButton { background-color:#34495e; color:white; font-weight:bold;"
                " padding:5px 10px; border-radius:4px; }"
                "QPushButton:hover { background-color:#2c3e50; }"
            )
            btn.clicked.connect(slot)
            top_bar.addWidget(btn)
        self._top_widget = QWidget()
        self._top_widget.setLayout(top_bar)
        root.addWidget(self._top_widget)

        # ── Y오프셋 + 위치 테스트 ─────────────────────────
        offset_frame = QWidget()
        offset_frame.setStyleSheet(
            "QWidget { background-color:rgba(100,100,100,0.1); border-radius:6px; }"
        )
        ofl = QHBoxLayout(offset_frame)
        ofl.setContentsMargins(10, 6, 10, 6)
        lbl = QLabel("📍 입력창 Y오프셋 (창 하단기준):")
        lbl.setStyleSheet("background:transparent; font-weight:bold;")
        ofl.addWidget(lbl)
        self.spin_offset = QSpinBox()
        self.spin_offset.setRange(10, 800)
        self.spin_offset.setValue(110)
        self.spin_offset.setSuffix(" px")
        self.spin_offset.setStyleSheet(
            "QSpinBox { background:#2c3e50; color:#fff; border:1px solid #34495e;"
            " border-radius:4px; padding:3px 8px; font-size:10pt; font-weight:bold; min-height:20px; }"
        )
        self.spin_offset.valueChanged.connect(self.update_offset)
        ofl.addWidget(self.spin_offset)
        btn_test = QPushButton("🎯 위치 테스트")
        btn_test.setStyleSheet(
            "QPushButton { background:#2c3e50; color:white; font-weight:bold;"
            " padding:4px 12px; border-radius:4px; }"
            "QPushButton:hover { background:#34495e; }"
        )
        btn_test.clicked.connect(self.test_click_position)
        ofl.addWidget(btn_test)
        ofl.addStretch()

        # ── 스텝 관리 버튼 + 진행 상황 라벨 ─────────────
        btn_step_mgr = QPushButton("📋 스텝 관리")
        btn_step_mgr.setStyleSheet(
            "QPushButton { background-color:#2980b9; color:white; font-weight:bold;"
            " padding:4px 14px; border-radius:4px; }"
            "QPushButton:hover { background-color:#3498db; }"
        )
        ofl.addWidget(btn_step_mgr)

        self._offset_frame = offset_frame
        root.addWidget(offset_frame)

        # 진행 상황 라벨 (메인 창에 유지)
        progress_row = QHBoxLayout()
        self.lbl_progress = QLabel("스텝 없음 — 클래식 모드로 실행됩니다.")
        self.lbl_progress.setStyleSheet("color:#888; font-size:9pt;")
        progress_row.addWidget(self.lbl_progress)
        progress_row.addStretch()
        self._progress_widget = QWidget()
        self._progress_widget.setLayout(progress_row)
        root.addWidget(self._progress_widget)

        # ── 접기/펼치기 토글 (항상 보임) ──────────────────
        collapse_row = QHBoxLayout()
        self.btn_collapse = QPushButton("🔽 로그만 보기")
        self.btn_collapse.setStyleSheet(
            "QPushButton { background-color:#566573; color:white; font-weight:bold;"
            " padding:3px 12px; border-radius:4px; }"
            "QPushButton:hover { background-color:#6c7a89; }"
        )
        self.btn_collapse.clicked.connect(self.toggle_collapsed)
        collapse_row.addWidget(self.btn_collapse)

        # 접힌 상태 전용 미니 체크박스 (메인 체크박스와 동기화)
        self.cb_continuous_mini = QCheckBox("연속")
        self.cb_continuous_mini.setToolTip("연속 작업 모드")
        self.cb_continuous_mini.setVisible(False)
        self.cb_continuous_mini.stateChanged.connect(self._on_continuous_mini)
        collapse_row.addWidget(self.cb_continuous_mini)

        self.cb_loop_forever_mini = QCheckBox("🔁 무한")
        self.cb_loop_forever_mini.setToolTip("무한 반복 모드")
        self.cb_loop_forever_mini.setVisible(False)
        self.cb_loop_forever_mini.stateChanged.connect(self._on_loop_forever_mini)
        collapse_row.addWidget(self.cb_loop_forever_mini)

        # 접힌 상태 전용 미니 시작/중지 버튼 (펼친 상태에서는 숨김)
        self.btn_start_mini = QPushButton("▶ 시작")
        self.btn_start_mini.setStyleSheet(
            "QPushButton { background:#2ecc71; color:white; font-weight:bold;"
            " padding:3px 12px; border-radius:4px; }"
            "QPushButton:hover { background:#27ae60; }"
            "QPushButton:disabled { background:#95a5a6; }"
        )
        self.btn_start_mini.clicked.connect(self.start_worker)
        self.btn_start_mini.setVisible(False)
        collapse_row.addWidget(self.btn_start_mini)

        self.btn_stop_mini = QPushButton("■ 중지")
        self.btn_stop_mini.setStyleSheet(
            "QPushButton { background:#e74c3c; color:white; font-weight:bold;"
            " padding:3px 12px; border-radius:4px; }"
            "QPushButton:hover { background:#c0392b; }"
            "QPushButton:disabled { background:#95a5a6; }"
        )
        self.btn_stop_mini.clicked.connect(self.stop_worker)
        self.btn_stop_mini.setEnabled(False)
        self.btn_stop_mini.setVisible(False)
        collapse_row.addWidget(self.btn_stop_mini)

        collapse_row.addStretch()
        root.addLayout(collapse_row)

        # ── 로그 콘솔 ─────────────────────────────────────
        self.log_console = QPlainTextEdit()
        self.log_console.setReadOnly(True)
        self.log_console.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.log_console.setStyleSheet(
            "QPlainTextEdit { background:#1E1E1E; color:#D4D4D4;"
            " font-family:Consolas,monospace; font-size:10pt; padding:8px;"
            " border:2px solid #2c3e50; border-radius:6px; }"
        )
        root.addWidget(self.log_console, stretch=1)

        # ── 액션 버튼 ─────────────────────────────────────
        action_bar = QHBoxLayout()
        action_bar.setSpacing(10)

        self.btn_start = QPushButton("▶ 감시 시작")
        self.btn_start.setStyleSheet(
            "QPushButton { background:#2ecc71; color:white; font-weight:bold;"
            " font-size:12pt; padding:10px; border-radius:6px; }"
            "QPushButton:hover { background:#27ae60; }"
            "QPushButton:disabled { background:#95a5a6; }"
        )
        self.btn_start.clicked.connect(self.start_worker)
        action_bar.addWidget(self.btn_start)

        self.btn_stop = QPushButton("■ 감시 중지")
        self.btn_stop.setStyleSheet(
            "QPushButton { background:#e74c3c; color:white; font-weight:bold;"
            " font-size:12pt; padding:10px; border-radius:6px; }"
            "QPushButton:hover { background:#c0392b; }"
            "QPushButton:disabled { background:#95a5a6; }"
        )
        self.btn_stop.clicked.connect(self.stop_worker)
        self.btn_stop.setEnabled(False)
        action_bar.addWidget(self.btn_stop)

        self.btn_force_next = QPushButton("⏭ 강제 다음 스텝")
        self.btn_force_next.setStyleSheet(
            "QPushButton { background:#8e44ad; color:white; font-weight:bold;"
            " font-size:12pt; padding:10px; border-radius:6px; }"
            "QPushButton:hover { background:#9b59b6; }"
            "QPushButton:disabled { background:#95a5a6; }"
        )
        self.btn_force_next.clicked.connect(self._on_force_next)
        self.btn_force_next.setEnabled(False)
        action_bar.addWidget(self.btn_force_next)

        self._action_widget = QWidget()
        self._action_widget.setLayout(action_bar)
        root.addWidget(self._action_widget)
        self._collapsed = False

        # ── 워커 및 로깅 연결 ─────────────────────────────
        self.worker = ClaudeWorker()
        self.worker.toast_signal.connect(self.show_toast)
        self.worker.continuous_mode_changed.connect(self._sync_continuous_checkbox)
        self.worker.step_progress_signal.connect(self._on_step_progress)
        self.worker.auto_paused_signal.connect(self._on_auto_paused)
        self.worker.all_steps_done_signal.connect(self._on_all_steps_done)
        self.setup_logging()

        # 스텝 관리 다이얼로그 — lbl_progress 등 모든 위젯 생성 후 여기서 초기화
        self._step_dlg = StepManagerDialog(self)
        btn_step_mgr.clicked.connect(
            lambda: (self._step_dlg.show(),
                     self._step_dlg.raise_(),
                     self._step_dlg.activateWindow())
        )

        # 저장된 스텝 복원 — 없으면 기본 스텝 삽입
        saved = load_steps()
        initial_steps = saved if saved is not None else DEFAULT_STEPS
        for step_text in initial_steps:
            self._step_dlg.add_step(text=step_text)
        self._update_progress_label()

        # 저장된 창 위치·크기 복원 — 없으면 우측 하단에 배치
        geom = load_window_geometry()
        if geom:
            self.resize(geom["w"], geom["h"])
            self.move(geom["x"], geom["y"])
        else:
            screen_geom = QApplication.primaryScreen().availableGeometry()
            self.move(screen_geom.right() - self.width() - 10,
                      screen_geom.bottom() - self.height() - 50)


    # ── 스텝 관리 ─────────────────────────────────────────
    def add_step(self, _checked=False, text: str = ""):
        self._step_dlg.add_step(text=text)
        self._update_progress_label()

    def _update_progress_label(self, current: int = 0):
        total = len(self._step_dlg._step_items)
        if total == 0:
            self.lbl_progress.setText("스텝 없음 — 클래식 모드로 실행됩니다.")
        elif current == 0:
            self.lbl_progress.setText(f"총 {total}개 스텝 등록됨. 시작하면 Step 1부터 진행합니다.")
        else:
            self.lbl_progress.setText(f"▶ Step {current}/{total} 진행 중")

    def get_steps(self) -> list[str]:
        return self._step_dlg.get_steps()

    def _lock_ui(self, locked: bool):
        self._step_dlg.set_locked(locked)

    # ── 신호 핸들러 ───────────────────────────────────────
    def _on_step_progress(self, current: int, total: int):
        self._update_progress_label(current)
        self._step_dlg.set_step_active(current, total)

    def _on_auto_paused(self, reason: str):
        logging.warning(f"⚠️ 자동 일시 정지: {reason}")
        self.btn_force_next.setEnabled(True)

    def _on_force_next(self):
        self.worker.force_next_step()
        self.btn_force_next.setEnabled(False)

    def _sync_continuous_checkbox(self, on: bool):
        self.cb_continuous.blockSignals(True)
        self.cb_continuous.setChecked(on)
        self.cb_continuous.blockSignals(False)

    # ── 텔레그램 명령 처리 ────────────────────────────────
    def _on_telegram_command(self, raw: str):
        cfg = load_telegram_config()
        token = cfg["bot_token"] if cfg else ""
        chat  = cfg["chat_id"]   if cfg else ""

        def reply(text: str):
            if token and chat:
                send_telegram_message(token, chat, text)

        # 명령어 + 인자 분리 (예: "/send DB 연결부 다시 작성해")
        parts = (raw or "").strip().split(maxsplit=1)
        if not parts:
            return
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""
        if cmd == "/status":
            steps_info = (f"스텝 {self.worker.expected_step}/{len(self.worker.steps)}"
                          if self.worker.steps else "클래식 모드")
            reply(f"[Auto-Pilot] 상태: {self.worker.state.value} | {steps_info}"
                  f" | 연속모드: {'ON' if self.worker.continuous_mode else 'OFF'}")
        elif cmd == "/stop":
            QTimer.singleShot(0, self.stop_worker)
            reply("[Auto-Pilot] 감시를 중지합니다.")
        elif cmd == "/pause":
            self.worker.continuous_mode = False
            self._sync_continuous_checkbox(False)
            reply("[Auto-Pilot] 연속 모드를 OFF했습니다.")
        elif cmd == "/resume":
            self.worker.continuous_mode = True
            self._sync_continuous_checkbox(True)
            if self.worker.state == State.PAUSED:
                self.worker.state = State.RESUMING
            reply("[Auto-Pilot] 연속 모드를 ON했습니다.")
        elif cmd == "/next":
            next_n = min(self.worker.expected_step + 1, len(self.worker.steps) + 1) if self.worker.steps else 1
            QTimer.singleShot(0, self._on_force_next)
            reply(f"[Auto-Pilot] 강제 다음 스텝 → Step {next_n}")
        elif cmd == "/screen":
            # _on_telegram_command은 메인 스레드 단독 실행 → 플래그 검사·설정에 경쟁 없음.
            if self._screen_busy:
                reply("[Auto-Pilot] 이미 화면 캡처가 진행 중입니다. 잠시 후 다시 시도하세요.")
            else:
                self._screen_busy = True
                reply("[Auto-Pilot] 📸 현재 화면을 캡처해 전송합니다…")
                # 캡처+업로드는 GUI를 막지 않도록 데몬 스레드에서 처리
                threading.Thread(target=self._send_screen_capture, daemon=True).start()
        elif cmd == "/send":
            if not arg.strip():
                reply("[Auto-Pilot] 사용법: /send <클로드에게 보낼 메시지>")
            elif not self.worker.isRunning():
                reply("[Auto-Pilot] 감시가 실행 중이 아닙니다. 먼저 시작 후 사용하세요.")
            else:
                # 워커 스레드가 다음 루프에서 클로드에 붙여넣어 전송 (충돌 방지)
                self.worker.queue_message(arg)
                reply(f"[Auto-Pilot] 📨 메시지를 전송 큐에 넣었습니다:\n{arg[:200]}")
        elif cmd == "/help":
            reply(
                "[Auto-Pilot] 📋 명령어 목록\n"
                "\n"
                "/status — 현재 상태 조회\n"
                "/screen — PC 화면 캡처 전송\n"
                "/send [메시지] — 클로드에 직접 메시지 전송\n"
                "/pause — 연속 모드 OFF (자동 진행 멈춤)\n"
                "/resume — 연속 모드 ON / PAUSED 재개\n"
                "/next — 강제 다음 스텝으로 넘김\n"
                "/stop — 감시 완전 중지\n"
                "/help — 이 명령어 목록 표시\n"
                "\n"
                "※ 감시 시작 후에만 명령이 동작합니다.\n"
                "※ 30초 지난 명령은 자동 폐기됩니다."
            )
        else:
            reply(f"[Auto-Pilot] 알 수 없는 명령: {cmd}\n"
                  f"/help 로 명령어 목록을 확인하세요.")
        logging.info(f"📨 텔레그램 명령 수신: {cmd}")

    def _send_screen_capture(self):
        """현재 전체 화면을 캡처해 텔레그램으로 전송 (데몬 스레드에서 호출)."""
        tmp = None
        try:
            shot = pyautogui.screenshot()
            tmp  = os.path.join(tempfile.gettempdir(),
                                f"autopilot_screen_{int(time.time())}.png")
            shot.save(tmp)
            caption = f"[Auto-Pilot] 🖥 화면 캡처 ({datetime.datetime.now():%Y-%m-%d %H:%M:%S})"
            if not send_telegram_photo(tmp, caption):
                send_telegram_text("[Auto-Pilot] 화면 전송에 실패했습니다.")
        except Exception as e:
            logging.error(f"화면 캡처 전송 실패: {e}")
            send_telegram_text(f"[Auto-Pilot] 화면 캡처 실패: {e}")
        finally:
            if tmp:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            self._screen_busy = False

    # ── 감시 시작/중지 ────────────────────────────────────
    def start_worker(self):
        if self.worker.isRunning():
            return

        steps = self.get_steps()

        # 사전 검증 — 스텝 완료는 이미지 매칭 전용이므로 스텝마다 완료 이미지가 필요하다.
        if steps:
            missing = [i for i in range(1, len(steps) + 1)
                       if not glob.glob(resource_path(f"step{i}_complete*.png"))]
            if missing:
                nums = ", ".join(str(n) for n in missing)
                QMessageBox.critical(
                    self, "사전 검증 실패",
                    f"스텝 {nums}번: 완료 감지 이미지(step{{N}}_complete*.png)가 없습니다.\n\n"
                    f"스텝 완료는 이미지 매칭으로만 감지합니다.\n"
                    f"각 스텝의 완료 표식(예: [STEP{{N}}_DONE] 텍스트)을 캡처해\n"
                    f"resource 폴더에 step{{N}}_complete.png 로 추가하세요."
                )
                return

        # 워커 초기화
        self.worker.steps           = steps
        self.worker.expected_step   = 1
        self.worker.continue_count  = 0
        self.worker.continuous_mode = self.cb_continuous.isChecked()
        self.worker.loop_forever    = self.cb_loop_forever.isChecked()

        # F12 전역 긴급 정지 등록
        if HAS_KEYBOARD:
            try:
                _keyboard_lib.add_hotkey('f12', self._emergency_stop_hotkey)
                logging.info("⌨️ F12 전역 긴급 정지 등록됨.")
            except Exception as e:
                logging.warning(f"F12 단축키 등록 실패: {e}")

        # 텔레그램 양방향 폴러 시작
        self._start_tg_poller()

        self._lock_ui(True)
        self._set_running_ui(True)
        self._update_progress_label(1 if steps else 0)
        # 감시 시작 시 Claude 창을 앞으로 가져와 Auto-Pilot에 가리지 않게 함
        win = self.worker.get_claude_window()
        if win:
            try:
                if win.isMinimized:
                    win.restore()
                win.activate()
            except Exception:
                pass
        self.worker.start()

    def stop_worker(self):
        self._teardown_run_resources()
        self.worker.stop()
        self.worker.wait()
        self._reset_ui_after_stop()
        logging.info("감시가 중지되었습니다.")

    def _teardown_run_resources(self):
        """F12 단축키·텔레그램 폴러 등 실행 중 자원을 해제한다."""
        if HAS_KEYBOARD:
            try:
                _keyboard_lib.remove_hotkey('f12')
            except Exception:
                pass
        self._stop_tg_poller()

    def _set_running_ui(self, running: bool):
        """메인·미니 시작/중지 버튼의 활성 상태를 함께 갱신한다."""
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        self.btn_start_mini.setEnabled(not running)
        self.btn_stop_mini.setEnabled(running)

    def _reset_ui_after_stop(self):
        """감시 종료 후 버튼·잠금·진행 표시를 초기 상태로 되돌린다."""
        self._lock_ui(False)
        self._set_running_ui(False)
        self.btn_force_next.setEnabled(False)
        self._step_dlg.set_step_active(0, 0)
        self._update_progress_label()

    def _on_all_steps_done(self):
        """워커가 한 바퀴 완료 후 스스로 정지할 때 호출 — 자원 해제 및 UI 정리."""
        self._teardown_run_resources()
        self.worker.wait()
        self._reset_ui_after_stop()
        logging.info("✅ 모든 스텝을 완료하여 감시를 종료했습니다.")

    def _emergency_stop_hotkey(self):
        """keyboard 라이브러리 콜백 — 워커 스레드에서 호출되므로 Qt 작업은 메인으로 마샬."""
        self.worker.stop()
        QTimer.singleShot(0, self._after_emergency_stop)

    def _after_emergency_stop(self):
        if not self.worker.wait(6000):
            # 6초 내 종료 실패 — UI를 풀면 살아있는 워커 위에 새 워커가 겹칠 수 있다.
            logging.error("🚨 워커가 6초 내 종료되지 않았습니다 — 잠금을 유지합니다.")
            QMessageBox.warning(
                self, "긴급 정지",
                "워커가 정상적으로 종료되지 않았습니다.\n"
                "잠시 후 자동 정리되며, 계속 멈춰 있으면 앱을 재시작하세요.",
            )
            return
        self._lock_ui(False)
        self._set_running_ui(False)
        self.btn_force_next.setEnabled(False)
        logging.warning("🚨 F12 긴급 정지!")

    # ── 텔레그램 폴러 관리 ────────────────────────────────
    def _start_tg_poller(self):
        cfg = load_telegram_config()
        if not cfg or not cfg.get("bidirectional"):
            return
        self._tg_poller = TelegramPollerThread(cfg["bot_token"], cfg["chat_id"])
        self._tg_poller.command_received.connect(self._on_telegram_command)
        self._tg_poller.start()
        logging.info("📨 텔레그램 양방향 제어 폴러 시작.")

    def _stop_tg_poller(self):
        if self._tg_poller and self._tg_poller.isRunning():
            self._tg_poller.stop()
            self._tg_poller.wait()
            self._tg_poller = None

    # ── 설정 다이얼로그 ────────────────────────────────────
    def open_settings(self):
        dlg = MessageSettingsDialog(self.worker.smart_prompt, self)
        if dlg.exec():
            self.worker.smart_prompt = dlg.get_values()
            logging.info("⚙️ 환경 설정이 저장되었습니다.")

    def open_telegram_settings(self):
        TelegramSettingsDialog(self).exec()

    # ── 기타 UI 슬롯 ──────────────────────────────────────
    def update_offset(self, value: int):
        self.worker.click_y_offset = value
        logging.info(f"클릭 위치 오프셋이 {value}px 로 변경되었습니다.")

    def toggle_continuous_mode(self, _):
        on = self.cb_continuous.isChecked()
        self.cb_continuous_mini.blockSignals(True)
        self.cb_continuous_mini.setChecked(on)
        self.cb_continuous_mini.blockSignals(False)
        self.worker.continuous_mode = on
        logging.info(f"연속 작업 모드가 {'ON' if on else 'OFF'} 되었습니다.")

    def _on_continuous_mini(self, _):
        # 미니 체크박스 → 메인 체크박스로 위임 (메인 핸들러가 워커·동기화 처리)
        self.cb_continuous.setChecked(self.cb_continuous_mini.isChecked())

    def toggle_loop_forever(self, _):
        on = self.cb_loop_forever.isChecked()
        self.cb_loop_forever_mini.blockSignals(True)
        self.cb_loop_forever_mini.setChecked(on)
        self.cb_loop_forever_mini.blockSignals(False)
        self.worker.loop_forever = on
        logging.info(f"무한 반복 모드가 {'ON' if on else 'OFF'} 되었습니다.")

    def _on_loop_forever_mini(self, _):
        self.cb_loop_forever.setChecked(self.cb_loop_forever_mini.isChecked())

    def toggle_collapsed(self):
        """로그만 보이도록 접거나, 모든 컨트롤을 다시 펼친다."""
        self._collapsed = not self._collapsed
        for w in (self._top_widget, self._offset_frame,
                  self._progress_widget, self._action_widget):
            w.setVisible(not self._collapsed)
        # 접힌 상태에서는 펼치기 버튼 옆 미니 시작/중지 버튼과 체크박스를 노출
        self.btn_start_mini.setVisible(self._collapsed)
        self.btn_stop_mini.setVisible(self._collapsed)
        self.cb_continuous_mini.setVisible(self._collapsed)
        self.cb_loop_forever_mini.setVisible(self._collapsed)
        if self._collapsed:
            self.btn_collapse.setText("🔼 펼치기")
            # 접힌 상태에서 창을 작게 줄일 수 있도록 최소 크기 제한 완화
            self.setMinimumSize(0, 0)
        else:
            self.btn_collapse.setText("🔽 로그만 보기")

    def toggle_always_on_top(self, _):
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint,
                           self.cb_always_on_top.isChecked())
        self.show()

    def test_click_position(self):
        win = self.worker.get_claude_window()
        if not win:
            logging.error("클로드 창을 찾을 수 없습니다.")
            return
        try:
            if win.isMinimized:
                win.restore()
            win.activate()
            time.sleep(0.3)
            cx = win.left + (win.width // 2)
            cy = win.bottom - self.spin_offset.value()
            logging.info(f"테스트 클릭 → X:{cx} Y:{cy}")
            pyautogui.moveTo(cx, cy, duration=0.5)
            pyautogui.click()
            logging.info("테스트 클릭 완료. 엉뚱한 곳이면 Y오프셋을 조정하세요.")
        except Exception as e:
            logging.error(f"테스트 클릭 실패: {e}")

    def setup_logging(self):
        logger = logging.getLogger()
        logger.setLevel(logging.INFO)
        gh = QTextEditLogger(self)
        gh.setFormatter(logging.Formatter(
            '%(asctime)s [%(levelname)s] %(message)s', datefmt="%H:%M:%S"))
        logger.addHandler(gh)
        fh = logging.handlers.RotatingFileHandler(
            "app.log", maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT,
            encoding='utf-8')
        fh.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
        logger.addHandler(fh)

    def append_log(self, msg: str, record: logging.LogRecord):
        if record.levelno >= logging.ERROR:
            html = f"<span style='color:#ff5555;'>{msg}</span>"
        elif record.levelno == logging.WARNING:
            html = f"<span style='color:#ffb86c;'>{msg}</span>"
        elif any(k in msg for k in ("감지되었습니다", "완료되었습니다", "이동 중")):
            html = f"<span style='color:#50fa7b;'>{msg}</span>"
        elif "[상태 변경]" in msg:
            html = f"<span style='color:#8be9fd;'>{msg}</span>"
        else:
            html = f"<span style='color:#d4d4d4;'>{msg}</span>"
        self.log_console.appendHtml(html)
        self.log_console.verticalScrollBar().setValue(
            self.log_console.verticalScrollBar().maximum())

    def clear_logs(self):
        self.log_console.clear()

    def show_toast(self, message: str):
        self.toast = ToastNotification(message)
        self.toast.show()

    def closeEvent(self, event):
        # 종료 시 현재 스텝과 창 위치·크기를 저장해 다음 실행에 복원한다.
        save_steps([item.get_text() for item in self._step_dlg._step_items])
        save_window_geometry(self.x(), self.y(), self.width(), self.height())
        self._stop_tg_poller()
        if HAS_KEYBOARD:
            try:
                _keyboard_lib.remove_hotkey('f12')
            except Exception:
                pass
        if self.worker.isRunning():
            self.worker.stop()
            # 워커가 pyautogui 동작 중일 수 있으므로 타임아웃을 두고,
            # 시간 내 종료 못 하면 강제 종료해 프로세스가 매달리지 않게 한다.
            if not self.worker.wait(5000):
                logging.warning("워커가 제때 종료되지 않아 강제 종료합니다.")
                self.worker.terminate()
                self.worker.wait()
        super().closeEvent(event)

# ---------------------------------------------------------
# 15. 진입점
# ---------------------------------------------------------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setWindowIcon(app_icon())
    palette = app.palette()
    palette.setColor(palette.ColorRole.Highlight, QColor("#2c3e50"))
    app.setPalette(palette)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
