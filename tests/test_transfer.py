import csv
import io
import json

import pytest

from reviewlab.repositories.library import Library
from reviewlab.services.transfer import (BACKUP_HEADER, MAX_BYTES, export_backup, export_list,
                                         parse_backup, preview_restore, restore, write_csv)


def mutate_backup(blob, mutation):
    rows = list(csv.DictReader(io.StringIO(blob.decode('utf-8-sig'))))
    payload = json.loads(rows[1]['payload'])
    mutation(payload)
    rows[1]['payload'] = json.dumps(payload, ensure_ascii=False)
    return write_csv(BACKUP_HEADER, [[r[k] for k in BACKUP_HEADER] for r in rows])


def test_lossless_backup_restore_and_idempotence(tmp_path, service, library, source, event):
    source['title'] = '=HYPERLINK("https://example.com")'
    source['notes'] = "'원문 그대로\n한글,쉼표"
    service.add_tag('미사용 태그')
    sid = service.save(source, [library.tags()[0]['id']])
    service.verify(sid, dict(event, status='원문 확인 중'))
    service.verify(sid, event, True)
    content = export_backup(library)
    assert content.startswith(b'\xef\xbb\xbf')
    target = Library(tmp_path / 'restored.sqlite3', 'actual')
    assert preview_restore(target, content) == dict(total=1, added=1, skipped=0)
    with pytest.raises(ValueError, match='동의'):
        restore(target, content)
    assert target.list_sources() == []
    result = restore(target, content, True)
    assert result['added'] == 1
    restored = target.list_sources()[0]
    original = library.list_sources()[0]
    # 연결 태그의 내부 ID는 대상 DB의 동일 이름 태그에 병합하므로 내용으로 비교한다.
    assert [t['name'] for t in restored.pop('tags')] == [t['name'] for t in original.pop('tags')]
    assert restored == original
    assert '미사용 태그' in [t['name'] for t in target.tags()]
    assert restore(target, content, True)['skipped'] == 1
    assert len(target.list_sources()) == 1
    with target.connect() as db:
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []


def test_list_export_korean_and_formula_protection(service, library, source):
    service.save(dict(source, title='=1+1', authors='@위험', notes='\t=1', purpose='한글 목적'))
    rows = list(csv.DictReader(io.StringIO(export_list(library).decode('utf-8-sig'))))
    assert rows[0]['제목'] == "'=1+1"
    assert rows[0]['저자·기관'] == "'@위험"
    assert rows[0]['핵심 메모'] == "'\t=1"
    assert rows[0]['활용 목적'] == '한글 목적'
    assert library.list_sources()[0]['title'] == '=1+1'
    with pytest.raises(ValueError, match='복원용 CSV'):
        parse_backup(export_list(library), 'actual')


def test_restore_does_not_overwrite_existing(service, library, source):
    sid = service.save(source)
    content = export_backup(library)
    service.save(dict(source, title='백업 이후 수정'), source_id=sid)
    assert restore(library, content, True)['skipped'] == 1
    assert library.list_sources()[0]['title'] == '백업 이후 수정'


def test_different_id_same_url_skipped(tmp_path, service, library, source):
    from reviewlab.services.research import Research
    service.save(source)
    target = Library(tmp_path / 'target.sqlite3', 'actual')
    Research(target).save(dict(source, title='별도 ID'))
    assert restore(target, export_backup(library), True)['skipped'] == 1


@pytest.mark.parametrize('mutation', [
    lambda p: p.update(title=''),
    lambda p: p.update(publication_date='2025-02-30'),
    lambda p: p.update(id='wrong-id'),
    lambda p: p.update(created_at='not-a-timestamp'),
    lambda p: p.update(tags=['누락 태그']),
    lambda p: p.update(events=[{'status': '검증 완료'}]),
])
def test_malformed_backup_is_atomic(tmp_path, service, library, source, mutation):
    service.save(source)
    target = Library(tmp_path / 'target.sqlite3', 'actual')
    content = mutate_backup(export_backup(library), mutation)
    with pytest.raises(ValueError):
        restore(target, content, True)
    assert target.list_sources() == []


def test_bad_second_row_does_not_partially_restore(tmp_path, service, library, source):
    service.save(source)
    service.save(dict(source, title='둘째', original_url='https://example.com/second'))
    rows = list(csv.reader(io.StringIO(export_backup(library).decode('utf-8-sig'))))
    rows[-1][-1] = '{invalid'
    target = Library(tmp_path / 'target.sqlite3', 'actual')
    with pytest.raises(ValueError):
        restore(target, write_csv(rows[0], rows[1:]), True)
    assert target.list_sources() == []


def test_empty_backup_and_mode_isolation(tmp_path, library):
    content = export_backup(library)
    assert parse_backup(content, 'actual')[1] == []
    with pytest.raises(ValueError, match='모드'):
        parse_backup(content, 'example')
    target = Library(tmp_path / 'example.sqlite3', 'example')
    with pytest.raises(ValueError):
        restore(target, content, True)
    assert target.list_sources() == []


@pytest.mark.parametrize('content', [b'\xff', b'not a backup', b'x' * (MAX_BYTES + 1)])
def test_invalid_files_rejected(content):
    with pytest.raises(ValueError):
        parse_backup(content, 'actual')


def test_restore_duplicate_records_inside_file(tmp_path, service, library, source):
    service.save(source)
    service.save(source, allow_duplicate=True)
    target = Library(tmp_path / 'restored.sqlite3', 'actual')
    result = restore(target, export_backup(library), True)
    assert (result['added'], result['skipped']) == (1, 1)


def test_restore_event_conflict_rolls_back(tmp_path, service, library, source, event):
    from reviewlab.services.research import Research
    sid = service.save(source)
    service.verify(sid, event, True)
    content = export_backup(library)
    target = Library(tmp_path / 'target.sqlite3', 'actual')
    other_id = Research(target).save(dict(source, title='다른 자료', original_url='https://example.com/other'))
    with target.connect() as db:
        target.insert_event(db, other_id, library.list_sources()[0]['events'][0])
    before = target.list_sources()
    with pytest.raises(ValueError, match='충돌'):
        restore(target, content, True)
    assert target.list_sources() == before


def test_explicit_restore_preserves_intentional_duplicates(tmp_path, service, library, source):
    service.save(source)
    service.save(source, allow_duplicate=True)
    target = Library(tmp_path / 'preserved.sqlite3', 'actual')
    content = export_backup(library)
    result = restore(target, content, True, allow_duplicates=True)
    assert (result['added'], result['skipped']) == (2, 0)
    assert restore(target, content, True, allow_duplicates=True)['skipped'] == 2


@pytest.mark.parametrize('mode', ['actual', 'example'])
def test_csv_disk_roundtrip_all_fields(tmp_path, source, event, mode):
    """실제 CSV 파일 저장→다시 읽기→새 DB 복원→모든 보존 필드 비교."""
    import hashlib
    from reviewlab.models import STATUSES, TYPES
    from reviewlab.services.research import Research
    from reviewlab.services.transfer import download_filename, inspect_restore

    original = Library(tmp_path / 'original' / mode / 'reviewlab.sqlite3', mode)
    research = Research(original)
    research.add_tag('사용자 태그')
    custom = next(t for t in original.tags() if t['name'] == '사용자 태그')
    research.rename_tag(custom['id'], '수정한 사용자 태그')
    research.add_tag('미사용 보존 태그')
    selected = [original.tags()[0]['id'], custom['id']]
    for index, kind in enumerate(TYPES):
        sid = research.save(dict(source, title=f'[허구] 한글 자료 {index}',
                                 source_type=kind, authors='가상 저자, 가상 기관',
                                 original_url=f'https://example.com/source/{index}',
                                 publication_date=['2024', '2024-03', '2024-03-15', '', '2025-02'][index],
                                 notes='한글 메모, "따옴표"\n둘째 줄', purpose=f'허구 활용 목적 {index}'), selected)
        if index < 4:
            research.verify(sid, dict(event, status='원문 확인 중'))
            research.verify(sid, dict(event, status=STATUSES[index]), True)
    initial_hash = hashlib.sha256(original.path.read_bytes()).hexdigest()
    listing_path = tmp_path / download_filename(mode, 'list')
    backup_path = tmp_path / download_filename(mode, 'backup')
    listing_path.write_bytes(export_list(original))
    backup_path.write_bytes(export_backup(original))
    assert listing_path.read_bytes().startswith(b'\xef\xbb\xbf')
    assert backup_path.read_bytes().startswith(b'\xef\xbb\xbf')
    exported = list(csv.DictReader(io.StringIO(listing_path.read_text(encoding='utf-8-sig'), newline='')))
    assert len(exported) == 5
    by_id = {s['id']: s for s in original.list_sources()}
    column_map = {'제목': 'title', '저자·기관': 'authors', '발행일': 'publication_date',
                  '자료 유형': 'source_type', '원문 URL': 'original_url', '핵심 메모': 'notes',
                  '활용 목적': 'purpose', '등록일(UTC)': 'created_at', '수정일(UTC)': 'updated_at', '검증 상태': 'status'}
    for row in exported:
        expected = by_id[row['자료 ID']]
        assert all(row[column] == expected[key] for column, key in column_map.items())
        assert row['태그'] == ' | '.join(t['name'] for t in expected['tags'])
        latest = expected['events'][-1] if expected['events'] else {}
        assert row['검증 담당자'] == latest.get('checked_by', '')
        assert row['검증 메모'] == latest.get('memo', '')
        assert row['확인한 원문·공식 자료'] == latest.get('evidence', '')
        assert row['검증 날짜'] == latest.get('checked_on', '')
        assert row['데이터 구분'] == mode
    recovered = Library(tmp_path / 'recovered' / mode / 'reviewlab.sqlite3', mode)
    report = inspect_restore(recovered, backup_path.read_bytes())
    assert (report['added'], report['skipped'], report['error_count']) == (5, 0, 0)
    assert report['can_restore']
    result = restore(recovered, backup_path.read_bytes(), confirmed=True)
    assert (result['added'], result['skipped']) == (5, 0)
    # 태그 내부 UUID는 이름으로 병합되므로, 전용 백업의 모든 보존 필드를 재비교한다.
    assert export_backup(recovered) == backup_path.read_bytes()
    assert export_list(recovered) == listing_path.read_bytes()
    assert hashlib.sha256(original.path.read_bytes()).hexdigest() == initial_hash
    assert sum(len(s['events']) for s in recovered.list_sources()) == 8
    assert restore(recovered, backup_path.read_bytes(), confirmed=True)['skipped'] == 5
    with recovered.connect() as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert not db.execute('PRAGMA foreign_key_check').fetchall()


def test_preview_counts_errors_and_never_partially_restores(service, library, source, tmp_path):
    import hashlib
    from reviewlab.services.transfer import inspect_restore
    for i in range(3):
        service.save(dict(source, title=f'자료{i}', original_url=f'https://example.com/{i}'))
    rows = list(csv.reader(io.StringIO(export_backup(library).decode('utf-8-sig'))))
    bad = json.loads(rows[3][-1])
    bad['title'] = ''
    rows[3][-1] = json.dumps(bad)
    rows[4][-1] = '{invalid-json'
    content = write_csv(rows[0], rows[1:])
    target = Library(tmp_path / 'target.sqlite3', 'actual')
    before = hashlib.sha256(target.path.read_bytes()).hexdigest()
    report = inspect_restore(target, content)
    assert (report['total'], report['added'], report['skipped'], report['error_count']) == (3, 1, 0, 2)
    assert report['scope'] == 'record'
    assert not report['can_restore']
    assert [e['record'] for e in report['errors']] == [2, 3]
    with pytest.raises(ValueError):
        restore(target, content, True)
    assert hashlib.sha256(target.path.read_bytes()).hexdigest() == before
    assert target.list_sources() == []


def test_preview_reports_duplicates_and_file_errors(service, library, source):
    from reviewlab.services.transfer import inspect_restore
    service.save(source)
    report = inspect_restore(library, export_backup(library))
    assert (report['added'], report['skipped'], report['error_count']) == (0, 1, 0)
    bad = inspect_restore(library, export_list(library))
    assert bad['scope'] == 'file'
    assert bad['error_count'] == 1
    assert bad['added'] is None and bad['skipped'] is None
    assert not bad['can_restore']


def test_download_filename_uses_korean_time(monkeypatch):
    import re
    from datetime import datetime, timezone
    from reviewlab.services import transfer
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 22, 16, 2, 3, tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(transfer, 'datetime', Clock)
    assert transfer.download_filename('actual', 'backup') == 'reviewlab-actual-backup-2026-10-23_010203-KST.csv'
    assert re.fullmatch(r'reviewlab-example-list-\d{4}-\d{2}-\d{2}_\d{6}-KST.csv', transfer.download_filename('example', 'list'))
    with pytest.raises(ValueError):
        transfer.download_filename('../outside', 'backup')


def test_preview_detects_existing_event_id_conflict(tmp_path, service, library, source, event):
    from reviewlab.services.research import Research
    from reviewlab.services.transfer import inspect_restore
    sid = service.save(source)
    service.verify(sid, event, True)
    target = Library(tmp_path / 'target.sqlite3', 'actual')
    other = Research(target).save(dict(source, title='기존 자료', original_url='https://example.com/other'))
    with target.connect() as db:
        target.insert_event(db, other, library.list_sources()[0]['events'][0])
    before = target.list_sources()
    report = inspect_restore(target, export_backup(library))
    assert report['added'] == 0 and report['error_count'] == 1
    assert not report['can_restore']
    assert target.list_sources() == before
