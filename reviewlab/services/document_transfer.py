"""전체 메타데이터 + 원문/추출 파일을 묶는 명시적 백업. 경로를 신뢰하지 않는다."""
import io,json,re,zipfile
from reviewlab.services.documents import Files,digest,MAX_FILE,MAX_TEXT,packed,CATEGORIES,extract


def validate_metadata(data):
    docs={r['id']:r for r in data['document_files']}
    for d in docs.values():
        if not isinstance(json.loads(d['reference_json']),dict):raise ValueError('참고문헌 스냅샷 오류')
        if d['kind'] not in ('source','draft') or d['format'] not in ('pdf','docx','txt') or d['status'] not in ('ready','encrypted','ocr_needed','partial_ocr_needed','empty','extraction_failed'):raise ValueError('문서 메타데이터 오류')
        if not all(re.fullmatch('[a-f0-9]{64}',d[k]) for k in ('sha256','text_sha256')):raise ValueError('문서 해시 오류')
    for n in data['document_notes']:
        if n['category'] not in CATEGORIES or n['state'] not in ('미확인','연구자 확인','보류') or not n['content'].strip():raise ValueError('분석 기록 상태 오류')
        scope=json.loads(n['scope_json']);evidence=json.loads(n['evidence_json'])
        if not isinstance(scope,list) or not set(scope)<=set(docs) or n['document_id'] not in scope or not evidence:raise ValueError('근거 문서 범위 오류')
        for e in evidence:
            if docs[e['document_id']]['status']!='ready':raise ValueError('정상 추출된 원문에만 근거를 연결할 수 있습니다.')
            if e['document_id'] not in scope or not isinstance(e['quote'],str) or not e['quote'].strip() or not isinstance(e['location'],str):raise ValueError('근거 위치 오류')


def export_bundle(library):
    from reviewlab.cloud.dataset import read_dataset
    files=Files(library)
    with library.connect() as db:data=read_dataset(db)
    out=io.BytesIO();total=0
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        manifest=packed(dict(format='reviewlab-files-v1',mode=library.mode,data=data))
        total=len(manifest)
        z.writestr('manifest.json',manifest)
        hashes={r[k] for r in data['document_files'] for k in ('sha256','text_sha256')}
        if len(hashes)>1999:raise ValueError('백업 파일 항목 한도 초과: 1,999개 파일 이내만 지원합니다.')
        for sha in sorted(hashes):
            blob=files.get(sha);total+=len(blob)
            if total>100*1024*1024:raise ValueError('파일·메타데이터 백업은 총 100MB 이내로 제한됩니다.')
            z.writestr(sha,blob)
    return out.getvalue()


def preview_bundle(library,blob):
    from reviewlab.cloud.dataset import validate,read_dataset
    from reviewlab.cloud.migration import plan
    if len(blob)>120*1024*1024:raise ValueError('백업 크기 한도 초과')
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            names=z.namelist()
            if len(names)!=len(set(names)) or len(names)>2000 or sum(x.file_size for x in z.infolist())>120*1024*1024:raise ValueError('백업 압축 한도/중복 오류')
            manifest=json.loads(z.read('manifest.json'))
            if manifest['format']!='reviewlab-files-v1' or manifest['mode']!=library.mode:raise ValueError('백업 형식/모드 오류')
            data=validate(manifest['data'],library.mode)
            hashes={d[k] for d in data['document_files'] for k in ('sha256','text_sha256')}
            if set(names)!={'manifest.json',*hashes}:raise ValueError('파일 누락 또는 알 수 없는 항목')
            files={sha:z.read(sha) for sha in hashes}
            if any(len(b)>MAX_FILE or digest(b)!=sha for sha,b in files.items()):raise ValueError('백업 파일 해시 오류')
            extracted={d['id']:json.loads(files[d['text_sha256']]) for d in data['document_files']}
            if any(not isinstance(value,dict) or value.get('extractor')!='reviewlab-v1' for value in extracted.values()):raise ValueError('지원하지 않는 추출 버전')
            units={key:value['units'] for key,value in extracted.items()}
            for d in data['document_files']:
                if len(files[d['text_sha256']])>MAX_TEXT:raise ValueError('추출 파일 한도 오류')
                values=units[d['id']]
                if not isinstance(values,list) or not all(isinstance(u,dict) and set(u)=={'location','text'} and isinstance(u['location'],str) and isinstance(u['text'],str) for u in values):raise ValueError('추출 구조 오류')
                if d['status']=='ready' and not any(u['text'].strip() for u in values):raise ValueError('추출 완료 상태 불일치')
                status,original=extract(files[d['sha256']],d['format'])
                if status!=d['status'] or original!=values:raise ValueError('원본과 추출 텍스트가 일치하지 않습니다.')
            for n in data['document_notes']:
                for e in json.loads(n['evidence_json']):
                    if not any(u['location']==e['location'] and e['quote'] in u['text'] for u in units[e['document_id']]):raise ValueError('백업 근거 불일치')
        with library.connect() as db:current=read_dataset(db)
        result=plan(data,current,library.mode);result['files']=files
        return result
    except (KeyError,TypeError,zipfile.BadZipFile,UnicodeError):raise ValueError('파일 백업이 손상되었습니다.') from None


def restore_bundle(library,blob,confirmed=False):
    if not confirmed:raise ValueError('중복·태그 재사용과 전체 추가 복원 내용을 확인하세요.')
    from reviewlab.cloud.dataset import read_dataset,fingerprint,TABLES,row_key
    from reviewlab.services.documents import Documents
    result=preview_bundle(library,blob)
    if result['errors']:raise ValueError('충돌이 있어 복원을 중단했습니다.')
    for content in result['files'].values():Files(library).put(content)
    library.snapshot()
    if getattr(library,'is_cloud',False):
        if fingerprint(library.dataset())!=result['target_fingerprint']:raise ValueError('미리보기 후 데이터 변경')
        library.replace_dataset(result['data'],library.revision)
    else:
        with library.connect() as db:
            db.execute('BEGIN IMMEDIATE');current=read_dataset(db)
            if fingerprint(current)!=result['target_fingerprint']:raise ValueError('미리보기 후 데이터 변경')
            for t in TABLES:
                existing={row_key(t,r) for r in current[t]}
                for r in result['data'][t]:
                    if row_key(t,r) not in existing:Documents.insert(db,t,r)
    return result['added']
