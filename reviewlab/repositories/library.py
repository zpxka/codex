"""SQLite 저장 계층. 각 처리에서 연결을 닫고 UI와 SQL을 분리한다."""
import sqlite3
import os
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from reviewlab.models import DEFAULT_TAGS, FIELDS, now


class Library:
    def __init__(self, path: Path, mode: str):
        if mode not in ("actual", "example"):
            raise ValueError("데이터 모드가 올바르지 않습니다.")
        self.path, self.mode = Path(path), mode
        self.backup_warning = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        versions = [1]
        fresh = False
        with self.connect() as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not tables:
                fresh = True
                script = (Path(__file__).parents[1] / "db/migrations/001_initial.sql").read_text()
                # executescript는 암시적 COMMIT을 수행하므로 직접 BEGIN을 포함한다.
                db.executescript("BEGIN IMMEDIATE;\n" + script)
                db.execute("INSERT INTO project_metadata VALUES(1,?)", (mode,))
                db.execute("INSERT INTO schema_migrations VALUES(1,?)", (now(),))
                db.executemany("INSERT INTO tags VALUES(?,?)", [(str(uuid4()), t) for t in DEFAULT_TAGS])
            else:
                if not {"schema_migrations", "project_metadata"}.issubset(tables):
                    raise ValueError("ReviewLab 데이터베이스가 아닙니다. 데이터 경로를 확인하세요.")
                versions = [r[0] for r in db.execute("SELECT version FROM schema_migrations ORDER BY version")]
                if versions not in ([1], [1, 2]):
                    raise ValueError("지원하지 않는 DB 버전입니다. 원본을 보존하고 앱 버전을 확인하세요.")
                row = db.execute("SELECT data_kind FROM project_metadata WHERE id=1").fetchone()
                if not row or row[0] != mode:
                    raise ValueError("DB의 실제/예시 구분이 현재 모드와 다릅니다.")
                if versions == [1, 2] and not {
                    "review_projects", "review_imports", "reviews", "review_notes", "review_checks",
                    "review_source_links", "review_analysis_runs"
                }.issubset(tables):
                    raise ValueError("DB 버전과 리뷰 테이블 구조가 일치하지 않습니다.")
        if versions == [1]:
            self.migrate_reviews(backup=not fresh)

    def migrate_reviews(self, backup=True):
        # 기존 DB 사본 생성에 실패하면 마이그레이션을 시작하지 않는다.
        if backup:
            self.snapshot()
        script = (Path(__file__).parents[1] / "db/migrations/002_reviews.sql").read_text()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            versions = [r[0] for r in db.execute("SELECT version FROM schema_migrations ORDER BY version")]
            if versions == [1, 2]:
                return
            if versions != [1]:
                raise ValueError("지원하지 않는 DB 버전입니다.")
            # DDL도 같은 트랜잭션에서 처리한다. executescript의 암시적 COMMIT을 피한다.
            for statement in script.split(";"):
                if statement.strip():
                    db.execute(statement)
            db.execute("INSERT INTO schema_migrations VALUES(2,?)", (now(),))
            if db.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("마이그레이션 외래키 검사가 실패했습니다.")


    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        committed_changes = False
        try:
            with db:
                yield db
            committed_changes = db.total_changes > 0
        finally:
            db.close()
        if committed_changes:
            self.automatic_backup()

    def automatic_backup(self, only_if_needed=False):
        """커밋 후 최신 일별 사본을 원자적으로 교체한다. 백업 오류로 저장을 취소하지 않는다."""
        temporary = None
        try:
            directory = self.path.parent / "backups" / "automatic"
            directory.mkdir(parents=True, exist_ok=True)
            day = datetime.now(timezone.utc).date().isoformat()
            target = directory / f"auto-{day}.sqlite3"
            if (only_if_needed and target.exists()
                    and target.stat().st_mtime_ns >= self.path.stat().st_mtime_ns):
                return target
            temporary = directory / f".pending-{uuid4()}.sqlite3"
            source = sqlite3.connect(self.path, timeout=10)
            try:
                destination = sqlite3.connect(temporary)
                try:
                    source.backup(destination)
                    if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise sqlite3.DatabaseError("Backup integrity check failed")
                finally:
                    destination.close()
            finally:
                source.close()
            os.replace(temporary, target)
            # 자동 생성된 일별 파일만 정리한다. 삭제·복원 직전 사본과 사용자 파일은 건드리지 않는다.
            daily = sorted(p for p in directory.iterdir()
                           if re.fullmatch(r"auto-\d{4}-\d{2}-\d{2}\.sqlite3", p.name) and p.is_file())
            for old in daily[:-30]:
                old.unlink()
            self.backup_warning = None
            return target
        except (OSError, sqlite3.Error):
            self.backup_warning = "자동 백업을 완료하지 못했습니다. 자료 저장과는 별개입니다. 백업 폴더 권한·디스크 공간을 확인하고 복원용 CSV를 별도로 내려받으세요."
            return None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def automatic_backups(self):
        directory = self.path.parent / "backups" / "automatic"
        return sorted((p for p in directory.glob("auto-*.sqlite3")
                       if re.fullmatch(r"auto-\d{4}-\d{2}-\d{2}\.sqlite3", p.name)), reverse=True)

    def tags(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM tags ORDER BY name")]

    def add_tag(self, name):
        with self.connect() as db:
            db.execute("INSERT INTO tags VALUES(?,?)", (str(uuid4()), name))

    def rename_tag(self, tag_id, name):
        with self.connect() as db:
            if db.execute("UPDATE tags SET name=? WHERE id=?", (name, tag_id)).rowcount != 1:
                raise ValueError("태그가 없습니다. 목록을 새로고침하세요.")

    def list_sources(self):
        with self.connect() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM sources ORDER BY created_at,id")]
            tags = {}
            for row in db.execute("SELECT source_id,t.id,t.name FROM source_tags st JOIN tags t ON t.id=st.tag_id ORDER BY t.name"):
                tags.setdefault(row[0], []).append({"id": row[1], "name": row[2]})
            events = {}
            for row in db.execute("SELECT * FROM verification_events ORDER BY sequence"):
                event = dict(row)
                event.pop("sequence")
                events.setdefault(event.pop("source_id"), []).append(event)
            for row in rows:
                row["tags"] = tags.get(row["id"], [])
                row["events"] = events.get(row["id"], [])
                row["status"] = row["events"][-1]["status"] if row["events"] else "미검증"
            return rows

    @staticmethod
    def insert_source(db, record):
        columns = ("id",) + FIELDS + ("created_at", "updated_at")
        db.execute(f"INSERT INTO sources ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                   tuple(record[k] for k in columns))

    @staticmethod
    def insert_event(db, source_id, event):
        keys = ("id", "status", "checked_on", "checked_by", "evidence", "memo", "recorded_at")
        db.execute(f"INSERT INTO verification_events (source_id,{','.join(keys)}) VALUES ({','.join('?' for _ in range(8))})",
                   (source_id,) + tuple(event[k] for k in keys))

    def save(self, record, tag_ids, source_id=None):
        with self.connect() as db:
            if source_id:
                changed = db.execute(
                    f"UPDATE sources SET {','.join(k+'=?' for k in FIELDS)},updated_at=? WHERE id=?",
                    tuple(record[k] for k in FIELDS) + (now(), source_id)).rowcount
                if not changed:
                    raise ValueError("자료가 없습니다. 목록을 새로고침하세요.")
                db.execute("DELETE FROM source_tags WHERE source_id=?", (source_id,))
            else:
                source_id = str(uuid4())
                self.insert_source(db, dict(record, id=source_id, created_at=now(), updated_at=now()))
            db.executemany("INSERT INTO source_tags VALUES(?,?)", [(source_id, t) for t in tag_ids])
        return source_id

    def delete(self, source_id):
        with self.connect() as db:
            db.execute("DELETE FROM sources WHERE id=?", (source_id,))

    def add_event(self, source_id, event):
        with self.connect() as db:
            self.insert_event(db, source_id, dict(event, id=str(uuid4()), recorded_at=now()))

    def snapshot(self):
        directory = self.path.parent / "backups"
        directory.mkdir(exist_ok=True)
        target = directory / f"before-change-{uuid4()}.sqlite3"
        with self.connect() as db:
            destination = sqlite3.connect(target)
            try:
                db.backup(destination)
            finally:
                destination.close()
        return target
