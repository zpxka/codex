import sqlite3

import pytest

from reviewlab.repositories.library import Library
from reviewlab.services.research import Research


def test_crud_search_restart_delete(service, library, source):
    tag_id = library.tags()[0]['id']
    sid = service.save(source, [tag_id])
    assert service.search('소비자')[0]['id'] == sid
    assert service.search('가상')[0]['id'] == sid
    assert service.search('없는 검색') == []
    assert service.search('%') == []  # SQL 와일드카드로 처리하지 않음
    assert service.search("' OR 1=1 --") == []
    assert service.search(source_type='기타') == []
    assert service.search(tag=library.tags()[0]['name'])[0]['id'] == sid
    before = service.search()[0]['created_at']
    service.save(dict(source, title='[허구] 수정된 연구'), [tag_id], sid)
    reopened = Research(Library(library.path, 'actual'))
    row = reopened.search()[0]
    assert row['title'] == '[허구] 수정된 연구'
    assert row['created_at'] == before
    assert row['status'] == '미검증'
    with pytest.raises(ValueError, match='삭제 확인'):
        service.delete(sid)
    assert len(reopened.search()) == 1
    backup = reopened.delete(sid, True)
    assert reopened.search() == []
    with sqlite3.connect(backup) as db:
        assert db.execute('SELECT count(*) FROM sources').fetchone()[0] == 1
    with library.connect() as db:
        assert db.execute('SELECT count(*) FROM source_tags').fetchone()[0] == 0
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


@pytest.mark.parametrize('field,value', [
    ('title', ''), ('title', '   '), ('original_url', 'javascript:alert(1)'),
    ('original_url', 'https://user:password@example.com'), ('original_url', 'https://example.com/a b'),
    ('original_url', 'https://[bad'), ('original_url', 'http://example.com:bad'),
    ('source_type', '알 수 없음'), ('publication_date', '2025-02-30'),
    ('publication_date', '2025-13'), ('publication_date', '2025/01'),
])
def test_invalid_input_does_not_save(service, source, field, value):
    with pytest.raises(ValueError):
        service.save(dict(source, **{field: value}))
    assert service.search() == []


@pytest.mark.parametrize('day', ['', '2024', '2024-02', '2024-02-29'])
def test_optional_and_partial_dates(service, source, day):
    service.save(dict(source, authors='', publication_date=day))
    assert service.search()[0]['publication_date'] == day


def test_duplicates_are_explicit(service, source):
    service.save(source)
    with pytest.raises(ValueError, match='중복 후보'):
        service.save(source)
    service.save(source, allow_duplicate=True)
    assert len(service.search()) == 2


def test_verification_requires_manual_confirmation_and_all_fields(service, library, source, event):
    sid = service.save(source)
    with pytest.raises(ValueError, match='직접 확인'):
        service.verify(sid, event)
    for key in ('checked_on', 'checked_by', 'evidence', 'memo'):
        with pytest.raises(ValueError):
            service.verify(sid, dict(event, **{key: ''}), True)
    assert service.search()[0]['status'] == '미검증'
    for status in ('원문 확인 중', '검증 완료', '사용 보류'):
        service.verify(sid, dict(event, status=status), True)
    row = Research(Library(library.path, 'actual')).search(status='사용 보류')[0]
    assert [e['status'] for e in row['events']] == ['원문 확인 중', '검증 완료', '사용 보류']
    service.delete(sid, True)
    with library.connect() as db:
        assert db.execute('SELECT count(*) FROM verification_events').fetchone()[0] == 0


def test_tags_rename_preserves_associations(service, library, source):
    service.add_tag('새 연구 주제')
    tag = next(t for t in library.tags() if t['name'] == '새 연구 주제')
    service.save(source, [tag['id']])
    service.rename_tag(tag['id'], '수정된 연구 주제')
    assert service.search(tag='수정된 연구 주제')[0]['tags'][0]['id'] == tag['id']
    with pytest.raises(ValueError, match='이미'):
        service.add_tag('수정된 연구 주제')


def test_sort_unknown_dates_last(service, source):
    for i, day in enumerate(('', '2024', '2025-03')):
        service.save(dict(source, title=f'자료 {i}', original_url=f'https://example.com/{i}', publication_date=day))
    assert [r['publication_date'] for r in service.search(sort='발행일 최신순')] == ['2025-03', '2024', '']
    assert [r['publication_date'] for r in service.search(sort='발행일 오래된순')] == ['2024', '2025-03', '']


def test_mode_and_schema_protection(library):
    with pytest.raises(ValueError, match='모드'):
        Library(library.path, 'example')
    with library.connect() as db:
        db.execute('INSERT INTO schema_migrations VALUES(3,?)', ('future',))
    with pytest.raises(ValueError, match='버전'):
        Library(library.path, 'actual')


def test_failed_write_rolls_back(library, service, source):
    sid = service.save(source)
    with pytest.raises(sqlite3.IntegrityError):
        library.save(dict(source, title='실패할 수정'), ['missing-tag'], sid)
    assert service.search()[0]['title'] == source['title']


def test_persistence_in_a_new_python_process(library, service, source):
    import json
    import subprocess
    import sys
    sid = service.save(source)
    result = subprocess.run(
        [sys.executable, '-c',
         'import json,sys; from pathlib import Path; from reviewlab.repositories.library import Library; '
         'print(json.dumps(Library(Path(sys.argv[1]), "actual").list_sources(), ensure_ascii=False))',
         str(library.path)], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)[0]['id'] == sid
    assert json.loads(result.stdout)[0]['title'] == source['title']


def test_reserved_filter_word_can_be_a_tag(service, library, source):
    service.add_tag('전체')
    tag = next(t for t in library.tags() if t['name'] == '전체')
    service.save(source, [tag['id']])
    service.save(dict(source, title='다른 자료', original_url='https://example.com/other'))
    assert len(service.search(tag='전체')) == 1
    assert len(service.search()) == 2
