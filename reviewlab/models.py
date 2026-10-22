"""UI와 복원에서 공통으로 적용하는 입력 규칙."""
import re
from datetime import date, datetime, timezone
from urllib.parse import urlsplit
from uuid import UUID

TYPES = ("학술논문", "뉴스기사", "정부·공공기관 자료", "플랫폼 정책", "기타")
STATUSES = ("미검증", "원문 확인 중", "검증 완료", "사용 보류")
DEFAULT_TAGS = (
    "리뷰 이벤트 및 대가성 리뷰", "리뷰 삭제 및 노출 관리", "AI 생성 리뷰",
    "소비자 신뢰도", "구매 의사결정", "구매 후 만족도", "플랫폼 규제 및 관련 정책",
)
FIELDS = ("title", "authors", "publication_date", "source_type", "original_url", "notes", "purpose")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def text(value, label, required=False, limit=20000):
    if not isinstance(value, str) or "\x00" in value or len(value) > limit:
        raise ValueError(f"{label}: 올바른 텍스트를 입력하세요(최대 {limit:,}자).")
    if required and not value.strip():
        raise ValueError(f"{label}은(는) 필수 항목입니다.")
    return value


def valid_id(value):
    try:
        UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise ValueError("백업의 자료 식별자가 올바르지 않습니다.") from None
    return value


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("백업의 기록 시각이 올바르지 않습니다.") from None
    return value


def publication_date(value):
    text(value, "발행일", limit=10)
    if not value:
        return value
    if not re.fullmatch(r"\d{4}(-\d{2})?(-\d{2})?", value):
        raise ValueError("발행일은 YYYY, YYYY-MM 또는 YYYY-MM-DD 형식으로 입력하세요.")
    try:
        date.fromisoformat(value + {4: "-01-01", 7: "-01", 10: ""}[len(value)])
    except ValueError:
        raise ValueError("발행일에 실제로 존재하는 날짜를 입력하세요.") from None
    return value


def validate_source(data):
    if not isinstance(data, dict) or any(key not in data for key in FIELDS):
        raise ValueError("자료 필드가 누락되었습니다.")
    result = {key: text(data[key], key) for key in FIELDS}
    text(result["title"], "자료 제목", required=True, limit=1000)
    if result["source_type"] not in TYPES:
        raise ValueError("자료 유형을 선택하세요.")
    publication_date(result["publication_date"])
    raw = text(result["original_url"], "원문 URL", required=True, limit=4000)
    try:
        url = urlsplit(raw)
        valid = (url.scheme in ("http", "https") and url.hostname
                 and not url.username and not url.password
                 and not any(c.isspace() or ord(c) < 32 for c in raw))
        _ = url.port
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("원문 URL은 계정 정보 없는 http:// 또는 https:// 주소로 입력하세요.")
    return result


def validate_event(event):
    if not isinstance(event, dict):
        raise ValueError("검증 기록 형식이 올바르지 않습니다.")
    keys = ("status", "checked_on", "checked_by", "evidence", "memo")
    if any(key not in event for key in keys):
        raise ValueError("검증 기록 필드가 누락되었습니다.")
    result = {key: text(event[key], key) for key in keys}
    if result["status"] not in STATUSES:
        raise ValueError("검증 상태가 올바르지 않습니다.")
    if result["checked_on"]:
        publication_date(result["checked_on"])
        if len(result["checked_on"]) != 10:
            raise ValueError("검증 날짜는 YYYY-MM-DD 형식으로 입력하세요.")
        if date.fromisoformat(result["checked_on"]) > date.today():
            raise ValueError("검증 날짜는 미래 날짜일 수 없습니다.")
    if result["status"] == "검증 완료":
        for key, label in (("checked_on", "검증 날짜"), ("checked_by", "검증 담당자"),
                           ("evidence", "확인한 원문 또는 공식 자료"), ("memo", "검증 관련 메모")):
            text(result[key], label, required=True)
    return result


def tag_name(value):
    return text(value, "태그", required=True, limit=100)
