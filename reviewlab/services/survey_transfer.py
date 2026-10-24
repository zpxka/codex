"""설문 전체 백업/추가 복원. 기존 자료를 덮어쓰지 않는다."""
import json
import hashlib
from reviewlab.services.surveys import TABLES,validate_config,value
from reviewlab.services.transfer import write_csv


def validate_bundle(data,mode):
    projects={r['id']:r for r in data['survey_projects']}
    questions={r['id']:r for r in data['survey_questions']}
    responses={r['id']:r for r in data['survey_responses']}
    imports={r['id']:r for r in data['survey_imports']}
    for p in projects.values():
        if p['data_kind']!=mode or not p['name'].strip(): raise ValueError('설문 프로젝트 모드·이름을 확인하세요.')
        from datetime import date
        for k in ('period_start','period_end'):
            if p[k]: date.fromisoformat(p[k])
        if p['period_start'] and p['period_end'] and p['period_start']>p['period_end']: raise ValueError('조사 기간 오류입니다.')
    for q in questions.values():
        validate_config(json.loads(q['config_json']))
        if q['project_id'] not in projects or not q['variable'].strip() or not q['name'].strip(): raise ValueError('설문 문항 참조 오류입니다.')
    for r in imports.values():
        if len(r['file_sha256'])!=64 or any(c not in '0123456789abcdef' for c in r['file_sha256']): raise ValueError('설문 원본 해시 오류입니다.')
        if not isinstance(json.loads(r['settings_json']),dict): raise ValueError('업로드 조건 오류입니다.')
    for r in responses.values():
        if r['source_row']<2 or imports[r['import_id']]['project_id']!=r['project_id']: raise ValueError('응답의 수집 기록 참조 오류입니다.')
    for a in data['survey_answers']:
        q=questions[a['question_id']];r=responses[a['response_id']]
        if q['project_id']!=r['project_id']: raise ValueError('다른 프로젝트의 응답 문항이 섞여 있습니다.')
        value(a['raw_value'],json.loads(q['config_json']))
    project_questions={}
    for q in questions.values(): project_questions.setdefault(q['project_id'],set()).add(q['id'])
    response_questions={}
    for a in data['survey_answers']: response_questions.setdefault(a['response_id'],[]).append(a['question_id'])
    for rid,r in responses.items():
        expected=project_questions.get(r['project_id'],set())
        found=response_questions.get(rid,[])
        if set(found)!=expected or len(found)!=len(expected): raise ValueError('응답의 문항별 원본이 누락·중복되었습니다.')
    for r in data['survey_analysis_runs']:
        settings=json.loads(r['config_json'])
        ids=settings.get('input_ids',[])
        if not isinstance(ids,list) or any(i not in responses or responses[i]['project_id']!=r['project_id'] for i in ids): raise ValueError('분석 입력 참조가 올바르지 않습니다.')
        if not all(isinstance(json.loads(r[k]),dict) for k in ('config_json','results_json')): raise ValueError('분석 이력 형식 오류입니다.')


def export_backup(library):
    from reviewlab.cloud.dataset import read_dataset,canonical
    with library.connect() as db: data=read_dataset(db)
    return write_csv(['format','mode','payload'],[['reviewlab-survey-v1',library.mode,canonical({t:data[t] for t in TABLES})]])


def preview(library,blob):
    import csv,io
    from reviewlab.cloud.dataset import validate,read_dataset,fingerprint
    if len(blob)>20*1024*1024: raise ValueError('설문 백업은 20MB 이하여야 합니다.')
    csv.field_size_limit(20*1024*1024)
    try:
        records=list(csv.DictReader(io.StringIO(blob.decode('utf-8-sig')),strict=True))
        if len(records)!=1 or set(records[0])!={'format','mode','payload'}: raise ValueError('설문 백업 형식·버전·모드를 확인하세요.')
        r=records[0]
        if r['format']!='reviewlab-survey-v1' or r['mode']!=library.mode: raise ValueError('설문 백업 형식·버전·모드를 확인하세요.')
        incoming=json.loads(r['payload'])
        if set(incoming)!=set(TABLES): raise ValueError('설문 백업 형식·버전·모드를 확인하세요.')
        with library.connect() as db: target=read_dataset(db)
        candidate={t:(incoming[t] if t in TABLES else rows) for t,rows in target.items()}
        validate(candidate,library.mode)  # 기존 데이터가 손상된 백업의 누락을 가리지 않게 먼저 검사
        merged={t:list(rows) for t,rows in target.items()};added=0
        for t in TABLES:
            old={r['id']:r for r in target[t]}
            for row in incoming[t]:
                if row['id'] in old:
                    if row!=old[row['id']]: raise ValueError('같은 ID에 다른 데이터가 있습니다. 덮어쓰지 않습니다.')
                else: merged[t].append(row);added+=1
        validate(merged,library.mode)
        return dict(data=merged,added=added,fingerprint=fingerprint(target))
    except (KeyError,TypeError,UnicodeError,csv.Error,json.JSONDecodeError):
        raise ValueError('설문 백업 형식·참조가 손상되었습니다.') from None


def restore(library,blob,confirmed=False):
    if not confirmed: raise ValueError('추가 복원 정책을 확인하세요.')
    from reviewlab.cloud.dataset import read_dataset,fingerprint,SCHEMA
    result=preview(library,blob)
    library.snapshot()
    from reviewlab.services.surveys import Surveys
    with library.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        current=read_dataset(db)
        if fingerprint(current)!=result['fingerprint']: raise ValueError('자료가 변경되었습니다. 다시 미리보기하세요.')
        for t in TABLES:
            ids={r['id'] for r in current[t]}
            for r in result['data'][t]:
                if r['id'] not in ids: Surveys.insert(db,t,r)
    return result['added']
