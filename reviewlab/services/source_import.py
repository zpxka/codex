"""일반 참고문헌 CSV 입력. 전용 백업 복원 형식과 별개이며 검증 이력을 승계하지 않는다."""
import csv
import io

from reviewlab.config import MODES
from reviewlab.models import FIELDS, tag_name, validate_source
from reviewlab.repositories.source_import import source_batch
from reviewlab.services.research import source_key
from reviewlab.services.transfer import write_csv

MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 5000
MAX_TAGS = 50
ENCODINGS = ('utf-8-sig', 'cp949')
LABELS = dict(title='제목', authors='저자·기관', publication_date='발행일', source_type='자료 유형',
              original_url='원문 URL', notes='핵심 메모', purpose='활용 목적', tags='태그')
ALIASES = {
    'title': ('제목', '자료 제목', '자료제목', 'title'),
    'authors': ('저자·기관', '저자 또는 작성 기관', '저자/기관', '저자', '작성 기관', 'authors'),
    'publication_date': ('발행일', 'publication_date'),
    'source_type': ('자료 유형', '자료유형', 'source_type'),
    'original_url': ('원문 URL', '원문URL', 'URL', 'original_url'),
    'notes': ('핵심 메모', '핵심 내용 메모', '자료의 핵심 내용 메모', 'notes'),
    'purpose': ('활용 목적', '연구 활용 목적', '연구에서 활용하려는 목적', 'purpose'),
    'tags': ('태그', '연구 주제 태그', 'tags'),
    'data_kind': ('데이터 구분', 'data_kind'),
}
REQUIRED = ('title', 'source_type', 'original_url')


def template_csv():
    # 빈 양식만 제공한다. 허구 참고문헌을 실제 저장소로 가져올 위험을 만들지 않는다.
    return write_csv(list(LABELS.values()), [])


def _read(content, encoding):
    if encoding not in ENCODINGS:
        raise ValueError('CSV 인코딩은 UTF-8 BOM 또는 CP949를 선택하세요.')
    if not content or len(content) > MAX_BYTES:
        raise ValueError('비어 있지 않은 10MB 이하 CSV 파일을 선택하세요.')
    csv.field_size_limit(MAX_BYTES)
    try:
        reader = csv.DictReader(io.StringIO(content.decode(encoding), newline=''), strict=True)
        original = reader.fieldnames
        if not original:
            raise ValueError('첫 행에 CSV 열 이름이 필요합니다. 양식을 내려받아 사용하세요.')
        headers = [name.strip() for name in original]
        if any(not name or '\x00' in name for name in headers) or len(set(headers)) != len(headers):
            raise ValueError('열 이름이 비어 있거나 중복되었습니다. 첫 행을 수정하세요.')
        mapping = {}
        for field, aliases in ALIASES.items():
            found = [old for old, header in zip(original, headers) if header in aliases]
            if len(found) > 1:
                raise ValueError(f'{LABELS.get(field, "데이터 구분")}에 해당하는 열이 여러 개입니다. 하나만 남기세요.')
            if found:
                mapping[field] = found[0]
        missing = [LABELS[k] for k in REQUIRED if k not in mapping]
        if missing:
            raise ValueError('필수 열이 없습니다: ' + ', '.join(missing) + '. 일반 참고문헌 양식을 사용하세요. 전용 백업은 백업 및 복원 메뉴에서 처리합니다.')
        rows = []
        for number, row in enumerate(reader, 2):
            if len(rows) >= MAX_ROWS:
                raise ValueError('한 번에 최대 5,000개 자료를 등록할 수 있습니다. 파일을 나누세요.')
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f'{number}행의 열 개수가 첫 행과 다릅니다. 쉼표·따옴표를 확인하세요.')
            rows.append((number, {field: row[column] for field, column in mapping.items()}))
        if not rows:
            raise ValueError('CSV에 자료가 없습니다. 양식의 두 번째 행부터 작성하세요.')
        ignored = [name for name in original if name not in mapping.values()]
        return rows, mapping, ignored
    except (UnicodeError, csv.Error):
        raise ValueError('CSV를 읽지 못했습니다. 인코딩을 바꾸거나 CSV의 쉼표·따옴표를 확인하세요.') from None


def _prepare(content, encoding, mode):
    rows, mapping, ignored = _read(content, encoding)
    prepared = []
    for number, row in rows:
        problems = []
        source = {k: row.get(k, '') for k in FIELDS}
        try:
            validate_source(source)
        except ValueError as error:
            problems.append(str(error))
        tags = []
        try:
            tags = list(dict.fromkeys(tag_name(part.strip()) for part in row.get('tags', '').split('|') if part.strip()))
            if len(tags) > MAX_TAGS:
                raise ValueError('한 자료의 태그는 최대 50개입니다.')
        except ValueError as error:
            problems.append(str(error))
        if 'data_kind' in row:
            declared = row['data_kind'].strip()
            allowed = {mode, next(label for label, value in MODES.items() if value == mode)}
            if mode == 'example':
                allowed.add('가상·테스트용')
            if declared and declared not in allowed:
                problems.append('CSV의 데이터 구분이 현재 실제/예시 모드와 다릅니다. 같은 모드에서 등록하세요.')
        prepared.append(dict(row=number, source=source, tags=tags, errors=problems))
    return prepared, mapping, ignored


def _inspect(prepared, existing):
    urls = {r['original_url']: '기존 자료' for r in existing}
    identities = {source_key(r): '기존 자료' for r in existing}
    report = dict(total=len(prepared), added=0, skipped=0, failed=0, rows=[])
    for item in prepared:
        source = item['source']
        row = dict(row=item['row'], title=source['title'], source=source, tags=item['tags'])
        if item['errors']:
            row.update(status='오류', reason=' / '.join(item['errors']))
            report['failed'] += 1
        else:
            url_match = urls.get(source['original_url'])
            identity_match = identities.get(source_key(source))
            if url_match or identity_match:
                row.update(status='중복', reason=f'{url_match}와 원문 URL 동일' if url_match else f'{identity_match}와 제목·저자·발행일 동일')
                report['skipped'] += 1
            else:
                row.update(status='추가 예정', reason='새 자료 · 검증 상태 미검증')
                report['added'] += 1
                urls[source['original_url']] = f'CSV {item["row"]}행'
                identities[source_key(source)] = f'CSV {item["row"]}행'
        report['rows'].append(row)
    report['can_import'] = report['failed'] == 0
    return report


def preview_import(library, content, encoding='utf-8-sig'):
    prepared, mapping, ignored = _prepare(content, encoding, library.mode)
    report = _inspect(prepared, library.list_sources())
    report.update(mapping=mapping, ignored_columns=ignored)
    return report


def import_csv(library, content, encoding='utf-8-sig', *, confirmed_mode=None):
    if confirmed_mode != library.mode:
        raise ValueError('현재 데이터 모드와 미검증 등록 정책을 확인하세요.')
    prepared, _, _ = _prepare(content, encoding, library.mode)
    # 파일은 다시 읽고, 기존 자료는 쓰기 잠금 획득 후 다시 검사한다.
    # 미리보기 이후 추가된 자료나 같은 파일의 반복 실행도 덮어쓰지 않는다.
    with source_batch(library) as batch:
        report = _inspect(prepared, batch.existing)
        if not report['can_import']:
            raise ValueError(f'오류 자료 {report["failed"]}건이 있어 전체 등록을 중단했습니다. CSV를 수정한 후 다시 업로드하세요. 추가 0건.')
        for item in report['rows']:
            if item['status'] == '추가 예정':
                batch.append(item['source'], item['tags'])
    return dict(total=report['total'], added=report['added'], skipped=report['skipped'], failed=0)
