import sqlite3
from datetime import datetime, timezone, timedelta

import pytest

from reviewlab.repositories import library as storage
from reviewlab.repositories.library import Library
from reviewlab.services.transfer import export_backup, restore


def backup_rows(library):
    with sqlite3.connect(library.automatic_backups()[0]) as db:
        db.row_factory = sqlite3.Row
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
        return [dict(r) for r in db.execute('SELECT * FROM sources')]


def test_mutations_update_daily_backup(service, library, source, event):
    assert backup_rows(library) == []
    sid = service.save(source, [library.tags()[0]['id']])
    assert backup_rows(library)[0]['title'] == source['title']
    service.save(dict(source, title='수정한 자료'), source_id=sid)
    assert backup_rows(library)[0]['title'] == '수정한 자료'
    service.verify(sid, event, True)
    service.add_tag('새 태그')
    tag_id = next(t['id'] for t in library.tags() if t['name'] == '새 태그')
    service.rename_tag(tag_id, '새 이름')
    with sqlite3.connect(library.automatic_backups()[0]) as db:
        assert db.execute('SELECT status FROM verification_events').fetchone()[0] == '검증 완료'
        assert db.execute('SELECT name FROM tags WHERE id=?', (tag_id,)).fetchone()[0] == '새 이름'
    service.delete(sid, True)
    assert backup_rows(library) == []
    assert len(library.automatic_backups()) == 1
    assert len(list((library.path.parent / 'backups').glob('before-change-*.sqlite3'))) == 1


def test_restore_updates_backup(tmp_path, service, library, source):
    service.save(source)
    target = Library(tmp_path / 'target' / 'reviewlab.sqlite3', 'actual')
    restore(target, export_backup(library), True)
    assert backup_rows(target)[0]['title'] == source['title']


def test_failed_backup_preserves_database_and_previous_copy(service, library, source, monkeypatch):
    sid = service.save(source)
    previous = library.automatic_backups()[0].read_bytes()
    def denied(*args):
        raise PermissionError('test-only denied replace')
    monkeypatch.setattr(storage.os, 'replace', denied)
    service.save(dict(source, title='백업 실패에도 DB 저장'), source_id=sid)
    assert service.search()[0]['title'] == '백업 실패에도 DB 저장'
    assert library.backup_warning
    assert library.automatic_backups()[0].read_bytes() == previous
    assert list(library.automatic_backups()[0].parent.glob('.pending-*')) == []
    monkeypatch.undo()
    library.automatic_backup(only_if_needed=True)
    assert not library.backup_warning
    assert backup_rows(library)[0]['title'] == '백업 실패에도 DB 저장'


def test_daily_rollover_retains_30_auto_files_only(library, monkeypatch):
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    old_dates = [(tomorrow - timedelta(days=90-i)).date().isoformat() for i in range(31)]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return tomorrow
    monkeypatch.setattr(storage, 'datetime', Clock)
    directory = library.path.parent / 'backups' / 'automatic'
    # 이번 날짜보다 앞선 사본 31개와 보존해야 하는 사용자 파일.
    for day in old_dates:
        (directory / f'auto-{day}.sqlite3').write_bytes(b'old-test-copy')
    user_file = directory / 'keep-notes.txt'
    user_file.write_text('보존')
    manual = library.snapshot()
    library.automatic_backup()
    files = library.automatic_backups()
    assert len(files) == 30
    assert files[0].name == f'auto-{tomorrow.date().isoformat()}.sqlite3'
    assert user_file.read_text() == '보존'
    assert manual.exists()
    assert not (directory / f'auto-{old_dates[0]}.sqlite3').exists()


def test_daily_startup_reuses_valid_copy_and_creates_missing_one(library):
    target = library.automatic_backups()[0]
    previous_mtime = target.stat().st_mtime_ns
    library.automatic_backup(only_if_needed=True)
    assert target.stat().st_mtime_ns == previous_mtime
    target.unlink()
    reopened = Library(library.path, library.mode)
    reopened.automatic_backup(only_if_needed=True)
    assert target.exists()
    assert backup_rows(reopened) == []


def test_rollback_does_not_replace_backup(library, service, source):
    sid = service.save(source)
    previous = library.automatic_backups()[0].read_bytes()
    with pytest.raises(sqlite3.IntegrityError):
        library.save(dict(source, title='롤백될 내용'), ['missing-tag'], sid)
    assert library.automatic_backups()[0].read_bytes() == previous
    assert backup_rows(library)[0]['title'] == source['title']


def test_mode_separation(tmp_path, library, service, source):
    service.save(source)
    example = Library(tmp_path / 'example' / 'reviewlab.sqlite3', 'example')
    assert backup_rows(example) == []
    assert backup_rows(library)[0]['title'] == source['title']


def test_daily_copy_can_recover_full_library(tmp_path, library, service, source, event):
    import shutil
    sid = service.save(source, [library.tags()[0]['id']])
    service.verify(sid, event, True)
    recovery_path = tmp_path / 'recovered' / 'actual' / 'reviewlab.sqlite3'
    recovery_path.parent.mkdir(parents=True)
    shutil.copy2(library.automatic_backups()[0], recovery_path)
    recovered = Library(recovery_path, 'actual')
    assert recovered.list_sources() == library.list_sources()
    assert recovered.tags() == library.tags()
