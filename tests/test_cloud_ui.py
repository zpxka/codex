from io import BytesIO
from pathlib import Path

from streamlit.testing.v1 import AppTest

from reviewlab.cloud.library import CloudLibrary
from reviewlab.repositories.library import Library
from reviewlab.services.research import Research
from cloud_helpers import MemoryServer

APP=str(Path(__file__).parents[1]/'app.py')


def element(items,label):return next(e for e in items if e.label==label)


def configure(tmp_path,monkeypatch):
    from reviewlab.ui import cloud
    monkeypatch.setenv('REVIEWLAB_DATA_DIR',str(tmp_path))
    monkeypatch.setattr(cloud,'config',lambda:dict(url='https://cloud-test.example.invalid',key='sb_publishable_test_not_a_real_key',storage='local'))


def start_cloud(tmp_path,monkeypatch):
    configure(tmp_path,monkeypatch)
    server=MemoryServer();client=server.client()
    app=AppTest.from_file(APP,default_timeout=20).run()
    app.session_state['cloud-client']=client
    element(app.sidebar.selectbox,'저장소').set_value('Supabase 공용 저장소').run()
    assert not app.exception
    return app,server,client


def test_missing_settings_do_not_claim_connected_or_create_cloud_data(tmp_path,monkeypatch):
    from reviewlab.ui import cloud
    monkeypatch.setenv('REVIEWLAB_DATA_DIR',str(tmp_path))
    monkeypatch.setattr(cloud,'config',lambda:dict(url='',key='',storage='local'))
    app=AppTest.from_file(APP).run()
    element(app.sidebar.selectbox,'저장소').set_value('Supabase 공용 저장소').run()
    assert not app.exception
    assert any('접속 정보가 없습니다' in w.value for w in app.warning)
    assert not (tmp_path/'cloud').exists()


def test_login_form_auth_failure_is_explicit(tmp_path,monkeypatch):
    from reviewlab.ui import cloud
    from reviewlab.cloud.client import CloudError
    configure(tmp_path,monkeypatch)
    def fail(self,email,password):raise CloudError('테스트 로그인 실패')
    monkeypatch.setattr(cloud.SupabaseClient,'sign_in',fail)
    app=AppTest.from_file(APP).run()
    element(app.sidebar.selectbox,'저장소').set_value('Supabase 공용 저장소').run()
    element(app.text_input,'Supabase 로그인 이메일').set_value('fake@example.invalid')
    element(app.text_input,'Supabase 비밀번호').set_value('not-real-password')
    element(app.button,'공용 저장소 로그인').click().run()
    assert app.error and not app.exception
    assert any('테스트 로그인 실패' in e.value for e in app.error)


def test_ui_shared_create_read_refresh_and_edit_conflict(tmp_path,monkeypatch,source):
    app,server,client=start_cloud(tmp_path,monkeypatch)
    app.sidebar.radio[0].set_value('자료 등록').run()
    element(app.text_input,'자료 제목 *').set_value('[허구] 공유 화면')
    element(app.text_input,'원문 URL *').set_value('https://example.com/cloud-ui')
    element(app.button,'자료 저장').click().run()
    assert app.success and not app.exception
    reader=CloudLibrary(server.client(),server.workspace,'actual',tmp_path/'other-computer')
    assert reader.list_sources()[0]['title']=='[허구] 공유 화면'
    app.sidebar.radio[0].set_value('자료 목록').run()
    sid=reader.list_sources()[0]['id']
    data={k:reader.list_sources()[0][k] for k in ('title','authors','publication_date','source_type','original_url','notes','purpose')}
    Research(reader).save(dict(data,title='다른 기기의 최신 수정'),source_id=sid)
    element(app.text_input,'자료 제목 *').set_value('오래된 화면의 수정')
    element(app.button,'수정 저장').click().run()
    assert app.error and not app.exception
    element(app.button,'공용 자료 새로고침').click().run()
    assert not app.exception
    assert element(app.text_input,'자료 제목 *').value=='다른 기기의 최신 수정'
    reader.close()


def test_ui_migration_requires_review_and_preserves_local(tmp_path,monkeypatch,source,event):
    configure(tmp_path,monkeypatch)
    local=Library(tmp_path/'actual'/'reviewlab.sqlite3','actual')
    sid=Research(local).save(source);Research(local).verify(sid,event,True)
    before=local.path.read_bytes()
    app,server,client=start_cloud(tmp_path,monkeypatch)
    app.sidebar.radio[0].set_value('공용 DB 관리').run()
    assert not app.exception
    element(app.button,'이전 전 백업 만들기·미리보기').click().run()
    assert not app.exception
    assert element(app.button,'공용 DB로 추가 이전 실행').disabled
    element(app.checkbox,'이전 전 사본과 건수를 확인했고 신뢰하는 과거 검증·검토 이력을 승계합니다').check().run()
    element(app.button,'공용 DB로 추가 이전 실행').click().run()
    assert app.success and not app.exception
    assert '참고문헌 1건' in app.success[0].value
    assert local.path.read_bytes()==before
    other=CloudLibrary(server.client(),server.workspace,'actual',tmp_path/'desktop')
    assert other.list_sources()[0]['events'][0]['checked_by']==event['checked_by']
    other.close()


def test_workspace_switch_never_carries_unsaved_edit_into_other_workspace(tmp_path,monkeypatch,source):
    from copy import deepcopy
    from reviewlab.cloud.dataset import read_local
    configure(tmp_path,monkeypatch)
    seed=Library(tmp_path/'seed.db','actual');Research(seed).save(source)
    data=read_local(seed.path,'actual')
    one=MemoryServer();client=one.client()
    two=MemoryServer();other=two.client(client.user_id)
    one.states[(one.workspace,'actual')]=dict(revision=1,data=deepcopy(data))
    data_two=deepcopy(data);data_two['sources'][0]['title']='다른 작업공간의 원문'
    two.states[(two.workspace,'actual')]=dict(revision=1,data=data_two)
    original=client.rpc
    def route(name,params=None):
        if name=='workspaces':
            return dict(schema_version=1,workspaces=[dict(id=one.workspace,name='A'),dict(id=two.workspace,name='B')])
        if (params or {}).get('p_workspace')==two.workspace:return other.rpc(name,params)
        return original(name,params)
    client.rpc=route
    app=AppTest.from_file(APP,default_timeout=20).run()
    app.session_state['cloud-client']=client
    element(app.sidebar.selectbox,'저장소').set_value('Supabase 공용 저장소').run()
    app.sidebar.radio[0].set_value('자료 목록').run()
    element(app.text_input,'자료 제목 *').set_value('A에서 쓰던 미저장 입력').run()
    element(app.sidebar.selectbox,'공용 작업공간').set_value(two.workspace).run()
    assert not app.exception
    assert element(app.text_input,'자료 제목 *').value=='다른 작업공간의 원문'
    assert two.states[(two.workspace,'actual')]['data']['sources'][0]['title']=='다른 작업공간의 원문'
