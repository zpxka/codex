"""독립 PostgreSQL 통합 테스트. Supabase 서비스/Auth 접속 성공을 뜻하지 않는다.

REVIEWLAB_TEST_PG_CONTAINER를 설정할 때만 전용 Docker DB에 실행한다.
준비: scripts/test_cloud_postgres.py (Supabase auth 스키마의 최소 로컬 대역 포함).
"""
from copy import deepcopy
import json
import os
import subprocess
from uuid import uuid4

import pytest

from reviewlab.cloud.dataset import TABLES,canonical
from reviewlab.models import now

CONTAINER=os.environ.get('REVIEWLAB_TEST_PG_CONTAINER')
pytestmark=pytest.mark.skipif(not CONTAINER,reason='전용 PostgreSQL 테스트 컨테이너 미설정 (실제 Supabase 미접속)')


@pytest.fixture(scope='module', autouse=True)
def owned_test_container():
    # 외부 환경변수로 운영 컨테이너를 지정해도 쓰기 전에 차단한다.
    run_id = os.environ.get('REVIEWLAB_TEST_PG_RUN', '')
    assert CONTAINER and CONTAINER.startswith('reviewlab-pg-test-') and len(run_id) == 32, '전용 runner가 만든 컨테이너만 허용합니다.'
    result = subprocess.run(['docker', 'inspect', '--format', '{{ index .Config.Labels "reviewlab.test-run" }}', CONTAINER],
                            capture_output=True, text=True, encoding='utf-8', check=True)
    assert result.stdout.strip() == run_id, '테스트 컨테이너 소유 확인 실패'


def sql(statement,check=True):
    result=subprocess.run(['docker','exec','-i',CONTAINER,'psql','-U','postgres','-d','reviewlab_test','-v','ON_ERROR_STOP=1','-XAtq'],
                          input=statement,text=True,encoding='utf-8',capture_output=True)
    if check and result.returncode:raise AssertionError(result.stderr)
    return result


def quote(value):return "'"+str(value).replace("'","''")+"'"


def rpc(user,function,args=''):
    uid=quote(user) if user else "''"
    result=sql(f"SET ROLE {'authenticated' if user else 'anon'}; SET request.jwt.claim.sub={uid}; SELECT to_jsonb(public.reviewlab_{function + ('_v3' if function!='create_workspace' else '')}({args}));",False)
    if result.returncode:return result
    return json.loads(result.stdout.strip())


@pytest.fixture
def users():
    a,b=str(uuid4()),str(uuid4())
    sql(f'INSERT INTO auth.users VALUES({quote(a)}),({quote(b)});')
    return a,b


@pytest.fixture
def scope(users):
    a,b=users
    wid=rpc(a,'create_workspace',quote('가상 통합 테스트'))
    return a,b,wid


def args(wid,mode='actual'):return quote(wid)+','+quote(mode)


def commit(user,wid,data,rev=0,operation=None,mode='actual'):
    operation=operation or str(uuid4())
    return rpc(user,'commit',args(wid,mode)+f',{rev},'+quote(operation)+','+quote(canonical(data))+'::jsonb')


def source_row(title='허구 SQL 테스트'):
    return dict(id=str(uuid4()),title=title,authors='가상 팀',publication_date='2025',source_type='기타',original_url='https://example.com/pg',notes='',purpose='',created_at=now(),updated_at=now())


def test_postgres_membership_rls_and_anon_denial(scope):
    a,b,wid=scope
    assert rpc(a,'read',args(wid))['revision']==0
    assert rpc(b,'read',args(wid)).returncode
    assert rpc(None,'read',args(wid)).returncode
    # 권한을 부여받지 않은 authenticated 사용자도 테이블 SELECT는 RLS가 빈 결과로 제한한다.
    result=sql(f"SET ROLE authenticated;SET request.jwt.claim.sub={quote(b)};SELECT count(*) FROM reviewlab.workspace_states WHERE workspace_id={quote(wid)};")
    assert result.stdout.strip()=='0'
    direct=sql(f"SET ROLE authenticated;SET request.jwt.claim.sub={quote(a)};DELETE FROM reviewlab.workspace_states WHERE workspace_id={quote(wid)};",False)
    assert direct.returncode  # 앱 사용자는 CAS를 건너뛰는 직접 쓰기 권한 없음
    other=rpc(b,'create_workspace',quote('다른 팀'))
    assert rpc(a,'read',args(other)).returncode


def test_postgres_atomic_cas_idempotency_and_mode_isolation(scope):
    a,b,wid=scope
    sql(f"INSERT INTO reviewlab.workspace_members VALUES({quote(wid)},{quote(b)},'editor');")
    data={t:[] for t in TABLES};data['sources']=[source_row()]
    operation=str(uuid4())
    result=commit(a,wid,data,operation=operation)
    assert result['revision']==1
    stale=deepcopy(data);stale['sources'][0]['title']='오래된 수정'
    assert commit(b,wid,stale,rev=0).returncode
    assert rpc(a,'read',args(wid))['data']['sources'][0]['title']=='허구 SQL 테스트'
    # 다음 저장 후 과거 요청을 재시도해도 되돌리지 않음.
    newer=deepcopy(data);newer['sources'].append(source_row('새 자료'))
    assert commit(b,wid,newer,rev=1)['revision']==2
    assert commit(a,wid,data,operation=operation)['replayed']
    assert len(rpc(a,'read',args(wid))['data']['sources'])==2
    assert commit(a,wid,newer,operation=operation).returncode
    assert not rpc(a,'read',args(wid,'example'))['data']['sources']


def test_postgres_bad_fk_and_extra_scope_fields_rollback(scope):
    a,_,wid=scope
    data={t:[] for t in TABLES};data['sources']=[source_row()]
    data['review_notes']=[dict(id=str(uuid4()),review_id=str(uuid4()),memo='없는 리뷰',recorded_at=now())]
    assert commit(a,wid,data).returncode
    snapshot=rpc(a,'read',args(wid))
    assert snapshot['revision']==0 and snapshot['data']['sources']==[]
    data['review_notes']=[];data['sources'][0]['workspace_id']=str(uuid4())
    assert commit(a,wid,data).returncode
    assert rpc(a,'read',args(wid))['revision']==0


def test_postgres_all_tables_roundtrip_delete_and_link_retention(scope,tmp_path,source,event):
    from reviewlab.repositories.library import Library
    from reviewlab.repositories.reviews import Reviews
    from reviewlab.services.research import Research
    from reviewlab.services.reviews import ReviewService
    from reviewlab.services.transfer import write_csv
    from reviewlab.services.review_transfer import save_run
    from reviewlab.analysis.reviews import statistics,context,patterns
    from reviewlab.cloud.dataset import read_local,fingerprint
    a,_,wid=scope
    local=Library(tmp_path/'source.db','actual');sid=Research(local).save(source,[local.tags()[0]['id']])
    Research(local).verify(sid,event,True)
    service=ReviewService(local);pid=service.create_project('허구 프로젝트')
    service.upload(pid,write_csv(['content','rating'],[['가상 내용',5]]),'허구 수집',True)
    row=service.repo.rows(pid)[0];service.note(row['id'],'메모');service.check(row['id'],'검토 완료','담당','2025-02-01','근거')
    service.repo.link_sources(pid,[sid])
    rows=service.repo.rows(pid);tables,_=statistics(rows);ctx=context(service.repo.project(pid),rows,rows,{})
    ctx['input_ids']=[row['id']];save_run(service.repo,pid,tables,ctx,patterns(rows))
    data=read_local(local.path,'actual')
    assert commit(a,wid,data)['revision']==1
    restored=rpc(a,'read',args(wid))['data']
    assert fingerprint(restored)==fingerprint(data)
    # 자료가 있는 상태에서 모든 업무 테이블의 RLS를 검사한다(빈 결과의 우연 방지).
    for table in TABLES[:11]:
        assert data[table]
        member = sql(f"SET ROLE authenticated;SET request.jwt.claim.sub={quote(a)};SELECT count(*) FROM reviewlab.{table} WHERE workspace_id={quote(wid)};")
        outsider = sql(f"SET ROLE authenticated;SET request.jwt.claim.sub={quote(scope[1])};SELECT count(*) FROM reviewlab.{table} WHERE workspace_id={quote(wid)};")
        assert int(member.stdout.strip()) == len(data[table])
        assert outsider.stdout.strip() == '0'
        denied = sql(f"SET ROLE authenticated;SET request.jwt.claim.sub={quote(a)};DELETE FROM reviewlab.{table} WHERE workspace_id={quote(wid)};", False)
        assert denied.returncode
    Research(local).delete(sid,True)
    changed=read_local(local.path,'actual')
    assert commit(a,wid,changed,rev=1)['revision']==2
    restored=rpc(a,'read',args(wid))['data']
    assert restored['sources']==[] and restored['review_source_links'][0]['source_id'] is None
    assert len(restored['reviews'])==1


def test_postgres_concurrent_writers_and_revoked_membership(scope):
    from concurrent.futures import ThreadPoolExecutor
    a,b,wid=scope
    sql(f"INSERT INTO reviewlab.workspace_members VALUES({quote(wid)},{quote(b)},'editor');")
    one={t:[] for t in TABLES};one['sources']=[source_row('동시 저장 A')]
    two={t:[] for t in TABLES};two['sources']=[source_row('동시 저장 B')]
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs=[pool.submit(commit,a,wid,one),pool.submit(commit,b,wid,two)]
        results=[job.result() for job in jobs]
    assert sum(isinstance(r,dict) for r in results)==1
    assert sum(isinstance(r,subprocess.CompletedProcess) and r.returncode!=0 for r in results)==1
    assert rpc(a,'read',args(wid))['revision']==1
    sql(f'DELETE FROM reviewlab.workspace_members WHERE workspace_id={quote(wid)} AND user_id={quote(b)};')
    assert rpc(b,'read',args(wid)).returncode
    assert commit(b,wid,two,rev=1).returncode


def test_postgres_cloud_adapter_two_clients_and_migration(scope,tmp_path,source,event):
    from reviewlab.cloud.client import CloudError,CloudConflict
    from reviewlab.cloud.library import CloudLibrary
    from reviewlab.cloud.migration import prepare,execute
    from reviewlab.repositories.library import Library
    from reviewlab.services.research import Research
    a,b,wid=scope
    sql(f"INSERT INTO reviewlab.workspace_members VALUES({quote(wid)},{quote(b)},'editor');")
    class SQLClient:
        url='https://local-postgres-test.example.invalid'
        def __init__(self,user):self.user_id=user
        def rpc(self,name,params=None):
            p=params or {}
            if name in ('read','status'):result=rpc(self.user_id,name,args(p['p_workspace'],p['p_mode']))
            elif name=='commit':result=commit(self.user_id,p['p_workspace'],p['p_data'],p['p_expected_revision'],p['p_operation'],p['p_mode'])
            else:raise AssertionError(name)
            if isinstance(result,subprocess.CompletedProcess):
                if 'RL_CONFLICT' in result.stderr:raise CloudConflict('PostgreSQL 테스트 충돌')
                raise CloudError('PostgreSQL 테스트 RPC 실패')
            return result
    first=CloudLibrary(SQLClient(a),wid,'actual',tmp_path/'laptop')
    second=CloudLibrary(SQLClient(b),wid,'actual',tmp_path/'desktop')
    local=Library(tmp_path/'original.db','actual');sid=Research(local).save(source)
    Research(local).verify(sid,event,True)
    execute(first,prepare(first,local.path),True,True,True)
    second.reload();assert second.list_sources()==first.list_sources()
    Research(first).save(dict(source,title='노트북 수정'),source_id=sid)
    with pytest.raises(CloudConflict):Research(second).save(dict(source,title='데스크탑 오래된 수정'),source_id=sid)
    second.reload();assert second.list_sources()[0]['title']=='노트북 수정'
    assert local.list_sources()[0]['title']==source['title']
    first.close();second.close()
