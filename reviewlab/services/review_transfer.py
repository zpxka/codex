"""리뷰 프로젝트 단위의 추가 전용 백업. 참고문헌 CSV와 별도 형식."""
import csv
import io
import json
import math
import sqlite3
from pathlib import Path
from uuid import uuid4
from reviewlab.models import now, valid_id, timestamp
from reviewlab.services.reviews import LABELS, STATES, LIMIT, day, normalize, encode_json
from reviewlab.services.transfer import write_csv, safe_cell, download_filename
from reviewlab.repositories.reviews import Reviews

FORMAT='reviewlab-reviews-v1'
HEADER=['format','mode','record_type','payload']
TABLES=('review_projects','review_imports','reviews','review_notes','review_checks','review_source_links','review_analysis_runs')
MAX_BYTES=50*1024*1024


def strict_json(value):
    def invalid_constant(token):
        raise ValueError('백업 JSON에는 NaN/Infinity를 사용할 수 없습니다.')
    return json.loads(value, parse_constant=invalid_constant)


def filename(mode,kind):
    return download_filename(mode,'backup').replace('-backup-',f'-reviews-{kind}-')


def export_reviews(repo,pid):
    rows=[]
    for r in repo.rows(pid):
        check=r['checks'][-1] if r['checks'] else {}
        values=[r['id'],repo.library.mode]+[r['raw'].get(k,'미확인') for k in LABELS]+[
            ' | '.join(n['memo'] for n in r['notes']),r['status'],check.get('reviewer',''),check.get('checked_on',''),check.get('evidence',''),check.get('memo','')]
        rows.append([safe_cell(v) for v in values])
    return write_csv(['내부 ID','저장 모드',*LABELS.values(),'추가 메모','검토 상태','검토자','검토일','판단 근거','검토 메모'],rows)


def result_csv(tables,context,pattern=None):
    rows=[]
    for name,df in tables.items():
        for i,record in enumerate(strict_json(df.to_json(orient='records',force_ascii=False))):
            for k,v in record.items():
                rows.append([name,i,k,'' if v is None else v,encode_json(context)])
    if pattern:
        for name in ('exact','phrases','expressions','similar'):
            for i,record in enumerate(pattern[name]):
                for k,v in record.items():
                    rows.append([name,i,k,v,encode_json(dict(context,pattern_parameters=pattern['parameters']))])
    return write_csv(['분석표','행','필드','값','분석 조건 JSON'],[[safe_cell(v) for v in row] for row in rows])


def save_run(repo,pid,tables,context,pattern):
    record=dict(id=str(uuid4()),project_id=pid,filters_json=encode_json(context['filters']),
        parameters_json=encode_json(pattern['parameters']),input_ids_json=encode_json(context['input_ids']),
        results_json=encode_json(dict(context=context,tables={k:strict_json(v.to_json(orient='records',force_ascii=False)) for k,v in tables.items()},patterns=pattern)),
        created_at=now(),tool_version='reviewlab-0.2.0')
    with repo.library.connect() as db:
        repo.insert(db,'review_analysis_runs',record)
    return record['id']


def export_backup(repo,pid):
    result=[]
    with repo.library.connect() as db:
        db.execute('BEGIN')
        for table in TABLES:
            if table=='review_projects':
                query='SELECT * FROM review_projects WHERE id=?'
            elif table in ('review_notes','review_checks'):
                query=f'SELECT t.* FROM {table} t JOIN reviews r ON r.id=t.review_id WHERE r.project_id=? ORDER BY '+('t.sequence' if table=='review_checks' else 't.recorded_at,t.id')
            else:
                query=f'SELECT * FROM {table} WHERE project_id=? ORDER BY '+('referenced_id' if table=='review_source_links' else 'id')
            for row in db.execute(query,(pid,)):
                record=dict(row)
                record.pop('sequence',None)
                result.append([FORMAT,repo.library.mode,table,encode_json(record)])
    data=write_csv(HEADER,result)
    if len(data)>MAX_BYTES:
        raise ValueError('CSV 백업 한도 50MB를 넘었습니다. 자동 SQLite 백업을 보관하세요.')
    return data


def parse_backup(content,library):
    if len(content)>MAX_BYTES:
        raise ValueError('리뷰 백업은 50MB 이하여야 합니다.')
    csv.field_size_limit(MAX_BYTES)
    bundle={t:[] for t in TABLES}
    try:
        reader=csv.DictReader(io.StringIO(content.decode('utf-8-sig')),strict=True)
        if reader.fieldnames!=HEADER:
            raise ValueError('리뷰 전용 백업 CSV를 선택하세요. 목록 CSV는 복원용이 아닙니다.')
        with library.connect() as db:
            schema={t:{r['name']:r['type'] for r in db.execute(f'PRAGMA table_info({t})') if r['name']!='sequence'} for t in TABLES}
            fields={t:set(columns) for t,columns in schema.items()}
        for index,row in enumerate(reader):
            if index>100000 or set(row)!=set(HEADER) or row['format']!=FORMAT or row['mode']!=library.mode or row['record_type'] not in TABLES:
                raise ValueError('백업 형식·모드·레코드 수가 올바르지 않습니다.')
            t=row['record_type']
            r=strict_json(row['payload'])
            if not isinstance(r,dict) or set(r)!=fields[t]:
                raise ValueError('백업의 필수 필드가 없거나 알 수 없는 필드가 있습니다.')
            for key,v in r.items():
                if v is not None and ((schema[t][key]=='TEXT' and (not isinstance(v,str) or '\x00' in v)) or (schema[t][key]=='INTEGER' and type(v) is not int) or (schema[t][key]=='REAL' and type(v) not in (int,float))):
                    raise ValueError('백업 필드의 자료형이 올바르지 않습니다.')
                if key in ('id','project_id','import_id','review_id','referenced_id') or (key=='source_id' and v is not None):
                    valid_id(v)
                if key in ('created_at','recorded_at'):
                    timestamp(v)
                if isinstance(v,(dict,list)) or (isinstance(v,float) and not math.isfinite(v)):
                    raise ValueError('백업 값 형식이 올바르지 않습니다.')
            bundle[t].append(r)
        if len(bundle['review_projects'])!=1 or len(bundle['reviews'])>LIMIT:
            raise ValueError('백업에는 단일 프로젝트와 최대 5,000개 리뷰가 있어야 합니다.')
        p=bundle['review_projects'][0]
        if p['data_kind']!=library.mode or not isinstance(p['name'],str) or not p['name'].strip() or not math.isfinite(p['rating_min']) or not math.isfinite(p['rating_max']) or p['rating_max']<=p['rating_min']:
            raise ValueError('프로젝트 모드·이름·별점 범위가 잘못되었습니다.')
        for t,records in bundle.items():
            for r in records:
                if 'project_id' in r and r['project_id']!=p['id']:
                    raise ValueError('다른 프로젝트의 레코드가 섞여 있습니다.')
        for r in bundle['review_imports']:
            mapping=strict_json(r['mapping_json'])
            if not isinstance(mapping,dict) or set(mapping)-set(LABELS) or any(not isinstance(v,str) for v in mapping.values()) or r['encoding'] not in ('utf-8-sig','cp949'):
                raise ValueError('수집 기록의 열 연결·인코딩이 잘못되었습니다.')
            if not r['source_note'].strip() or len(r['file_sha256'])!=64 or any(c not in '0123456789abcdef' for c in r['file_sha256']):
                raise ValueError('수집 경위 또는 파일 해시가 올바르지 않습니다.')
            if any(type(r[k]) is not int or r[k]<0 for k in ('total_rows','imported_rows','skipped_rows','ignored_columns')) or r['total_rows']!=r['imported_rows']+r['skipped_rows']:
                raise ValueError('수집 기록의 행 개수가 잘못되었습니다.')
        for r in bundle['reviews']:
            raw=strict_json(r['raw_json'])
            if not isinstance(raw,dict):
                raise ValueError('원본 필드 형식 오류입니다.')
            normalized=normalize(raw,p)
            if any(r[k]!=v for k,v in normalized.items()) or type(r['source_row']) is not int or r['source_row']<2:
                raise ValueError('보존 원본과 정규화된 리뷰가 일치하지 않습니다.')
        for r in bundle['review_notes']:
            if not r['memo'].strip() or len(r['memo'])>30000:
                raise ValueError('연구자 메모가 비었거나 너무 깁니다.')
        for r in bundle['review_checks']:
            if r['status'] not in STATES or not r['reviewer'].strip() or not r['checked_on'] or (r['status'] in ('검토 완료','분석 제외') and not r['evidence'].strip()):
                raise ValueError('검토 기록의 필수 값이 없습니다.')
            day(r['checked_on'])
        review_ids={r['id'] for r in bundle['reviews']}
        for r in bundle['review_imports']:
            if sum(v['import_id']==r['id'] for v in bundle['reviews'])!=r['imported_rows']:
                raise ValueError('수집 기록과 리뷰 개수가 일치하지 않습니다.')
        for r in bundle['review_analysis_runs']:
            for k in ('filters_json','parameters_json','results_json'):
                if not isinstance(strict_json(r[k]),dict):
                    raise ValueError('분석 기록 형식이 올바르지 않습니다.')
            ids=strict_json(r['input_ids_json'])
            if not isinstance(ids,list) or any(not isinstance(i,str) or i not in review_ids for i in ids):
                raise ValueError('분석 기록의 입력 리뷰 참조가 올바르지 않습니다.')
        # 메모리 DB에서 모든 제약/참조/중복을 먼저 검증한다. 실제 DB는 쓰지 않는다.
        with sqlite3.connect(':memory:') as db:
            db.execute('PRAGMA foreign_keys=ON')
            root=Path(__file__).parents[1]/'db/migrations'
            db.executescript((root/'001_initial.sql').read_text()+(root/'002_reviews.sql').read_text())
            for t in TABLES:
                for r in bundle[t]:
                    record=dict(r,source_id=None) if t=='review_source_links' else r
                    Reviews.insert(db,t,record)
            if db.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('백업 참조 무결성 오류입니다.')
        return bundle
    except (UnicodeError,csv.Error,json.JSONDecodeError,KeyError,TypeError,AttributeError,sqlite3.Error,OverflowError,RecursionError) as e:
        raise ValueError('손상되거나 지원하지 않는 리뷰 백업입니다. 기존 데이터는 변경하지 않았습니다.') from e


def preview_restore(library,content):
    bundle=parse_backup(content,library)
    pid=bundle['review_projects'][0]['id']
    with library.connect() as db:
        exists=db.execute('SELECT 1 FROM review_projects WHERE id=?',(pid,)).fetchone() is not None
        if not exists:
            for table in TABLES:
                if table=='review_source_links':
                    continue
                for r in bundle[table]:
                    if db.execute(f'SELECT 1 FROM {table} WHERE id=?',(r['id'],)).fetchone():
                        raise ValueError('다른 프로젝트와 내부 ID가 충돌합니다. 복원을 차단했습니다.')
    return dict(added=0 if exists else len(bundle['reviews']),skipped=len(bundle['reviews']) if exists else 0,
        duplicate_project=exists,errors=0,project=bundle['review_projects'][0]['name'])


def restore(library,content,confirmed=False):
    if not confirmed:
        raise ValueError('복원 정책과 과거 검토 이력 승계를 확인하세요.')
    bundle=parse_backup(content,library)
    library.snapshot()
    with library.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        report=preview_restore(library,content)
        if report['duplicate_project']:
            return report
        for t in TABLES:
            for r in bundle[t]:
                record=dict(r)
                if t=='review_source_links':
                    known=db.execute('SELECT 1 FROM sources WHERE id=?',(r['referenced_id'],)).fetchone()
                    record['source_id']=r['referenced_id'] if known else None
                Reviews.insert(db,t,record)
    return report
