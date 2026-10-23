"""SQLite → 공용 데이터의 추가 전용 이전. 중복은 명시적 확인 없이는 실행하지 않는다."""
from copy import deepcopy
from pathlib import Path
import sqlite3
from uuid import uuid4

from reviewlab.cloud.client import CloudError, CloudConflict
from reviewlab.cloud.dataset import TABLES, canonical, row_key, read_local, validate, fingerprint
from reviewlab.cloud.library import private_file
from reviewlab.services.research import source_key


def freeze_local(path,mode):
    source=Path(path).expanduser().resolve()
    if not source.is_file():raise CloudError('기존 SQLite 파일이 없습니다. 저장 경로를 확인하세요.')
    destination=source.parent/'backups'/f'before-cloud-{uuid4()}.sqlite3'
    destination.parent.mkdir(parents=True,exist_ok=True)
    try:
        with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as db,sqlite3.connect(destination) as target:
            db.backup(target)
        private_file(destination)
        return destination,read_local(destination,mode)
    except (OSError,sqlite3.Error,ValueError):
        raise CloudError('이전 전 사본 생성 또는 검사에 실패했습니다. 원본 SQLite는 변경하지 않았습니다. 경로·모드·디스크 공간을 확인하세요.') from None


def without_sequence(row):
    return {k:v for k,v in row.items() if k!='sequence'}


def child_signature(data,table,predicate):
    rows=[r for r in data[table] if predicate(r)]
    if rows and 'sequence' in rows[0]:
        return [canonical(without_sequence(r)) for r in sorted(rows,key=lambda r:r['sequence'])]
    return sorted(canonical(r) for r in rows)


def project_signature(data,pid):
    ids={r['id'] for r in data['reviews'] if r['project_id']==pid}
    result={}
    for table in TABLES[4:]:
        result[table]=child_signature(data,table,lambda r: r['id']==pid if table=='review_projects' else
            r['review_id'] in ids if table in ('review_notes','review_checks') else r['project_id']==pid)
    return result


def plan(incoming,target,mode):
    incoming=deepcopy(validate(incoming,mode)); target=deepcopy(validate(target,mode))
    errors=[]; duplicates=[]; tag_reuse=[]
    existing_tags={r['id']:r for r in target['tags']}; names={r['name']:r['id'] for r in target['tags']}
    tag_map={}
    for tag in incoming['tags']:
        if tag['id'] in existing_tags and tag!=existing_tags[tag['id']]:
            errors.append(dict(table='tags',id=tag['id'],reason='같은 태그 ID에 다른 이름이 있습니다.'))
        tag_map[tag['id']]=names.get(tag['name'],tag['id'])
        if tag_map[tag['id']]!=tag['id']:
            tag_reuse.append(dict(name=tag['name'],original_id=tag['id'],target_id=tag_map[tag['id']]))
    for row in incoming['source_tags']:row['tag_id']=tag_map[row['tag_id']]
    incoming['tags']=[dict(r,id=tag_map[r['id']]) for r in incoming['tags']]
    sources={r['id']:r for r in target['sources']}
    url_map={r['original_url']:r for r in target['sources']}; key_map={source_key(r):r for r in target['sources']}
    for r in incoming['sources']:
        if r['id'] in sources:
            # 같은 ID의 자료는 원문뿐 아니라 태그·검증 이력도 같아야 한다.
            for table in ('source_tags','verification_events'):
                pred=lambda x: x['source_id']==r['id']
                if child_signature(incoming,table,pred)!=child_signature(target,table,pred):
                    errors.append(dict(table=table,id=r['id'],reason='같은 자료 ID의 태그 또는 검증 이력이 다릅니다. 자동 병합하지 않습니다.'))
        else:
            match=url_map.get(r['original_url']) or key_map.get(source_key(r))
            if match:
                duplicates.append(dict(table='sources',id=r['id'],title=r['title'],existing_id=match['id'],reason='URL 또는 제목·저자·발행일 중복 후보'))
            url_map.setdefault(r['original_url'],r);key_map.setdefault(source_key(r),r)
    projects={r['id']:r for r in target['review_projects']}
    for r in incoming['review_projects']:
        if r['id'] in projects and project_signature(incoming,r['id'])!=project_signature(target,r['id']):
            errors.append(dict(table='review_projects',id=r['id'],reason='같은 프로젝트 ID의 데이터·메모·검토·분석 기록이 다릅니다.'))
        elif r['id'] not in projects:
            match=next((p for p in projects.values() if p['name']==r['name']),None)
            if match:duplicates.append(dict(table='review_projects',id=r['id'],title=r['name'],existing_id=match['id'],reason='동일 이름 프로젝트 후보'))
    merged=deepcopy(target);counts={};skipped={}
    for table in TABLES:
        existing={row_key(table,r):r for r in target[table]}
        sequence=max((r.get('sequence',0) for r in target[table]),default=0)
        counts[table]=0;skipped[table]=0
        ordered=sorted(incoming[table],key=lambda r:r.get('sequence',0))
        for r in ordered:
            key=row_key(table,r)
            if key in existing:
                if without_sequence(r)!=without_sequence(existing[key]):
                    errors.append(dict(table=table,id=str(key),reason='동일 ID의 내용이 다릅니다. 기존 자료를 덮어쓰지 않습니다.'))
                skipped[table]+=1
                continue
            row=deepcopy(r)
            if 'sequence' in row:
                sequence+=1;row['sequence']=sequence
            merged[table].append(row);existing[key]=row;counts[table]+=1
    if not errors:
        try:merged=validate(merged,mode)
        except ValueError as error:errors.append(dict(table='all',id='',reason=str(error)))
    return dict(data=merged,added=counts,skipped=skipped,errors=errors,duplicates=duplicates,tag_reuse=tag_reuse,
                incoming_fingerprint=fingerprint(incoming),target_fingerprint=fingerprint(target))


def prepare(library,source_path):
    if library._broken:
        raise CloudError('미확인 저장 요청을 처리하거나 공용 자료를 새로고침한 뒤 이전하세요.')
    backup,data=freeze_local(source_path,library.mode)
    result=plan(data,library.dataset(),library.mode)
    result.update(backup_path=backup,expected_revision=library.revision,workspace_id=library.workspace_id,mode=library.mode)
    return result


def execute(library,preview,confirmed=False,keep_duplicates=False,reuse_tags=False):
    if not confirmed:raise CloudError('이전 전 백업·미리보기·과거 검증 이력 승계를 확인하세요.')
    if preview.get('workspace_id')!=library.workspace_id or preview.get('mode')!=library.mode:
        raise CloudError('이전 대상 작업공간 또는 실제/예시 모드가 바뀌었습니다. 미리보기를 다시 만드세요.')
    if preview['errors']:raise CloudError('ID 충돌 또는 오류가 있어 전체 이전을 차단했습니다.')
    if preview['duplicates'] and not keep_duplicates:
        raise CloudError('중복 후보를 확인하고 별도 자료로 모두 보존할지 선택하세요.')
    if preview['tag_reuse'] and not reuse_tags:
        raise CloudError('같은 이름 태그의 연결 재사용을 확인하세요. 자료 자체는 합치지 않습니다.')
    if fingerprint(library.dataset())!=preview['target_fingerprint']:
        raise CloudConflict('미리보기 이후 대상 자료가 변경되었습니다. 이전 미리보기를 다시 만드세요.')
    if any(preview['added'].values()):
        library.replace_dataset(preview['data'],preview['expected_revision'])
    return preview['added']
