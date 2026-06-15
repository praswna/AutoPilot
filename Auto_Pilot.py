# ==========================================
# Auto-Pilot v1.3.0
# ==========================================
VERSION = "1.3.0"

import os
import sys
import time
import datetime
import re
import logging
import logging.handlers  # [제안] app.log 크기 기준 롤오버용
import glob  # [추가] 파일 패턴 검색을 위한 모듈
import json       # 텔레그램 설정(telegram_config.json) 읽기용
import tempfile   # 백로그 캡처 임시 저장용
import urllib.request  # 텔레그램 API 호출용(외부 의존성 없이 stdlib 사용)
import urllib.parse
import urllib.error
from enum import Enum
from collections import namedtuple

# PyQt6 윈도우 DPI 관련 경고 메시지 숨김 처리
os.environ["QT_LOGGING_RULES"] = "qt.qpa.window=false"

# PyQt6 패키지
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QCheckBox, QPlainTextEdit,
                             QLabel, QDialog, QSpinBox, QDoubleSpinBox, QFormLayout, QLineEdit, QDialogButtonBox, QGroupBox,
                             QSystemTrayIcon, QMenu, QMessageBox)
from PyQt6.QtCore import QThread, pyqtSignal, Qt, QTimer, QObject
from PyQt6.QtGui import QFont, QIcon, QPixmap, QPainter, QColor, QBrush

# 의존성 패키지
import pyautogui
import pygetwindow as gw
import pyperclip

# [수정] 외부 프로그램 설치가 필요 없는 EasyOCR 라이브러리 사용
try:
    import easyocr
    import numpy as np
    HAS_OCR = True
    ocr_reader = None # OCR 엔진 지연 초기화용 변수
except ImportError:
    HAS_OCR = False

# ---------------------------------------------------------
# 1. 설정 및 상수 (Configuration)
# ---------------------------------------------------------
APP_NAME = "Auto-Pilot"
TARGET_WINDOW_TITLE = "Claude"
EXCLUDE_TITLE_KEYWORDS = [
    "Visual Studio Code", "Cursor", "Sublime", "Notepad++",
    "메모장", "Notepad", ".py", ".log", ".txt", "탐색기", "Explorer",
]
CHECK_INTERVAL = 5
FALLBACK_WAIT_MINUTES = 30
CONFIDENCE_LEVEL = 0.8
PAST_TIME_GRACE_MINUTES = 5
MAX_INPUT_LEN = 4000

# [제안 #2·#3] 화면 템플릿 매칭 기본 신뢰도 (환경 설정창에서 0.40~0.99 범위로 조정 가능).
#   - 생성(■)/완료(↵)는 단계적 신뢰도 사다리의 기준값으로 쓰인다.
#   - 한도 화면은 오탐 차단을 위해 더 엄격(컬러 비교)하게 둔다.
GENERATING_CONFIDENCE = 0.80
READY_CONFIDENCE = 0.80
LIMIT_CONFIDENCE = 0.90

# [대기→완료] app.log 파일이 무한정 커지지 않도록 크기 기준 롤오버를 적용한다.
LOG_MAX_BYTES = 2 * 1024 * 1024   # 2MB 도달 시 회전
LOG_BACKUP_COUNT = 3              # app.log + .1 ~ .3 백업 유지

# [제안 #1] 주기당 1회 캡처한 화면에서 찾은 좌표를 절대 화면 기준으로 담는 경량 박스.
# pyautogui.Box(namedtuple)와 같은 필드(.left/.top/.width/.height)라 호출부 호환된다.
ScreenBox = namedtuple("ScreenBox", "left top width height")

# 계획이 안정되면 Claude가 응답 끝에 출력하는 토큰. OCR로 감지하면 연속 모드를 자동 종료한다.
STABLE_TOKEN = "LOOPSTABLE"

# 정지 시 텔레그램으로 백로그 캡처를 보낼 때 쓰는 설정 파일 (exe와 같은 폴더).
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
TELEGRAM_CONFIG_PATH = os.path.join(APP_DIR, "telegram_config.json")

# 스마트 프롬프트 (계획서 아티팩트 방식)
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


def load_telegram_config() -> dict | None:
    """telegram_config.json 을 읽어 활성·필수값이 채워져 있으면 dict, 아니면 None."""
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
    """단순 multipart/form-data 본문을 만든다 (외부 라이브러리 없이)."""
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
    """주어진 토큰/chat_id로 텍스트 메시지를 보낸다. (테스트용) (성공여부, 메시지) 반환."""
    if not bot_token or not chat_id:
        return False, "bot_token 또는 chat_id 가 비어 있습니다."
    try:
        import urllib.parse
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
    """telegram_config.json 설정이 있으면 텍스트 메시지를 보낸다. 설정 없으면 조용히 건너뜀.
    (정지 알림에 백로그 텍스트를 동봉할 때 사용)"""
    cfg = load_telegram_config()
    if not cfg:
        return False
    ok, msg = send_telegram_message(cfg["bot_token"], cfg["chat_id"], text)
    if not ok:
        logging.warning(f"텔레그램 백로그 텍스트 전송 실패: {msg}")
    return ok


def send_telegram_photo(image_path: str, caption: str) -> bool:
    """telegram_config.json 설정이 있으면 사진을 전송한다. 설정 없으면 조용히 건너뜀."""
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
            logging.info("📨 텔레그램으로 정지 알림(백로그 캡처)을 보냈습니다.")
        else:
            logging.warning(f"텔레그램 전송 응답 코드: {resp.status}")
        return ok
    except Exception as e:
        logging.error(f"텔레그램 전송 실패: {e}")
        return False

def parse_target_time(text: str) -> datetime.datetime | None:
    if not text:
        return None

    # [수정] 복사된 텍스트가 너무 길 경우 (대화내역 전체 복사 등), 맨 마지막이나 관련 키워드 주변만 탐색
    if len(text) > 500:
        keyword_idx = max(text.rfind('재설정'), text.rfind('한도'), text.lower().rfind('limit'), text.lower().rfind('reset'))
        if keyword_idx != -1:
            start = max(0, keyword_idx - 100)
            end = min(len(text), keyword_idx + 100)
            text = text[start:end]
        else:
            text = text[-500:]

    # 보이지 않는 유니코드 제어문자 및 탭, 줄바꿈 등을 모두 제거/정규화
    text = re.sub(r'[\u200b\u200c\u200d\u200e\u200f\ufeff]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()

    hour, minute = None, None
    is_pm = '오후' in text or 'pm' in text.lower()
    is_am = '오전' in text or 'am' in text.lower()

    # 1. XX:XX 또는 XX.XX 형태 (예: 5:30)
    match = re.search(r'(\d{1,2})\s*[:\.]\s*(\d{2})', text)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
    else:
        # 2. X시 X분 형태
        match_kr = re.search(r'(\d{1,2})\s*시\s*(\d{1,2})\s*분', text)
        if match_kr:
            hour = int(match_kr.group(1))
            minute = int(match_kr.group(2))
        else:
            # 3. X시 형태
            match_kr_hour = re.search(r'(\d{1,2})\s*시', text)
            if match_kr_hour:
                hour = int(match_kr_hour.group(1))
                minute = 0

    if hour is None or minute is None:
        return None

    # 시간 범위 방어
    if not (0 <= hour <= 24) or not (0 <= minute <= 59):
        return None

    # AM/PM 처리 및 24시간제 변환
    if is_pm and hour < 12:
        hour += 12
    elif is_am and hour == 12:
        hour = 0
    elif hour == 24:
        hour = 0

    now = datetime.datetime.now()
    target_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    # 과거 시간으로 잡혔을 경우 (다음 날로 설정하되, 몇 분 차이는 유예)
    if target_time < now:
        if (now - target_time) <= datetime.timedelta(minutes=PAST_TIME_GRACE_MINUTES):
            target_time = now
        else:
            # AM/PM 표시가 없고 현재 시간보다 과거라면 (예: 현재 14시인데 5:30으로 잡힌 경우 -> 17:30으로 추론)
            if not is_am and not is_pm and hour < 12:
                pm_time = target_time.replace(hour=hour + 12)
                if pm_time > now:
                    target_time = pm_time
                else:
                    target_time += datetime.timedelta(days=1)
            else:
                target_time += datetime.timedelta(days=1)

    return target_time

# ---------------------------------------------------------
# 3. 로깅 시그널 및 핸들러 (GUI용)
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
# 4. 토스트 알림 팝업
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
                background-color: #2D2D2D;
                color: #FFFFFF;
                padding: 15px;
                border-radius: 5px;
                font-family: 'Malgun Gothic', sans-serif;
                font-size: 11pt;
                font-weight: bold;
            }
        """)
        layout.addWidget(label)
        self.setLayout(layout)

        screen = QApplication.primaryScreen().geometry()
        x = screen.width() - 350
        y = screen.height() - 150
        self.move(x, y)
        QTimer.singleShot(duration, self.close)

# ---------------------------------------------------------
# 5. 오토파일럿 워커 스레드 (비동기 처리)
# ---------------------------------------------------------
class State(Enum):
    IDLE = "IDLE"
    MONITORING = "MONITORING"
    WAITING = "WAITING"
    GENERATING = "GENERATING"
    RESUMING = "RESUMING"

DEFAULT_MESSAGES = {
    State.IDLE: "클로드 앱이 닫혀 있습니다. 창이 켜지기를 대기 중입니다.",
    State.MONITORING: "클로드가 '대기 중(입력 가능)' 상태입니다. (연속 모드: {mode})",
    State.GENERATING: "클로드가 현재 '답변을 작성'하고 있습니다.",
    State.WAITING: "사용량 한도 초과 상태입니다. 휴식 중 (재개 예정: {time})",
    State.RESUMING: "클로드에게 다음 작업을 지시(프롬프트 전송)하는 중입니다."
}

class TelegramSettingsDialog(QDialog):
    """텔레그램 정지 알림 설정 + 사용법 안내 + 테스트 전송 (앱 내장)."""

    GUIDE = (
        "<b>📨 텔레그램 정지 알림 설정</b><br><br>"
        "연속 모드가 <b>계획 안정(LOOPSTABLE)</b>으로 자동 정지될 때, "
        "Claude 창 캡처(백로그 포함)를 텔레그램으로 보냅니다.<br><br>"
        "<b>① 봇 토큰 받기</b><br>"
        "텔레그램에서 <b>@BotFather</b> 검색 → <b>/newbot</b> → 안내대로 진행하면 "
        "봇 <b>토큰</b>을 줍니다. 그 토큰을 아래 '봇 토큰'에 붙여넣으세요.<br><br>"
        "<b>② 내 chat id 받기</b><br>"
        "방금 만든 봇과 대화를 시작(아무 메시지 전송)한 뒤, "
        "<b>@userinfobot</b> 에게 말을 걸면 내 <b>id</b> 숫자를 알려줍니다. "
        "그 숫자를 '내 chat id'에 입력하세요.<br><br>"
        "<b>③ 테스트 → 저장</b><br>"
        "'테스트 전송'으로 메시지가 오는지 확인하고, '알림 사용'을 켠 뒤 '저장'하세요.<br>"
        "<i>※ 토큰은 비밀번호 같은 값이니 외부에 공유하지 마세요.</i>"
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("텔레그램 정지 알림 설정")
        self.resize(520, 560)

        layout = QVBoxLayout(self)

        guide = QLabel(self.GUIDE)
        guide.setWordWrap(True)
        guide.setTextFormat(Qt.TextFormat.RichText)
        guide.setStyleSheet("font-size: 10pt; padding: 4px;")
        layout.addWidget(guide)

        form = QFormLayout()
        self.cb_enabled = QCheckBox("알림 사용 (정지 시 텔레그램으로 캡처 전송)")
        form.addRow(self.cb_enabled)

        self.edit_token = QLineEdit()
        self.edit_token.setPlaceholderText("예: 123456789:ABCd... (BotFather가 준 토큰)")
        self.edit_token.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("봇 토큰:", self.edit_token)

        self.edit_chat = QLineEdit()
        self.edit_chat.setPlaceholderText("예: 987654321 (숫자 id)")
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

    def _toggle_token_echo(self, _state):
        self.edit_token.setEchoMode(
            QLineEdit.EchoMode.Normal if self.cb_show_token.isChecked()
            else QLineEdit.EchoMode.Password
        )

    def _load_existing(self):
        try:
            with open(TELEGRAM_CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            self.cb_enabled.setChecked(bool(cfg.get("enabled")))
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
            "enabled": self.cb_enabled.isChecked(),
            "bot_token": self.edit_token.text().strip(),
            "chat_id": self.edit_chat.text().strip(),
        }
        try:
            with open(TELEGRAM_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            self.lbl_result.setText(f"✅ 저장됨: {TELEGRAM_CONFIG_PATH}")
            self.lbl_result.setStyleSheet("color: #2ecc71; padding: 2px;")
        except Exception as e:
            self.lbl_result.setText(f"❌ 저장 실패: {e}")
            self.lbl_result.setStyleSheet("color: #e74c3c; padding: 2px;")


class MessageSettingsDialog(QDialog):
    def __init__(self, current_messages, current_prompt, current_conf, parent=None):
        super().__init__(parent)
        self.setWindowTitle("상황별 메시지 및 설정")
        self.resize(550, 720)
        
        layout = QVBoxLayout(self)

        # 상황별 메시지 설정 그룹
        msg_group = QGroupBox("상황별 로그 메시지 설정")
        form_layout = QFormLayout()
        
        self.inputs = {}
        labels = {
            State.IDLE: "대기 중 (창 닫힘):",
            State.MONITORING: "감시 중 (입력 대기):",
            State.GENERATING: "답변 작성 중:",
            State.WAITING: "한도 초과 대기:",
            State.RESUMING: "작업 지시 중:"
        }
        
        for state, label_text in labels.items():
            line_edit = QLineEdit(current_messages[state])
            self.inputs[state] = line_edit
            form_layout.addRow(label_text, line_edit)
            
        msg_group.setLayout(form_layout)
        layout.addWidget(msg_group)
        
        info_label = QLabel("※ '{mode}'는 ON/OFF로, '{time}'은 시간으로 자동 치환됩니다.")
        info_label.setStyleSheet("color: #888888; font-size: 9pt;")
        layout.addWidget(info_label)

        # 화면 인식 신뢰도 설정 그룹 (제안 #2·#3)
        conf_group = QGroupBox("화면 인식 신뢰도 (0.40~0.99 · 높을수록 엄격, 오탐↓ 미탐↑)")
        conf_form = QFormLayout()
        self.conf_inputs = {}
        conf_labels = {
            "generating": "답변 작성 중(■) 감지:",
            "ready": "입력 대기(↵) 감지:",
            "limit": "사용량 한도 화면 감지:",
        }
        for key, label_text in conf_labels.items():
            spin = QDoubleSpinBox()
            spin.setRange(0.40, 0.99)
            spin.setSingleStep(0.01)
            spin.setDecimals(2)
            spin.setValue(float(current_conf.get(key, 0.80)))
            self.conf_inputs[key] = spin
            conf_form.addRow(label_text, spin)
        conf_group.setLayout(conf_form)
        layout.addWidget(conf_group)

        conf_info = QLabel("※ 생성/대기 감지는 이 값을 기준으로 흑백·컬러 신뢰도를 단계적으로 낮춰가며 재시도합니다.")
        conf_info.setStyleSheet("color: #888888; font-size: 9pt;")
        conf_info.setWordWrap(True)
        layout.addWidget(conf_info)

        # 스마트 프롬프트 설정 그룹
        prompt_group = QGroupBox("스마트 프롬프트 설정 (입력창 비어있을 때 자동 전송할 텍스트)")
        prompt_layout = QVBoxLayout()
        self.prompt_edit = QPlainTextEdit(current_prompt)
        prompt_layout.addWidget(self.prompt_edit)
        prompt_group.setLayout(prompt_layout)
        layout.addWidget(prompt_group)
        
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
        
    def get_values(self):
        new_messages = {state: edit.text() for state, edit in self.inputs.items()}
        new_prompt = self.prompt_edit.toPlainText()
        new_conf = {key: spin.value() for key, spin in self.conf_inputs.items()}
        return new_messages, new_prompt, new_conf

class ClaudeWorker(QThread):
    toast_signal = pyqtSignal(str)
    continuous_mode_changed = pyqtSignal(bool)  # 자동 정지 시 GUI 체크박스 동기화용

    def __init__(self):
        super().__init__()
        self.state = State.IDLE
        self.running = True
        self.target_time = None
        self.continuous_mode = False
        self.click_y_offset = 110

        # [제안 #2·#3] 화면 인식 신뢰도 (환경 설정창에서 조정)
        self.generating_confidence = GENERATING_CONFIDENCE
        self.ready_confidence = READY_CONFIDENCE
        self.limit_confidence = LIMIT_CONFIDENCE

        self.last_status_msg = ""
        self.status_messages = DEFAULT_MESSAGES.copy()
        self.smart_prompt = SMART_PROMPT

    def run(self):
        self.running = True
        self.state = State.IDLE
        self.last_status_msg = ""
        
        logging.info("=========================================")
        logging.info(f"{APP_NAME} 감시 스레드를 시작합니다.")
        # 리소스 파일 체크
        self._check_resources()
        logging.info("=========================================")

        while self.running:
            try:
                self._update_state()
            except pyautogui.FailSafeException:
                logging.error("마우스가 화면 모서리로 이동하여 FailSafe가 발동되었습니다. 루프를 재개합니다.")
            except Exception as e:
                logging.error(f"루프 실행 중 에러 발생: {e}")
            
            self._interruptible_sleep(CHECK_INTERVAL)

    def stop(self):
        self.running = False

    def _check_resources(self):
        """이미지 파일 누락 여부 확인"""
        missing = []
        if not os.path.exists(resource_path("generating.png")):
            missing.append("generating.png")
            
        # [수정] limit_warning으로 시작하는 파일이 1개라도 있는지 검사
        limit_imgs = glob.glob(resource_path("limit_warning*.png"))
        if not limit_imgs:
            missing.append("limit_warning*.png (최소 1개 이상 필요)")
            
        if missing:
            logging.warning(f"⚠️ 경고: 이미지 인식용 파일이 누락되었습니다: {', '.join(missing)}")
            logging.warning("해당 기능(답변 중 감지, 한도 초과 감지)이 정상 작동하지 않을 수 있습니다.")

    def _interruptible_sleep(self, seconds):
        end = time.time() + seconds
        while self.running and time.time() < end:
            time.sleep(0.1)

    def get_claude_window(self):
        if sys.platform != 'win32':
            logging.error("pygetwindow는 현재 Windows만 공식 지원합니다.")
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
            logging.debug(f"창 검색 중 오류 발생: {e}")

        for w in candidates:
            if w.title == TARGET_WINDOW_TITLE:
                return w
        return candidates[0] if candidates else None

    def _log_status_change(self):
        current_status = ""
        
        if self.state == State.IDLE:
            current_status = self.status_messages[State.IDLE]
        elif self.state == State.MONITORING:
            mode_str = "ON" if self.continuous_mode else "OFF"
            current_status = self.status_messages[State.MONITORING].replace("{mode}", mode_str)
        elif self.state == State.GENERATING:
            current_status = self.status_messages[State.GENERATING]
        elif self.state == State.WAITING:
            target_str = self.target_time.strftime('%H:%M:%S') if self.target_time else '미정'
            current_status = self.status_messages[State.WAITING].replace("{time}", target_str)
        elif self.state == State.RESUMING:
            current_status = self.status_messages[State.RESUMING]

        if current_status != self.last_status_msg:
            logging.info(f"💡 [상태 변경] {current_status}")
            self.last_status_msg = current_status

    def _update_state(self):
        claude_win = self.get_claude_window()
        is_claude_open = claude_win is not None

        # [제안 #1] 이번 주기에 쓸 화면을 한 번만 캡처해 모든 템플릿 매칭에 재사용한다.
        #           (창마다 generating*·limit_warning*·ready* 여러 장을 매번 새로 캡처하던 비용 제거)
        #           WAITING 단계는 아래에서 frame을 쓰지 않으므로 캡처 자체를 건너뛴다.
        frame = self._grab_window_frame(claude_win) if (is_claude_open and self.state != State.WAITING) else None

        # [개선] 1. 창이 열려있고 이미 대기(WAITING) 중인 상태가 아니라면, 한도 초과 여부부터 최우선 검사
        if is_claude_open and self.state != State.WAITING:
            is_limited = self._check_for_rate_limit(claude_win, frame)
            if is_limited:
                self._log_status_change()
                return

        # 2. 한도 초과가 아닐 때만 답변 생성 중/완료 여부 검사
        #    생성(■)과 완료(↵) 두 신호를 함께 보고, ready 템플릿이 있으면
        #    완료는 ready(↵)가 보일 때만 '확정'한다. 둘 다 애매하면 상태를 유지(오판 방지).
        is_generating = False
        done_confirmed = True
        if is_claude_open and self.state in (State.MONITORING, State.GENERATING):
            is_generating = self._check_is_generating(claude_win, frame)
            if not is_generating and self._has_ready_templates():
                done_confirmed = self._check_is_ready(claude_win, frame)

        self._log_status_change()

        # --- 상태 머신 전환 로직 ---
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
                # 생성(■)도 완료(↵)도 확인 안 됨 → 모호 상태. 다음 주기까지 현재 상태 유지.
                pass
            else:
                if self.state == State.GENERATING:
                    logging.info("답변 작성이 완료되었습니다! 5초 대기 후 다음 단계를 진행합니다.")
                    self._interruptible_sleep(5)
                    if self.continuous_mode and self._check_loop_stable(claude_win):
                        logging.info("🛑 계획 안정(LOOPSTABLE) 감지 — 연속 모드를 끄고 대기합니다.")
                        self.continuous_mode = False
                        self.continuous_mode_changed.emit(False)
                        self._notify_stable(claude_win)
                        self.state = State.MONITORING
                    elif self.continuous_mode:
                        self.state = State.RESUMING
                    else:
                        self.state = State.MONITORING
                elif self.state == State.MONITORING:
                    if self.continuous_mode:
                        logging.info("무한 연속 모드 ON: 즉시 다음 작업을 지시합니다.")
                        self.state = State.RESUMING

        elif self.state == State.WAITING:
            if not is_claude_open:
                logging.info("대기 중 클로드 창이 닫혔습니다. 대기를 취소합니다.")
                self.target_time = None
                self.state = State.IDLE
                return

            if not self.target_time:
                self.state = State.MONITORING
                return

            # 한도 화면이 이미 사라졌다면(=리셋됨) 예정 시각과 무관하게 즉시 재개한다.
            # OCR이 재개 시각을 잘못 읽어 미래(오후/내일)로 잡혀도 여기서 자가 복구된다.
            limit_pos, _ = self._locate_rate_limit(claude_win)
            if limit_pos is None:
                logging.info("한도 화면이 사라졌습니다(리셋 추정). 예정 시각과 무관하게 재개합니다.")
                self.target_time = None
                self.state = State.RESUMING
                return

            now = datetime.datetime.now()
            if now >= self.target_time:
                logging.info("대기 시간이 종료되었습니다. 재개를 시도합니다.")
                self.state = State.RESUMING

        elif self.state == State.RESUMING:
            self._execute_smart_resume()
            self.target_time = None
            self.state = State.MONITORING

    @staticmethod
    def _window_region(win):
        if not win:
            return None
        try:
            left = max(0, win.left)
            top = max(0, win.top)
            width = max(1, win.width)
            height = max(1, win.height)
            return (left, top, width, height)
        except Exception:
            return None

    def _grab_window_frame(self, win):
        """[제안 #1] 창 영역을 1회 캡처해 (PIL 이미지, 원점 left, 원점 top)을 돌려준다.
        캡처에 실패하면 None을 돌려줘 호출부가 기존 locateOnScreen 경로로 자연스럽게 폴백한다."""
        region = self._window_region(win)
        try:
            if region:
                left, top, _w, _h = region
                return (pyautogui.screenshot(region=region), left, top)
            return (pyautogui.screenshot(), 0, 0)
        except Exception as e:
            logging.debug(f"주기 화면 캡처 실패(개별 매칭으로 폴백): {e}")
            return None

    # [수정] 흑백(grayscale) 설정 + 주기당 캡처 재사용(frame) 파라미터 추가
    def _locate(self, img_path, win, confidence, use_grayscale=False, frame=None):
        try:
            if frame is not None:
                # [제안 #1] 이미 캡처해 둔 화면(haystack)에서 needle을 찾고,
                #           반환 좌표를 창 원점만큼 더해 절대 화면 좌표로 환산한다.
                shot, origin_x, origin_y = frame
                box = pyautogui.locate(img_path, shot, confidence=confidence, grayscale=use_grayscale)
                if box is None:
                    return None
                return ScreenBox(int(box.left) + origin_x, int(box.top) + origin_y,
                                 int(box.width), int(box.height))
            region = self._window_region(win)
            if region:
                return pyautogui.locateOnScreen(img_path, confidence=confidence, region=region, grayscale=use_grayscale)
            return pyautogui.locateOnScreen(img_path, confidence=confidence, grayscale=use_grayscale)
        except Exception:
            return None

    def _locate_rate_limit(self, claude_win, frame=None):
        """한도 초과 이미지가 화면에 있으면 (pos, 파일명), 없으면 (None, None). 부작용 없음."""
        for img_path in glob.glob(resource_path("limit_warning*.png")):
            # 오탐지를 원천 차단하기 위해 흑백 인식을 끄고(컬러 비교), 신뢰도를 엄격하게(기본 0.9) 둔다.
            pos = self._locate(img_path, claude_win, confidence=self.limit_confidence,
                               use_grayscale=False, frame=frame)
            if pos:
                return pos, os.path.basename(img_path)
        return None, None

    def _check_for_rate_limit(self, claude_win, frame=None):
        pos, img_name = self._locate_rate_limit(claude_win, frame)
        if pos:
            logging.info(f"사용량 한도 도달 화면이 감지되었습니다! ({img_name}) OCR로 시간을 판독합니다.")
            self._setup_wait_timer(claude_win, pos)
            return True
        return False

    def _match_any(self, prefix: str, claude_win, base_conf: float, frame=None) -> bool:
        """resource의 <prefix>*.png 중 하나라도 화면에서 찾으면 True.
        흑백(base) → 흑백(base-0.10) → 컬러(base-0.08) 순으로 단계적으로 시도해 UI/배율 차이에 견딘다.
        (base_conf=0.8이면 기존과 동일한 0.8 → 0.70 → 0.72 사다리)"""
        ladder = (
            (True, base_conf),
            (True, max(0.40, round(base_conf - 0.10, 2))),
            (False, max(0.40, round(base_conf - 0.08, 2))),
        )
        for img_path in glob.glob(resource_path(prefix + "*.png")):
            for use_gray, conf in ladder:
                if self._locate(img_path, claude_win, conf, use_grayscale=use_gray, frame=frame) is not None:
                    return True
        return False

    def _check_is_generating(self, claude_win, frame=None):
        # 생성 중 = 정지 버튼(■, generating*.png) 이 보임
        return self._match_any("generating", claude_win, self.generating_confidence, frame)

    def _check_is_ready(self, claude_win, frame=None):
        # 완료/입력 대기 = 전송 버튼(↵, ready*.png) 이 보임
        return self._match_any("ready", claude_win, self.ready_confidence, frame)

    @staticmethod
    def _has_ready_templates() -> bool:
        return bool(glob.glob(resource_path("ready*.png")))

    @staticmethod
    def _ensure_ocr() -> bool:
        """easyocr Reader를 지연 초기화한다. 사용 가능하면 True, 불가/실패면 False.
        초기화에 실패하면 HAS_OCR을 꺼 이후 호출이 즉시 False를 반환하게 한다."""
        global HAS_OCR, ocr_reader
        if not HAS_OCR:
            return False
        if ocr_reader is None:
            try:
                logging.info("OCR 엔진을 초기화하는 중입니다... (최초 1회만 실행되며 약간의 시간이 소요될 수 있습니다)")
                ocr_reader = easyocr.Reader(['ko', 'en'])
            except Exception as e:
                logging.error(f"OCR 엔진 초기화 실패: {e}")
                HAS_OCR = False
                return False
        return True

    def _check_loop_stable(self, claude_win) -> bool:
        """입력창 바로 위(=응답 마지막 줄) 영역을 OCR해 STABLE_TOKEN이 있으면 True.
        OCR 불가 시에는 항상 False (자동 정지하지 않음)."""
        if claude_win is None or not self._ensure_ocr():
            return False
        try:
            # 입력창 클릭 지점(win.bottom - click_y_offset) 위쪽을 넉넉히 캡처
            # (응답 마지막 줄 위치가 입력창 높이·여백에 따라 달라질 수 있어 띠를 크게 잡음)
            band_h = 420
            bottom = int(claude_win.bottom - self.click_y_offset)
            top = int(max(0, bottom - band_h))
            left = int(max(0, claude_win.left + 20))
            width = int(max(1, claude_win.width - 40))
            height = int(max(1, bottom - top))
            screenshot = pyautogui.screenshot(region=(left, top, width, height))
            results = ocr_reader.readtext(np.array(screenshot))
            raw = " ".join(t for _bbox, t, _p in results)
            # OCR 오인식 보정: 공백 제거, 대문자화, 0↔O 치환 후 알파벳만 남겨 비교
            normalized = re.sub(r'[^A-Z]', '', "".join(raw.split()).upper().replace("0", "O"))
            found = STABLE_TOKEN in normalized
            logging.info(f"🔍 [안정 OCR] '{raw.strip()[:120]}' → {'감지' if found else '미감지'}")
            return found
        except Exception as e:
            logging.debug(f"안정 토큰 OCR 실패: {e}")
            return False

    def _notify_stable(self, claude_win):
        """정지 시 Claude 창을 캡처해 텔레그램으로 보낸다 (설정 있을 때만).
        [제안 #4] 캡처 이미지에 더해, 거기서 OCR로 추출한 '백로그 텍스트'도 함께 동봉해
        모바일에서 이미지를 확대하지 않고도 백로그 내용을 바로 읽을 수 있게 한다."""
        # 텔레그램이 설정되지 않았으면 캡처/OCR 비용을 들이지 않고 즉시 종료한다.
        if not load_telegram_config():
            return
        try:
            region = self._window_region(claude_win)
            shot = pyautogui.screenshot(region=region) if region else pyautogui.screenshot()
            tmp_path = os.path.join(tempfile.gettempdir(), "autopilot_stable.png")
            shot.save(tmp_path)
            caption = f"[Auto-Pilot] 계획 안정 — 정지됨 ({datetime.datetime.now():%Y-%m-%d %H:%M})"
            send_telegram_photo(tmp_path, caption)
            # 같은 캡처를 재사용해 백로그 텍스트를 뽑아 별도 메시지로 동봉(캡션 1024자 제한 회피).
            backlog_text = self._extract_backlog_text(shot)
            if backlog_text:
                send_telegram_text("[Auto-Pilot] 📋 백로그\n" + backlog_text[:3500])
        except Exception as e:
            logging.error(f"정지 알림 처리 실패: {e}")

    def _extract_backlog_text(self, screenshot) -> str:
        """[제안 #4] 정지 캡처 이미지에서 백로그로 보이는 텍스트를 OCR로 추출한다.
        OCR 불가/실패 시 빈 문자열을 돌려줘 알림은 이미지만 전송된다(기존 동작 유지)."""
        if screenshot is None or not self._ensure_ocr():
            return ""
        try:
            results = ocr_reader.readtext(np.array(screenshot))
            lines = [t.strip() for _bbox, t, _p in results if t and t.strip()]
            return "\n".join(lines).strip()
        except Exception as e:
            logging.debug(f"백로그 OCR 실패: {e}")
            return ""

    def _setup_wait_timer(self, claude_win, limit_pos):
        # OCR을 쓸 수 없으면(미설치/초기화 실패) 시간 판독을 포기하고 안전 대기 시간을 적용한다.
        if not self._ensure_ocr():
            logging.error("easyocr를 사용할 수 없어 OCR 시간 판독을 건너뜁니다. (필요 시: pip install easyocr numpy)")
            logging.info(f"안전 대기 시간({FALLBACK_WAIT_MINUTES}분) 적용...")
            self.target_time = datetime.datetime.now() + datetime.timedelta(minutes=FALLBACK_WAIT_MINUTES)
            self.state = State.WAITING
            return

        # [변경] 마우스 클릭/클립보드 복사를 완전히 제거하고 화면 캡처 OCR로 교체
        # limit_pos(한도초과 아이콘 위치)를 기준으로 우측으로 500px, 상하로 약간 여유를 두고 캡처 영역 설정
        left = int(limit_pos.left)
        top = int(max(0, limit_pos.top - 15))
        width = int(limit_pos.width + 500)
        height = int(limit_pos.height + 30)
        capture_region = (left, top, width, height)

        logging.info("OCR을 위해 알림 텍스트 영역을 캡처합니다...")

        try:
            # 1. 화면 캡처 및 numpy 배열 변환
            screenshot = pyautogui.screenshot(region=capture_region)
            img_np = np.array(screenshot)
            
            # 2. 이미지에서 텍스트 추출
            results = ocr_reader.readtext(img_np)
            
            # 3. 추출된 텍스트 조립 (인식된 글자 덩어리들을 띄어쓰기로 연결)
            copied_text = " ".join([text for bbox, text, prob in results])
                
            debug_text = copied_text.replace('\n', ' ').strip()
            logging.info(f"🔍 [OCR 결과]: {debug_text if debug_text else '텍스트를 찾지 못함'}")
            
            # 4. 시간 파싱
            parsed_time = parse_target_time(copied_text)

        except Exception as e:
            logging.error(f"OCR 실행 실패: {e}")
            parsed_time = None

        if parsed_time:
            self.target_time = parsed_time
            logging.info(f"OCR 분석으로 재개 시간을 파악했습니다: {self.target_time.strftime('%Y-%m-%d %H:%M:%S')}")
        else:
            self.target_time = datetime.datetime.now() + datetime.timedelta(minutes=FALLBACK_WAIT_MINUTES)
            logging.info(f"시간 파싱 실패. 안전 대기 시간({FALLBACK_WAIT_MINUTES}분) 적용: {self.target_time.strftime('%Y-%m-%d %H:%M:%S')}")
            
        self.state = State.WAITING

    def _focus_claude_window(self):
        win = self.get_claude_window()
        if win:
            try:
                if win.isMinimized:
                    win.restore()
                win.activate()
                # 윈도우가 완전히 활성화될 때까지 약간 대기
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
            
        # 슬립 직후 사용자가 다른 창을 띄웠는지 재검증
        if win.title != gw.getActiveWindow().title:
            logging.warning("사용자 개입 감지: 클로드 창이 포커스를 잃어 자동 전송을 보류합니다.")
            return

        original_clipboard = safe_clipboard_paste()
        try:
            pyperclip.copy("")

            center_x = win.left + (win.width // 2)
            bottom_y = win.bottom - self.click_y_offset
            pyautogui.click(x=center_x, y=bottom_y)
            time.sleep(0.5)

            pyautogui.hotkey('ctrl', 'a')
            time.sleep(0.2)
            pyautogui.hotkey('ctrl', 'c')
            time.sleep(0.5)

            current_input = safe_clipboard_paste().strip()

            if len(current_input) > MAX_INPUT_LEN:
                logging.warning(
                    f"입력창에서 비정상적으로 긴 텍스트({len(current_input)}자)가 감지되었습니다. "
                    "입력창이 아닌 대화 영역을 클릭했을 가능성이 있어 전송을 건너뜁니다. "
                    "위치 테스트로 Y 오프셋을 점검하세요."
                )
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
                if original_clipboard:
                    pyperclip.copy(original_clipboard)
                else:
                    pyperclip.copy("")
            except Exception as e:
                logging.debug(f"클립보드 복구 실패: {e}")

        self._interruptible_sleep(5.0)

# ---------------------------------------------------------
# 6. 메인 GUI 창 (Main Window)
# ---------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{VERSION}")
        self.setWindowIcon(app_icon())
        # UI 요소들이 넉넉하게 배치되도록 높이를 약간 더 늘림
        self.resize(850, 520)
        self.tray = None
        self._force_quit = False

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(12)  # 위젯 간 간격 넓힘
        main_layout.setContentsMargins(15, 15, 15, 15)

        # 1. 상단 툴바 (체크박스 및 환경설정/로그지우기)
        top_bar = QHBoxLayout()
        
        # 1-1. 좌측 토글 스위치 그룹
        toggle_layout = QHBoxLayout()
        self.cb_continuous = QCheckBox("연속 작업 모드 (ON/OFF)")
        self.cb_continuous.setFont(QFont("Malgun Gothic", 10, QFont.Weight.Bold))
        self.cb_continuous.stateChanged.connect(self.toggle_continuous_mode)
        toggle_layout.addWidget(self.cb_continuous)

        self.cb_always_on_top = QCheckBox("📌 항상 위 고정")
        self.cb_always_on_top.setFont(QFont("Malgun Gothic", 10, QFont.Weight.Bold))
        self.cb_always_on_top.stateChanged.connect(self.toggle_always_on_top)
        self.cb_always_on_top.setChecked(False)  # 시작 시 항상 위 고정 해제
        toggle_layout.addWidget(self.cb_always_on_top)
        
        top_bar.addLayout(toggle_layout)
        top_bar.addStretch() # 가운데 여백으로 양쪽 정렬
        
        # 1-2. 우측 도구 버튼 그룹
        tool_layout = QHBoxLayout()
        self.btn_settings = QPushButton("⚙️ 환경 설정")
        self.btn_settings.setStyleSheet("""
            QPushButton {
                background-color: #34495e; color: white; font-weight: bold; 
                padding: 6px 12px; border-radius: 4px;
            }
            QPushButton:hover { background-color: #2c3e50; }
        """)
        self.btn_settings.clicked.connect(self.open_settings)
        tool_layout.addWidget(self.btn_settings)

        self.btn_clear = QPushButton("🗑️ 로그 지우기")
        self.btn_clear.setStyleSheet("""
            QPushButton {
                background-color: #7f8c8d; color: white; font-weight: bold;
                padding: 6px 12px; border-radius: 4px;
            }
            QPushButton:hover { background-color: #95a5a6; }
        """)
        self.btn_clear.clicked.connect(self.clear_logs)
        tool_layout.addWidget(self.btn_clear)

        self.btn_telegram = QPushButton("📨 텔레그램")
        self.btn_telegram.setStyleSheet("""
            QPushButton {
                background-color: #2a7ab0; color: white; font-weight: bold;
                padding: 6px 12px; border-radius: 4px;
            }
            QPushButton:hover { background-color: #3498db; }
        """)
        self.btn_telegram.clicked.connect(self.open_telegram_settings)
        tool_layout.addWidget(self.btn_telegram)

        top_bar.addLayout(tool_layout)
        main_layout.addLayout(top_bar)

        # 2. 오프셋 설정 및 테스트 그룹 (시각적 분리를 위해 얇은 박스 처리)
        offset_frame = QWidget()
        offset_frame.setStyleSheet("""
            QWidget {
                background-color: rgba(100, 100, 100, 0.1);
                border-radius: 6px;
            }
        """)
        offset_layout = QHBoxLayout(offset_frame)
        offset_layout.setContentsMargins(10, 8, 10, 8)
        
        lbl_offset = QLabel("📍 입력창 Y좌표 오프셋 (창 하단기준):")
        lbl_offset.setStyleSheet("background: transparent; font-weight: bold;")
        offset_layout.addWidget(lbl_offset)
        
        self.spin_offset = QSpinBox()
        self.spin_offset.setRange(10, 800)
        self.spin_offset.setValue(110)
        self.spin_offset.setSuffix(" px")
        self.spin_offset.setStyleSheet("""
            QSpinBox {
                background-color: #2c3e50;
                color: #ffffff;
                border: 1px solid #34495e;
                border-radius: 4px;
                padding: 3px 8px;
                font-size: 10pt;
                font-weight: bold;
                min-height: 20px;
            }
            /* 포커스 되었을 때의 테두리를 튀지 않는 차분한 톤으로 수정 */
            QSpinBox:focus {
                border: 1px solid #5d6d7e;
                background-color: #1a252f;
            }
            QSpinBox::up-button {
                subcontrol-origin: border;
                subcontrol-position: top right;
                width: 16px;
                border-left: 1px solid #34495e;
                border-top-right-radius: 3px;
                background-color: #2c3e50;
            }
            QSpinBox::down-button {
                subcontrol-origin: border;
                subcontrol-position: bottom right;
                width: 16px;
                border-left: 1px solid #34495e;
                border-bottom-right-radius: 3px;
                background-color: #2c3e50;
            }
            QSpinBox::up-button:hover, QSpinBox::down-button:hover {
                background-color: #34495e;
            }
            QSpinBox::up-button:pressed, QSpinBox::down-button:pressed {
                background-color: #1a252f;
            }
        """)
        self.spin_offset.valueChanged.connect(self.update_offset)
        offset_layout.addWidget(self.spin_offset)
        
        self.btn_test = QPushButton("🎯 위치 테스트")
        # [수정] 테스트 버튼의 배경색을 QSpinBox와 동일한 남색(#2c3e50)으로 통일
        self.btn_test.setStyleSheet("""
            QPushButton {
                background-color: #2c3e50; color: white; font-weight: bold; 
                font-size: 10pt; padding: 4px 12px; border-radius: 4px; border: none;
                min-height: 20px;
            }
            QPushButton:hover { background-color: #34495e; }
        """)
        self.btn_test.clicked.connect(self.test_click_position)
        offset_layout.addWidget(self.btn_test)
        offset_layout.addStretch()
        
        main_layout.addWidget(offset_frame)

        # 3. 로그 콘솔 영역
        self.log_console = QPlainTextEdit()
        self.log_console.setReadOnly(True)
        # 로그 콘솔 내 텍스트 자동 줄바꿈 방지 (가로 스크롤바 생성)
        self.log_console.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.log_console.setStyleSheet("""
            QPlainTextEdit {
                background-color: #1E1E1E;
                color: #D4D4D4;
                font-family: Consolas, monospace;
                font-size: 10pt;
                padding: 10px;
                border: 2px solid #2c3e50;
                border-radius: 6px;
            }
        """)
        main_layout.addWidget(self.log_console)

        # 4. 하단 핵심 액션 버튼 (크고 눈에 띄게)
        action_bar = QHBoxLayout()
        action_bar.setSpacing(15)
        
        self.btn_start = QPushButton("▶ 감시 시작")
        self.btn_start.setStyleSheet("""
            QPushButton {
                background-color: #2ecc71; color: white; font-weight: bold; 
                font-size: 13pt; padding: 12px; border-radius: 6px;
            }
            QPushButton:hover { background-color: #27ae60; }
            QPushButton:disabled { background-color: #95a5a6; }
        """)
        self.btn_start.clicked.connect(self.start_worker)
        action_bar.addWidget(self.btn_start)

        self.btn_stop = QPushButton("■ 감시 중지")
        self.btn_stop.setStyleSheet("""
            QPushButton {
                background-color: #e74c3c; color: white; font-weight: bold; 
                font-size: 13pt; padding: 12px; border-radius: 6px;
            }
            QPushButton:hover { background-color: #c0392b; }
            QPushButton:disabled { background-color: #95a5a6; }
        """)
        self.btn_stop.clicked.connect(self.stop_worker)
        self.btn_stop.setEnabled(False)
        action_bar.addWidget(self.btn_stop)

        main_layout.addLayout(action_bar)

        self.worker = ClaudeWorker()
        self.worker.toast_signal.connect(self.show_toast)
        self.worker.continuous_mode_changed.connect(self._sync_continuous_checkbox)
        self.setup_logging()
        self.setup_tray()

        # 창 시작 위치: 우측 하단 (작업표시줄 바로 위)
        screen_geom = QApplication.primaryScreen().availableGeometry()
        # 윈도우 제목 표시줄 두께(약 30~40px)를 고려하여 Y좌표 여백을 50px로 넉넉하게 수정
        x = screen_geom.right() - self.width() - 10
        y = screen_geom.bottom() - self.height() - 50
        self.move(x, y)

    def setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = None
            return

        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip(APP_NAME)

        menu = QMenu()
        act_show = menu.addAction("창 보이기")
        act_show.triggered.connect(self.show_normal_from_tray)
        act_hide = menu.addAction("창 숨기기")
        act_hide.triggered.connect(self.hide)
        menu.addSeparator()
        act_quit = menu.addAction("종료")
        act_quit.triggered.connect(self.quit_from_tray)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.on_tray_activated)
        self.tray.show()

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_normal_from_tray()

    def show_normal_from_tray(self):
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def quit_from_tray(self):
        self._force_quit = True
        self.close()

    def update_offset(self, value):
        self.worker.click_y_offset = value
        logging.info(f"클릭 위치 오프셋이 {value}px 로 변경되었습니다.")

    def open_settings(self):
        current_conf = {
            "generating": self.worker.generating_confidence,
            "ready": self.worker.ready_confidence,
            "limit": self.worker.limit_confidence,
        }
        dialog = MessageSettingsDialog(self.worker.status_messages, self.worker.smart_prompt, current_conf, self)
        if dialog.exec():
            new_messages, new_prompt, new_conf = dialog.get_values()
            self.worker.status_messages = new_messages
            self.worker.smart_prompt = new_prompt
            self.worker.generating_confidence = new_conf["generating"]
            self.worker.ready_confidence = new_conf["ready"]
            self.worker.limit_confidence = new_conf["limit"]
            logging.info("⚙️ 상황별 메시지·프롬프트·화면 인식 신뢰도 설정이 저장되었습니다.")

    def open_telegram_settings(self):
        TelegramSettingsDialog(self).exec()

    def test_click_position(self):
        win = self.worker.get_claude_window()
        if not win:
            logging.error("클로드 창을 찾을 수 없습니다. 창이 켜져 있는지 확인하세요.")
            return

        try:
            if win.isMinimized:
                win.restore()
            win.activate()
            time.sleep(0.3)

            center_x = win.left + (win.width // 2)
            bottom_y = win.bottom - self.spin_offset.value()

            logging.info(f"테스트 좌표 이동 중... (X: {center_x}, Y: {bottom_y})")
            pyautogui.moveTo(center_x, bottom_y, duration=0.5)
            pyautogui.click()
            
            logging.info("테스트 클릭이 완료되었습니다. 엉뚱한 곳을 클릭했다면 오프셋 수치를 조절해보세요.")
        except Exception as e:
            logging.error(f"테스트 클릭 실패: {e}")

    def setup_logging(self):
        logger = logging.getLogger()
        logger.setLevel(logging.INFO)
        
        gui_handler = QTextEditLogger(self)
        gui_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt="%H:%M:%S"))
        logger.addHandler(gui_handler)
        
        # [완료] app.log 크기 기준 롤오버 — 2MB 도달 시 회전하고 백업 3개까지 보관해 무한 증식 방지
        file_handler = logging.handlers.RotatingFileHandler(
            "app.log", maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding='utf-8')
        file_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
        logger.addHandler(file_handler)

    def append_log(self, msg, record):
        if record.levelno >= logging.ERROR:
            formatted_msg = f"<span style='color: #ff5555;'>{msg}</span>"
        elif record.levelno == logging.WARNING:
            formatted_msg = f"<span style='color: #ffb86c;'>{msg}</span>"
        elif "감지되었습니다" in msg or "완료되었습니다" in msg or "이동 중" in msg:
            formatted_msg = f"<span style='color: #50fa7b;'>{msg}</span>"
        elif "[상태 변경]" in msg:
            formatted_msg = f"<span style='color: #8be9fd;'>{msg}</span>"
        else:
            formatted_msg = f"<span style='color: #d4d4d4;'>{msg}</span>"

        self.log_console.appendHtml(formatted_msg)
        scrollbar = self.log_console.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def clear_logs(self):
        self.log_console.clear()

    def toggle_always_on_top(self, _state):
        is_checked = self.cb_always_on_top.isChecked()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, is_checked)
        self.show()  # 윈도우 속성(플래그)이 변경되면 화면에 다시 렌더링해야 함

    def toggle_continuous_mode(self, _state):
        is_checked = self.cb_continuous.isChecked()
        self.worker.continuous_mode = is_checked
        logging.info(f"무한 연속 작업 모드가 {'ON' if is_checked else 'OFF'} 되었습니다.")

    def _sync_continuous_checkbox(self, on: bool):
        """워커가 자동으로 연속 모드를 끌 때 GUI 체크박스를 신호 루프 없이 동기화한다."""
        self.cb_continuous.blockSignals(True)
        self.cb_continuous.setChecked(on)
        self.cb_continuous.blockSignals(False)

    def start_worker(self):
        if self.worker.isRunning():
            return
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.worker.start()

    def stop_worker(self):
        self.worker.stop()
        self.worker.wait()
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        logging.info("감시가 중지되었습니다.")

    def show_toast(self, message):
        self.toast = ToastNotification(message)
        self.toast.show()

    def closeEvent(self, event):
        if getattr(self, "tray", None) and not getattr(self, "_force_quit", False):
            event.ignore()
            self.hide()
            self.tray.showMessage(
                APP_NAME,
                "백그라운드에서 계속 실행 중입니다. 트레이 아이콘에서 종료할 수 있습니다.",
                QSystemTrayIcon.MessageIcon.Information,
                3000,
            )
            return

        if self.worker.isRunning():
            self.worker.stop()
            self.worker.wait()
        if getattr(self, "tray", None):
            self.tray.hide()
        super().closeEvent(event)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setWindowIcon(app_icon())

    # [수정] 앱 전체의 기본 파란색 포인트를 스핀박스/버튼 배경색인 남색(#2c3e50)으로 일괄 변경
    # (이렇게 하면 체크박스를 체크했을 때의 파란 배경도 동일한 남색으로 예쁘게 바뀝니다!)
    palette = app.palette()
    palette.setColor(palette.ColorRole.Highlight, QColor("#2c3e50"))
    app.setPalette(palette)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())