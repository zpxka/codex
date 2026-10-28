"""기존 두 단계 전체 데이터의 공통 표현. SQLite 파일과 SQL은 신뢰하지 않고 검사한다."""
import hashlib
import json
import sqlite3
from contextlib import contextmanager, closing
from uuid import UUID
from pathlib import Path

from reviewlab.models import valid_id, validate_source, validate_event, tag_name, timestamp

TABLES = ('tags', 'sources', 'source_tags', 'verification_events', 'review_projects', 'review_imports',
          'reviews', 'review_notes', 'review_checks', 'review_source_links', 'review_analysis_runs')
from reviewlab.services.surveys import TABLES as SURVEY_TABLES
LEGACY_TABLES = TABLES
V2_TABLES = TABLES + SURVEY_TABLES
from reviewlab.services.documents import TABLES as DOCUMENT_TABLES
TABLES = V2_TABLES + DOCUMENT_TABLES

MAX_BYTES = 20 * 1024 * 1024
MAX_ROWS = 50000


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def empty_schema(db, mode):
    root = Path(__file__).parents[1] / 'db/migrations'
    db.executescript(''.join((root/name).read_text(encoding='utf-8') for name in ('001_initial.sql','002_reviews.sql','003_surveys.sql','004_documents.sql')))
    db.execute('INSERT INTO project_metadata VALUES(1,?)', (mode,))
    db.execute("INSERT INTO schema_migrations VALUES(1,'cloud-copy'),(2,'cloud-copy'),(3,'cloud-copy'),(4,'cloud-copy')")


def schema():
    with closing(sqlite3.connect(':memory:')) as db, db:
        empty_schema(db, 'example')
        return {t: [dict(zip(('cid','name','type','notnull','default','pk'), row))
                    for row in db.execute(f'PRAGMA table_info({t})')] for t in TABLES}


SCHEMA = schema()


def row_key(table, row):
    if 'id' in row:
        return (row['id'],)
    return (row['source_id'],row['tag_id']) if table == 'source_tags' else (row['project_id'],row['referenced_id'])


def normalize_order(data):
    normalized={}
    for table in TABLES:
        real_fields={c['name'] for c in SCHEMA[table] if c['type']=='REAL'}
        rows=[{k:float(v) if k in real_fields and v is not None else v for k,v in row.items()} for row in data[table]]
        normalized[table]=sorted(rows,key=lambda r:(r['sequence'],) if 'sequence' in r else row_key(table,r))
    return normalized


def fingerprint(data):
    return hashlib.sha256(canonical(normalize_order(data)).encode()).hexdigest()


def read_dataset(db):
    db.row_factory = sqlite3.Row
    return normalize_order({t: [dict(r) for r in db.execute(f'SELECT * FROM {t}')] for t in TABLES})


def insert_dataset(db, data):
    for table in TABLES:
        fields = [c['name'] for c in SCHEMA[table]]
        db.executemany(f'INSERT INTO {table} ({",".join(fields)}) VALUES ({",".join("?" for _ in fields)})',
                       [tuple(row[k] for k in fields) for row in data[table]])


def validate(data, mode):
    if mode not in ('actual','example') or not isinstance(data,dict) or set(data) != set(TABLES):
        raise ValueError('전체 백업의 데이터 모드 또는 테이블 목록이 올바르지 않습니다.')
    try:
        if len(canonical(data).encode()) > MAX_BYTES or sum(len(rows) for rows in data.values()) > MAX_ROWS:
            raise ValueError('공용 데이터는 모드별 20MB·50,000개 레코드 이내여야 합니다.')
        for table in TABLES:
            expected = {c['name']: c for c in SCHEMA[table]}
            if not isinstance(data[table],list):
                raise ValueError('전체 백업의 테이블은 목록이어야 합니다.')
            for r in data[table]:
                if not isinstance(r,dict) or set(r)!=set(expected):
                    raise ValueError('전체 백업에 누락되거나 알 수 없는 필드가 있습니다.')
                for k,v in r.items():
                    c=expected[k]
                    required=c['notnull'] or c['pk']
                    if v is None:
                        if required:
                            raise ValueError('필수 값이 비어 있습니다.')
                        continue
                    if c['type']=='TEXT' and (not isinstance(v,str) or '\x00' in v):
                        raise ValueError('문자열 형식이 잘못되었습니다.')
                    if c['type']=='INTEGER' and type(v) is not int:
                        raise ValueError('정수 형식이 잘못되었습니다.')
                    if c['type']=='REAL' and type(v) not in (int,float):
                        raise ValueError('숫자 형식이 잘못되었습니다.')
                    if k in ('created_at','updated_at','recorded_at'):
                        timestamp(v)
                    if k=='sequence' and v<1:
                        raise ValueError('이력 순번은 양의 정수여야 합니다.')
                    if k=='id' or k.endswith('_id'):
                        valid_id(v)
                        if str(UUID(v))!=v:
                            raise ValueError('표준 UUID 형식이 아닌 ID가 있습니다. 원본을 보존하고 별도 검토하세요.')
        for r in data['sources']:
            validate_source(r)
        for r in data['tags']:
            tag_name(r['name'])
        for r in data['verification_events']:
            validate_event(r)
        from reviewlab.services.document_transfer import validate_metadata
        validate_metadata(data)
        from reviewlab.services.survey_transfer import validate_bundle
        validate_bundle(data, mode)
        if any(p['data_kind'] != mode for p in data['review_projects']):
            raise ValueError('실제/예시 리뷰 프로젝트가 섞여 있습니다.')
        with closing(sqlite3.connect(':memory:')) as db, db:
            db.execute('PRAGMA foreign_keys=ON')
            empty_schema(db,mode)
            insert_dataset(db,data)
            if db.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('전체 백업의 참조 무결성 오류입니다.')
            # 2단계의 기존 원본/정규화 일치·검토·분석 참조 검사도 그대로 재사용한다.
            from reviewlab.services.review_transfer import TABLES as REVIEW_TABLES, FORMAT, HEADER, parse_backup
            from reviewlab.services.transfer import write_csv
            class SchemaReader:
                @contextmanager
                def connect(self):
                    db.row_factory=sqlite3.Row
                    yield db
            reader=SchemaReader();reader.mode=mode
            for project in data['review_projects']:
                pid=project['id']
                review_ids={r['id'] for r in data['reviews'] if r['project_id']==pid}
                records=[]
                for table in REVIEW_TABLES:
                    rows=[r for r in data[table] if (r['id']==pid if table=='review_projects' else
                          r['review_id'] in review_ids if table in ('review_notes','review_checks') else r['project_id']==pid)]
                    if table=='review_checks':rows=sorted(rows,key=lambda r:r['sequence'])
                    for row in rows:
                        payload={k:v for k,v in row.items() if k!='sequence'}
                        records.append([FORMAT,mode,table,canonical(payload)])
                parse_backup(write_csv(HEADER,records),reader)
        return normalize_order(data)
    except (TypeError,KeyError,sqlite3.Error,OverflowError,RecursionError) as error:
        raise ValueError('전체 백업의 구조·자료형·참조가 올바르지 않습니다.') from error


def read_local(path, mode):
    """원본 DB에는 쓰지 않는다. 1단계 DB는 빈 리뷰 테이블을 채운 표현으로 읽는다."""
    path=Path(path).expanduser().resolve()
    if not path.is_file():
        raise ValueError('기존 SQLite 파일을 찾지 못했습니다. 원래 앱의 저장 경로를 확인하세요.')
    try:
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db, db:
            db.execute('PRAGMA trusted_schema=OFF')
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                raise ValueError('SQLite 무결성 검사에 실패했습니다.')
            if db.execute('SELECT data_kind FROM project_metadata WHERE id=1').fetchone() != (mode,):
                raise ValueError('SQLite의 실제/예시 모드가 현재 모드와 다릅니다.')
            versions=[r[0] for r in db.execute('SELECT version FROM schema_migrations ORDER BY version')]
            if versions not in ([1],[1,2],[1,2,3],[1,2,3,4]):
                raise ValueError('지원하지 않는 SQLite 스키마 버전입니다.')
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            needed=TABLES if versions==[1,2,3,4] else V2_TABLES if versions==[1,2,3] else LEGACY_TABLES if versions==[1,2] else TABLES[:4]
            if not set(needed)<=tables:
                raise ValueError('필수 SQLite 테이블이 없습니다.')
            db.row_factory=sqlite3.Row
            data={t: [dict(r) for r in db.execute(f'SELECT * FROM {t}')] if t in needed else [] for t in TABLES}
            return validate(data,mode)
    except sqlite3.Error as error:
        raise ValueError('ReviewLab SQLite 파일을 읽지 못했습니다. 원본은 변경하지 않았습니다.') from error
