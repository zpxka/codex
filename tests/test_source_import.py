"""일반 CSV 일괄 등록은 허구 자료·임시 DB로만 검증한다."""
import csv
import io
import sqlite3

import pytest

from reviewlab.repositories.library import Library
from reviewlab.services.source_import import LABELS, import_csv, preview_import, template_csv
from reviewlab.services.transfer import export_backup, export_list, restore, write_csv


def blob(records, extra=(), encoding='utf-8-sig'):
    keys = list(LABELS) + list(extra)
    content = write_csv([LABELS.get(k, k) for k in keys], [[r.get(k, '') for k in keys] for r in records])
    return content.decode('utf-8-sig').encode(encoding)


def record():
    return dict(title='[허구] 소비자 연구 자료', authors='가상 연구팀', publication_date='2025-02',
                source_type='학술논문', original_url='https://example.com/bulk',
                notes='한글 메모\n쉼표, 따옴표 "원문"', purpose='일괄 등록 테스트', tags='소비자 신뢰도|새 분류')


def load(library, content, encoding='utf-8-sig'):
    return import_csv(library, content, encoding, confirmed_mode=library.mode)


@pytest.mark.parametrize('encoding', ['utf-8-sig', 'cp949'])
def test_normal_import_korean_tags_and_automatic_backup(library, service, encoding):
    data = record()
    payload = blob([data], encoding=encoding)
    before_tags = library.tags()
    report = preview_import(library, payload, encoding)
    assert (report['added'], report['skipped'], report['failed']) == (1, 0, 0)
    assert library.list_sources() == [] and library.tags() == before_tags  # 미리보기는 쓰기 없음
    assert load(library, payload, encoding) == dict(total=1, added=1, skipped=0, failed=0)
    row = service.search(query='가상', tag='새 분류', status='미검증')[0]
    for key in ('title', 'authors', 'publication_date', 'source_type', 'original_url', 'notes', 'purpose'):
        assert row[key] == data[key]
    assert row['status'] == '미검증' and row['events'] == []
    assert {t['name'] for t in row['tags']} == {'소비자 신뢰도', '새 분류'}
    old = next(t for t in before_tags if t['name'] == '소비자 신뢰도')
    assert old in row['tags']
    assert Library(library.path, library.mode).list_sources() == [row]
    with sqlite3.connect(library.automatic_backups()[0]) as db:
        assert db.execute('SELECT title FROM sources').fetchone()[0] == data['title']
        assert db.execute('SELECT count(*) FROM source_tags').fetchone()[0] == 2


def test_existing_and_file_duplicates_keep_verified_source(library, service, source, event):
    sid = service.save(source)
    service.verify(sid, event, True)
    original = library.list_sources()[0]
    rows = [dict(record(), title='별도 제목', original_url=source['original_url'], tags='건너뛴 태그'),
            dict(record(), title=source['title'].upper(), authors=source['authors'].upper(),
                 publication_date=source['publication_date'], original_url='https://example.com/another'),
            record(), dict(record(), title='CSV 중복', tags='추가하면안되는태그'),
            dict(record(), title='또 다른 자료', original_url='https://example.com/second')]
    payload = blob(rows)
    report = preview_import(library, payload)
    assert (report['added'], report['skipped'], report['failed']) == (2, 3, 0)
    assert '기존 자료' in report['rows'][0]['reason']
    assert '제목·저자·발행일' in report['rows'][1]['reason']
    assert 'CSV 4행' in report['rows'][3]['reason']
    assert load(library, payload)['added'] == 2
    assert next(r for r in library.list_sources() if r['id'] == sid) == original
    assert not {'건너뛴 태그', '추가하면안되는태그'} & {t['name'] for t in library.tags()}
    assert load(library, payload) == dict(total=5, added=0, skipped=5, failed=0)


@pytest.mark.parametrize('changes', [dict(title=''), dict(source_type=''), dict(original_url=''),
    dict(source_type='허용하지 않는 유형'), dict(original_url='javascript:alert(1)'),
    dict(original_url='https://user:secret@example.com'), dict(original_url='https://exa mple.com'),
    dict(publication_date='2025-02-30'), dict(tags='가'*101), dict(title='\x00')])
def test_bad_row_rejects_whole_file_without_touching_existing(library, service, source, changes):
    service.save(source)
    before = export_backup(library)
    payload = blob([record(), dict(record(), **changes)])
    report = preview_import(library, payload)
    assert report['failed'] == 1 and not report['can_import']
    assert report['rows'][1]['row'] == 3 and report['rows'][1]['reason']
    with pytest.raises(ValueError, match='오류 자료 1건'):
        load(library, payload)
    assert export_backup(library) == before


@pytest.mark.parametrize('payload', [b'', b'\xff', b'title,title\na,b', b'title,source_type\na,b',
    b'title,source_type,original_url\na,b', b'title,source_type,original_url\n"unfinished',
    b' ,source_type,original_url\na,b,c', b'title,source_type,original_url',
    b'title,source_type,original_url\na,b,c,d'])
def test_malformed_file_errors_are_safe(library, payload):
    before = export_backup(library)
    with pytest.raises(ValueError):
        preview_import(library, payload)
    with pytest.raises(ValueError):
        load(library, payload)
    assert export_backup(library) == before


def test_aliases_optional_fields_and_ambiguous_columns(library):
    payload = write_csv([' 자료제목 ', '자료유형', 'URL'],
                        [['[허구] 별칭', '기타', 'https://example.com/alias']])
    load(library, payload)
    row = library.list_sources()[0]
    assert row['title'] == '[허구] 별칭'
    assert all(row[k] == '' for k in ('authors', 'publication_date', 'notes', 'purpose'))
    assert row['tags'] == []
    ambiguous = write_csv(['제목', '자료 제목', '자료 유형', '원문 URL'], [['A', 'B', '기타', 'https://example.com']])
    with pytest.raises(ValueError, match='여러 개'):
        preview_import(library, ambiguous)


def test_extra_verification_columns_do_not_grant_verification(library):
    data = dict(record(), **{'검증 상태': '검증 완료', '검증 담당자': '자동 승계 금지', '자료 ID': 'external'})
    payload = blob([data], extra=('검증 상태', '검증 담당자', '자료 ID'))
    report = preview_import(library, payload)
    assert set(report['ignored_columns']) == {'검증 상태', '검증 담당자', '자료 ID'}
    load(library, payload)
    assert library.list_sources()[0]['status'] == '미검증'
    assert library.list_sources()[0]['events'] == []


def test_modes_and_explicit_csv_mode(library, tmp_path):
    example = Library(tmp_path/'example'/'reviewlab.sqlite3', 'example')
    payload = blob([dict(record(), **{'데이터 구분': '가상·테스트용'})], extra=('데이터 구분',))
    assert preview_import(library, payload)['failed'] == 1
    with pytest.raises(ValueError):
        load(library, payload)
    load(example, payload)
    assert not library.list_sources() and len(example.list_sources()) == 1
    with pytest.raises(ValueError, match='모드'):
        import_csv(library, blob([record()]), confirmed_mode='example')
    with pytest.raises(ValueError):
        import_csv(library, blob([record()]))


def test_formula_text_preserved_and_export_escaped(library):
    data = dict(record(), title='=1+1', authors='@가상', notes='\t=1', tags='=태그')
    load(library, blob([data]))
    row = library.list_sources()[0]
    assert row['title'] == '=1+1' and row['notes'] == '\t=1'
    cells = list(csv.DictReader(io.StringIO(export_list(library).decode('utf-8-sig'))))[0]
    assert cells['제목'] == "'=1+1" and cells['저자·기관'] == "'@가상" and cells['핵심 메모'] == "'\t=1"
    assert cells['태그'] == "'=태그"


def test_row_errors_all_visible_and_duplicates_not_counted_as_errors(library):
    payload = blob([record(), record(), dict(record(), title=''), dict(record(), original_url='wrong')])
    report = preview_import(library, payload)
    assert (report['total'], report['added'], report['skipped'], report['failed']) == (4, 1, 1, 2)
    assert [r['row'] for r in report['rows'] if r['status'] == '오류'] == [4, 5]


def test_registration_rechecks_after_preview_and_backup_only_once(library, service, monkeypatch):
    one, two = record(), dict(record(), title='두 번째 자료', original_url='https://example.com/two')
    payload = blob([one, two])
    assert preview_import(library, payload)['added'] == 2
    service.save({k: one[k] for k in ('title', 'authors', 'publication_date', 'source_type', 'original_url', 'notes', 'purpose')})
    calls = []
    original = library.automatic_backup
    def capture(**kwargs):
        calls.append(True)
        return original(**kwargs)
    monkeypatch.setattr(library, 'automatic_backup', capture)
    assert load(library, payload) == dict(total=2, added=1, skipped=1, failed=0)
    assert len(calls) == 1
    load(library, payload)
    assert len(calls) == 1  # 중복만 있는 등록은 DB 쓰기/불필요한 백업 없음


def test_transaction_rolls_back_sources_new_tags_and_links(library, service, source, event, monkeypatch):
    sid = service.save(source)
    service.verify(sid, event, True)
    before = export_backup(library)
    original = library.insert_source
    def fail_second(db, record):
        if record['title'] == '쓰기 실패':
            raise sqlite3.OperationalError('test write failure')
        return original(db, record)
    monkeypatch.setattr(library, 'insert_source', fail_second)
    payload = blob([record(), dict(record(), title='쓰기 실패', original_url='https://example.com/fail')])
    with pytest.raises(sqlite3.OperationalError):
        load(library, payload)
    assert export_backup(library) == before
    assert not {'새 분류'} & {t['name'] for t in library.tags()}
    with library.connect() as db:
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []


def test_template_limits_and_original_backup_compatibility(library, tmp_path, monkeypatch):
    from reviewlab.services import source_import
    template = template_csv()
    assert template.startswith(b'\xef\xbb\xbf')
    assert list(csv.reader(io.StringIO(template.decode('utf-8-sig')))) == [list(LABELS.values())]
    load(library, blob([dict(record(), tags=' 소비자 신뢰도 | 새 분류 | 새 분류 ||')]))
    backup = export_backup(library)
    target = Library(tmp_path/'restored.db', 'actual')
    restore(target, backup, True)
    assert target.list_sources()[0]['title'] == record()['title']
    assert {t['name'] for t in target.list_sources()[0]['tags']} == {'소비자 신뢰도', '새 분류'}
    with pytest.raises(ValueError, match='전용 백업'):
        preview_import(library, backup)
    with pytest.raises(ValueError, match='인코딩'):
        preview_import(library, blob([record()]), 'utf-16')
    monkeypatch.setattr(source_import, 'MAX_ROWS', 1)
    with pytest.raises(ValueError, match='5,000'):
        preview_import(library, blob([record(), record()]))
    monkeypatch.setattr(source_import, 'MAX_BYTES', 1)
    with pytest.raises(ValueError, match='10MB'):
        preview_import(library, blob([record()]))
