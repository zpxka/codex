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
