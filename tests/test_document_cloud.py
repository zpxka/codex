import io,json,os
from uuid import uuid4
import pytest
from reviewlab.cloud.library import CloudLibrary
from reviewlab.services.documents import Documents
from reviewlab.services.document_transfer import export_bundle,restore_bundle
from cloud_helpers import MemoryServer
from test_documents import seed


class HTTP:
    def __init__(self):self.files={}
    def request(self,method,url,headers=None,data=None,**kwargs):
        key=url.split('reviewlab-originals/')[1]
        status=200 if key in self.files else 404
        if method=='POST':
            assert headers['x-upsert']=='false'
            status=409 if key in self.files else 200
            if status==200:self.files[key]=data
        class Response:
            status_code=status
            def close(self):pass
            def iter_content(self,size):yield self.content
        r=Response();r.content=self.files.get(key,b'');return r


def test_two_cloud_sessions_files_notes_modes_and_bundle(tmp_path,source):
    server=MemoryServer();http=HTTP()
    def client():
        c=server.client();c.http=http;c.refresh=lambda:None;return c
    a=CloudLibrary(client(),server.workspace,'actual',tmp_path/'a');b=CloudLibrary(client(),server.workspace,'actual',tmp_path/'b')
    try:
        s,d=seed(a,source);b.reload()
        assert Documents(b).units(d['id'])==s.units(d['id'])
        hits=s.search([d['id']],'리뷰');s.note(d['id'],'한계','가상 조건',hits,[d['id']])
        b.reload();assert Documents(b).rows('document_notes')==s.rows('document_notes')
        blob=export_bundle(b)
        from reviewlab.repositories.library import Library
        target=Library(tmp_path/'restore.db','actual');restore_bundle(target,blob,True)
        assert Documents(target).units(d['id'])==s.units(d['id'])
        assert all('/actual/' in key for key in http.files)
    finally:a.close();b.close()


@pytest.mark.skipif(not os.environ.get('REVIEWLAB_TEST_PG_CONTAINER'),reason='임시 PostgreSQL 필요, 실제 Supabase 미접속')
def test_storage_private_policy_and_document_rpc(tmp_path,source):
    from test_cloud_postgres import sql,rpc,quote,args,commit
    from reviewlab.cloud.dataset import read_local,fingerprint
    from reviewlab.repositories.library import Library
    from pathlib import Path
    # Supabase Storage HTTP가 아닌 실제 SQL 정책 검증용 최소 테이블 대역.
    sql('CREATE SCHEMA storage; CREATE TABLE storage.buckets(id text primary key,name text,public boolean,file_size_limit bigint,allowed_mime_types text[]); CREATE TABLE storage.objects(id uuid default gen_random_uuid(),bucket_id text,name text); ALTER TABLE storage.objects ENABLE ROW LEVEL SECURITY; GRANT USAGE ON SCHEMA storage TO authenticated,anon; GRANT SELECT,INSERT,UPDATE,DELETE ON storage.objects TO authenticated,anon;')
    sql(Path('supabase/storage_documents.sql').read_text())
    a,b=str(uuid4()),str(uuid4());sql(f'INSERT INTO auth.users VALUES({quote(a)}),({quote(b)});')
    wid=rpc(a,'create_workspace',quote('가상 원문'))
    key=wid+'/actual/'+'a'*64
    prefix=f'SET ROLE authenticated;SET request.jwt.claim.sub={quote(a)};'
    sql(prefix+f'INSERT INTO storage.objects(bucket_id,name) VALUES(\'reviewlab-originals\',{quote(key)});')
    assert sql(f"SET ROLE authenticated;SET request.jwt.claim.sub={quote(b)};SELECT count(*) FROM storage.objects;").stdout.strip()=='0'
    assert sql("SET ROLE anon;SELECT count(*) FROM storage.objects;").stdout.strip()=='0'
    assert sql(prefix+"DELETE FROM storage.objects; SELECT count(*) FROM storage.objects;").stdout.strip()=='1'
    assert sql(prefix+"INSERT INTO storage.objects(bucket_id,name) VALUES('reviewlab-originals','invalid/path');",False).returncode
    local=Library(tmp_path/'data.db','actual');s,d=seed(local,source);hits=s.search([d['id']],'리뷰');s.note(d['id'],'한계','가상',hits,[d['id']])
    data=read_local(local.path,'actual');assert commit(a,wid,data)['revision']==1
    assert fingerprint(rpc(a,'read',args(wid))['data'])==fingerprint(data)
    assert rpc(b,'read',args(wid)).returncode
    assert not rpc(a,'read',args(wid,'example'))['data']['document_files']
    old=sql(prefix+f"SELECT public.reviewlab_commit_v2({quote(wid)},'actual',1,{quote(str(uuid4()))},'{{}}'::jsonb);",False)
    assert old.returncode and 'RL_SCHEMA_UPGRADE_REQUIRED' in old.stderr
    assert fingerprint(rpc(a,'read',args(wid))['data'])==fingerprint(data)


def test_unconfirmed_storage_upload_not_retried_or_linked(tmp_path,source):
    import requests
    from reviewlab.services.research import Research
    server=MemoryServer()
    class LostResponse(HTTP):
        calls=0
        def request(self,method,*a,**kw):
            result=super().request(method,*a,**kw)
            if method=='POST':
                self.calls+=1
                raise requests.Timeout('simulated private details must not be displayed')
            return result
    http=LostResponse();client=server.client();client.http=http;client.refresh=lambda:None
    cloud=CloudLibrary(client,server.workspace,'actual',tmp_path)
    try:
        sid=Research(cloud).save(source);service=Documents(cloud)
        with pytest.raises(ValueError,match='자동 재전송하지 않습니다') as error:
            service.upload('가상 원문'.encode(),'test.txt',sid)
        assert 'private details' not in str(error.value)
        assert http.calls==1 and len(http.files)==1
        assert not service.rows() and len(cloud.list_sources())==1
    finally:cloud.close()


def test_document_cas_conflict_preserves_first_writer(tmp_path,source):
    from reviewlab.cloud.client import CloudConflict
    server=MemoryServer();http=HTTP()
    def client():
        c=server.client();c.http=http;c.refresh=lambda:None;return c
    a=CloudLibrary(client(),server.workspace,'actual',tmp_path/'a')
    b=CloudLibrary(client(),server.workspace,'actual',tmp_path/'b')
    try:
        sa,d=seed(a,source);b.reload();sb=Documents(b)
        evidence=sa.search([d['id']],'리뷰')
        first=sa.note(d['id'],'핵심 결과','먼저 확인한 판단',evidence,[d['id']])
        with pytest.raises(CloudConflict):sb.note(d['id'],'핵심 결과','오래된 판단',evidence,[d['id']])
        b.reload()
        assert sb.rows('document_notes')==[first]
    finally:a.close();b.close()
