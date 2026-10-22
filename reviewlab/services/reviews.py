"""리뷰 입력 검증. 원문은 허용한 필드만 보존하고 분석 주석과 분리한다."""
import csv
import hashlib
import io
import json
import math
from datetime import date
from urllib.parse import urlsplit
from uuid import uuid4
from reviewlab.models import now, text
from reviewlab.repositories.reviews import Reviews

LABELS = dict(review_key='리뷰 고유 ID', platform='플랫폼명', industry='업종', rating='별점',
    review_date='리뷰 작성일', content='리뷰 내용', event_mentioned='리뷰 이벤트 언급 여부',
    ad_disclosed='광고·협찬 표시 여부', collected_on='수집일', original_url='원문 URL',
    research_memo='연구자 메모', data_kind='데이터 구분')
FLAGS = ('미확인', '예', '아니오')
STATES = ('미검토', '검토 중', '검토 완료', '분석 제외')
LIMIT = 5000


def encode_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def day(value):
    if value is None:
        return None
    try:
        result = date.fromisoformat(value)
        if result.isoformat() != value or result > date.today():
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('날짜는 미래가 아닌 YYYY-MM-DD 형식이어야 합니다.') from None
    return value


def normalize(raw, project):
    if set(raw) - set(LABELS) or any(not isinstance(v, str) or '\x00' in v or len(v) > 30000 for v in raw.values()):
        raise ValueError('지원하지 않는 필드 또는 너무 긴 값입니다.')
    def optional(k):
        v = raw.get(k, '').strip()
        return None if v.lower() in ('', '미확인', 'unknown') else v
    values = {k: optional(k) for k in LABELS if k not in ('research_memo', 'data_kind')}
    if project['data_kind'] == 'actual' and (optional('data_kind') in ('example', '가상·테스트용') or '가상·테스트용' in (values['content'] or '')):
        raise ValueError('가상·테스트용 자료는 예시·연습 모드에서만 업로드하세요.')
    kind = optional('data_kind')
    if kind and kind not in (project['data_kind'], '실제 연구자료' if project['data_kind'] == 'actual' else '가상·테스트용'):
        raise ValueError('데이터 구분이 현재 모드와 다릅니다.')
    if values['rating'] is not None:
        try:
            values['rating'] = float(values['rating'])
            if not math.isfinite(values['rating']) or not project['rating_min'] <= values['rating'] <= project['rating_max']:
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError('별점이 숫자가 아니거나 프로젝트 척도 범위를 벗어났습니다.') from None
    for k in ('review_date', 'collected_on'):
        values[k] = day(values[k])
    for k in ('event_mentioned', 'ad_disclosed'):
        v = (values[k] or '미확인').lower()
        aliases = {'yes':'예', 'true':'예', '1':'예', 'no':'아니오', 'false':'아니오', '0':'아니오'}
        values[k] = aliases.get(v, v)
        if values[k] not in FLAGS:
            raise ValueError('언급·표시 여부는 예 / 아니오 / 미확인으로 입력하세요.')
    if values['original_url']:
        u = urlsplit(values['original_url'])
        if u.scheme not in ('http','https') or not u.hostname or u.username or u.password or any(c.isspace() or ord(c)<32 for c in values['original_url']):
            raise ValueError('원문 URL은 계정 정보 없는 http(s) 주소여야 합니다.')
    if values['content'] is None and values['rating'] is None:
        raise ValueError('리뷰 내용 또는 별점 중 하나는 필요합니다.')
    identity = [values['platform'], values['review_key']] if values['review_key'] else values
    values['identity_key'] = hashlib.sha256(encode_json(identity).encode()).hexdigest()
    return values


def read_csv(content, encoding='utf-8-sig'):
    if len(content) > 10 * 1024 * 1024:
        raise ValueError('리뷰 CSV는 10MB 이하로 나누어 올리세요.')
    if encoding not in ('utf-8-sig','cp949'):
        raise ValueError('지원하지 않는 인코딩입니다.')
    try:
        reader = csv.DictReader(io.StringIO(content.decode(encoding)), strict=True)
        headers = reader.fieldnames
        if not headers or len(set(headers)) != len(headers) or any(not h for h in headers):
            raise ValueError('CSV 열 이름이 없거나 중복되었습니다.')
        rows = []
        for row in reader:
            if None in row or any(v is None for v in row.values()):
                raise ValueError('CSV 행의 열 개수가 일치하지 않습니다.')
            rows.append(row)
            if len(rows) > LIMIT:
                raise ValueError('한 파일은 5,000행 이하로 나누세요.')
        if not rows:
            raise ValueError('CSV에 리뷰 행이 없습니다.')
        return headers, rows
    except (UnicodeError, csv.Error):
        raise ValueError('CSV를 읽지 못했습니다. 인코딩과 쉼표 구분 형식을 확인하세요.') from None


def mapping_for(headers):
    return {k: label if label in headers else k for k,label in LABELS.items() if label in headers or k in headers}


class ReviewService:
    def __init__(self, library):
        self.repo = Reviews(library)
        self.library = library

    def create_project(self, name, minimum=0, maximum=5, description=''):
        if not name.strip() or len(name)>200 or not all(math.isfinite(v) for v in (minimum, maximum)) or maximum <= minimum:
            raise ValueError('프로젝트 이름과 올바른 별점 최솟값·최댓값이 필요합니다.')
        text(description,'설명',limit=30000)
        return self.repo.create_project(name,minimum,maximum,description)

    def preview(self, pid, content, encoding='utf-8-sig', mapping=None):
        project = self.repo.project(pid)
        if not project:
            raise ValueError('분석 프로젝트를 먼저 선택하세요.')
        headers, rows = read_csv(content, encoding)
        mapping = mapping_for(headers) if mapping is None else mapping
        if set(mapping)-set(LABELS) or not set(mapping.values()) <= set(headers) or len(set(mapping.values())) != len(mapping):
            raise ValueError('열 연결이 잘못되었거나 중복되었습니다.')
        if not {'content','rating'} & set(mapping):
            raise ValueError('리뷰 내용 또는 별점 열을 연결하세요.')
        known = {r['identity_key'] for r in self.repo.rows(pid)}
        records, errors, skipped = [], [], 0
        for number,row in enumerate(rows, 2):
            raw = {k:row[col] for k,col in mapping.items()}
            try:
                values = normalize(raw, project)
                if values['identity_key'] in known:
                    skipped += 1
                    continue
                known.add(values['identity_key'])
                records.append(dict(values, raw_json=encode_json(raw), source_row=number))
            except ValueError as e:
                errors.append(f'{number}행: {e}')
        if len(known) > LIMIT:
            errors.append('프로젝트당 최대 5,000개 리뷰를 지원합니다. 새 프로젝트로 나누세요.')
        return dict(records=records, errors=errors, added=len(records), skipped=skipped,
                    total=len(rows), mapping=mapping, ignored_columns=len(headers)-len(mapping))

    def upload(self, pid, content, source_note, confirmed, encoding='utf-8-sig', mapping=None):
        if not confirmed or not source_note.strip():
            raise ValueError('수집 경위와 이용 권한·개인정보·별점 척도 확인이 필요합니다.')
        text(source_note,'수집 경위',required=True,limit=30000)
        # 잠금을 먼저 얻어 미리보기 이후 다른 업로드와의 경합도 다시 검사한다.
        with self.library.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            report = self.preview(pid,content,encoding,mapping)
            if report['errors']:
                raise ValueError('\n'.join(report['errors'][:20]))
            iid = str(uuid4())
            self.repo.insert(db,'review_imports',dict(id=iid,project_id=pid,file_sha256=hashlib.sha256(content).hexdigest(),
                encoding=encoding,mapping_json=encode_json(report['mapping']),source_note=source_note,total_rows=report['total'],
                imported_rows=report['added'],skipped_rows=report['skipped'],ignored_columns=report['ignored_columns'],created_at=now()))
            for record in report['records']:
                self.repo.insert(db,'reviews',dict(record,id=str(uuid4()),project_id=pid,import_id=iid,created_at=now()))
        return report

    def check(self,rid,status,reviewer,checked_on,evidence,memo=''):
        if status not in STATES:
            raise ValueError('지원하지 않는 검토 상태입니다.')
        for label,value in (('검토자',reviewer),('근거',evidence),('메모',memo)):
            text(value,label,limit=30000)
        day(checked_on)
        if not reviewer.strip() or not checked_on or (status in ('검토 완료','분석 제외') and not evidence.strip()):
            raise ValueError('검토자·검토일이 필요하며 완료·제외 시 판단 근거도 입력하세요.')
        self.repo.append_check(rid,dict(status=status,reviewer=reviewer,checked_on=checked_on,evidence=evidence,memo=memo))

    def note(self,rid,memo):
        if not memo.strip() or len(memo)>30000:
            raise ValueError('메모는 1~30,000자로 입력하세요.')
        self.repo.append_note(rid,memo)
