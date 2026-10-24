"""임시 PostgreSQL: 설문 RLS/CAS/멱등성 및 실제 v1→v2 보존 검증."""
import json
import os
from pathlib import Path
import subprocess
from uuid import uuid4
import pytest
from test_cloud_postgres import owned_test_container,sql,rpc,args,commit,quote,source_row
from test_surveys import seed
from reviewlab.services.surveys import TABLES
from reviewlab.cloud.dataset import read_local,fingerprint
from reviewlab.repositories.library import Library

pytestmark=pytest.mark.skipif(not os.environ.get('REVIEWLAB_TEST_PG_CONTAINER'),reason='전용 PostgreSQL 컨테이너 미설정; 실제 Supabase 아님')


def test_survey_postgres_all_tables_rls_mode_cas_and_old_client_block(tmp_path):
    a,b=str(uuid4()),str(uuid4())
    sql(f'INSERT INTO auth.users VALUES({quote(a)}),({quote(b)});')
    wid=rpc(a,'create_workspace',quote('가상 설문 통합'))
    local=Library(tmp_path/'survey.db','actual');service,pid,_,_=seed(local);service.save_analysis(pid,{'input_ids':service.frame(pid)[0]['id'].tolist()},{'test':'허구 분석'})
    data=read_local(local.path,'actual');operation=str(uuid4())
    assert commit(a,wid,data,operation=operation)['revision']==1
    assert commit(a,wid,data,operation=operation)['replayed']
    assert fingerprint(rpc(a,'read',args(wid))['data'])==fingerprint(data)
    for table in TABLES:
        assert data[table]
        result=sql(f"SET ROLE authenticated; SET request.jwt.claim.sub={quote(b)}; SELECT count(*) FROM reviewlab.{table} WHERE workspace_id={quote(wid)};")
        assert result.stdout.strip()=='0'
        assert sql(f"SET ROLE authenticated; SET request.jwt.claim.sub={quote(a)}; DELETE FROM reviewlab.{table} WHERE workspace_id={quote(wid)};",False).returncode
    old=sql(f"SET ROLE authenticated;SET request.jwt.claim.sub={quote(a)};SELECT public.reviewlab_commit({args(wid)},1,{quote(str(uuid4()))},'{{}}'::jsonb);",False)
    assert old.returncode and 'RL_SCHEMA_UPGRADE_REQUIRED' in old.stderr
    assert not rpc(a,'read',args(wid,'example'))['data']['survey_projects']
    assert commit(a,wid,data,rev=0).returncode
    assert rpc(b,'read',args(wid)).returncode


def test_actual_v1_to_v2_migration_preserves_fifteen_fictional_references():
    from scripts.test_cloud_postgres import BOOTSTRAP
    container=os.environ['REVIEWLAB_TEST_PG_CONTAINER'];database='survey_upgrade_'+uuid4().hex[:12]
    sql('CREATE DATABASE '+database)
    def pg(statement):
        result=subprocess.run(['docker','exec','-i',container,'psql','-U','postgres','-d',database,'-v','ON_ERROR_STOP=1','-XAtq'],input=statement,text=True,encoding='utf-8',capture_output=True)
        assert result.returncode==0,result.stderr
        return result.stdout.strip()
    try:
        # Roles는 클러스터 공용. auth 스키마/함수는 새 임시 DB에만 만든다.
        bootstrap=BOOTSTRAP.replace('CREATE ROLE anon NOLOGIN;','').replace('CREATE ROLE authenticated NOLOGIN;','')
        pg(bootstrap+Path('supabase/migrations/202610240001_shared_storage.sql').read_text())
        user=str(uuid4());pg(f'INSERT INTO auth.users VALUES({quote(user)});')
        prefix=f'SET ROLE authenticated;SET request.jwt.claim.sub={quote(user)};'
        wid=pg(prefix+"SELECT public.reviewlab_create_workspace('가상 이전 15건');")
        from reviewlab.cloud.dataset import LEGACY_TABLES,canonical
        data={t:[] for t in LEGACY_TABLES};data['sources']=[source_row('[허구] 이전 보존 '+str(i)) for i in range(15)]
        from reviewlab.models import now
        tag=str(uuid4());data['tags']=[dict(id=tag,name='가상 이전 태그')]
        data['source_tags']=[dict(source_id=data['sources'][0]['id'],tag_id=tag)]
        data['verification_events']=[dict(sequence=1,id=str(uuid4()),source_id=data['sources'][0]['id'],status='검증 완료',checked_on='2025-01-01',checked_by='가상 담당',evidence='허구 원문',memo='테스트',recorded_at=now())]
        operation=str(uuid4())
        pg(prefix+f'SELECT public.reviewlab_commit({quote(wid)},\'actual\',0,{quote(operation)},{quote(canonical(data))}::jsonb);')
        before=json.loads(pg(prefix+f"SELECT public.reviewlab_read({quote(wid)},'actual');"))
        pg(Path('supabase/migrations/202610250002_surveys.sql').read_text())
        after=json.loads(pg(prefix+f"SELECT public.reviewlab_read_v2({quote(wid)},'actual');"))
        assert after['revision']==before['revision']==1
        assert all(after['data'][t]==before['data'][t] for t in LEGACY_TABLES)
        assert len(after['data']['sources'])==15
        assert pg(f'SELECT count(*) FROM reviewlab.commit_receipts WHERE operation_id={quote(operation)};')=='1'
        assert all(after['data'][t]==[] for t in TABLES)
    finally:
        sql('DROP DATABASE '+database)
