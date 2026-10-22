"""파일 경로는 UI, 저장소, 테스트가 공유한다."""
import os
from pathlib import Path

MODES = {"실제 연구자료": "actual", "예시·연습 자료": "example"}


def database_path(mode: str) -> Path:
    if mode not in MODES.values():
        raise ValueError("지원하지 않는 데이터 모드입니다.")
    root = Path(os.environ.get("REVIEWLAB_DATA_DIR", "~/.reviewlab")).expanduser()
    return root / mode / "reviewlab.sqlite3"
