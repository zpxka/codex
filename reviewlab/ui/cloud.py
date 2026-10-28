"""로그인, 작업공간 선택, 백업 및 SQLite 이전 화면."""
import hashlib
from collections.abc import Mapping
import os
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

from reviewlab.cloud.client import CloudError, SupabaseClient
from reviewlab.cloud.library import CloudLibrary, private_file
from reviewlab.cloud import migration
from reviewlab.config import database_path


NAMES={'tags':'태그','sources':'참고문헌','source_tags':'자료-태그 연결','verification_events':'원문 검증 이력',
       'review_projects':'리뷰 프로젝트','review_imports':'리뷰 수집 기록','reviews':'리뷰 원본','review_notes':'리뷰 메모',
       'review_checks':'리뷰 검토 이력','review_source_links':'참고문헌 연결','review_analysis_runs':'분석 실행 기록', 'document_files':'원문·초고 파일', 'document_notes':'근거·검토 이력', 'survey_projects':'설문 프로젝트', 'survey_questions':'설문 문항', 'survey_imports':'설문 업로드', 'survey_responses':'설문 응답', 'survey_answers':'문항별 원본', 'survey_analysis_runs':'설문 분석 기록'}


def config():
    try:
        secret=st.secrets.get('reviewlab',{})
    except StreamlitSecretNotFoundError:
        if Path('.streamlit/secrets.toml').exists() or (Path.home()/'.streamlit/secrets.toml').exists():
            raise CloudError('secrets 설정 파일을 읽지 못했습니다. TOML 형식·권한을 확인하세요. 실제 설정값은 표시하지 않습니다.') from None
        secret={}
    if not isinstance(secret,Mapping):
        raise CloudError('secrets의 [reviewlab] 항목은 TOML 테이블이어야 합니다.')
    result=dict(url=os.environ.get('REVIEWLAB_SUPABASE_URL',secret.get('supabase_url','')),
                key=os.environ.get('REVIEWLAB_SUPABASE_KEY',secret.get('supabase_key','')),
                storage=os.environ.get('REVIEWLAB_STORAGE',secret.get('storage','local')))
    if any(not isinstance(value,str) for value in result.values()) or result['storage'] not in ('local','supabase'):
        raise CloudError('저장소 설정을 확인하세요. storage는 local 또는 supabase이고 URL·key는 문자열이어야 합니다.')
    return result


def clear_cloud():
    for key in list(st.session_state):
        if key.startswith('cloud-library:'):
            st.session_state.pop(key).close()
        elif key.startswith(('cloud-preview:','cloud-download:')):
            st.session_state.pop(key)
    st.session_state.pop('cloud-client',None)


def library(mode,page):
    cfg=config()
    client=st.session_state.get('cloud-client')
    if client and not client.access_token:
        clear_cloud();client=None
    if client and (client.url!=cfg['url'].rstrip('/') or client.key!=cfg['key']):
        clear_cloud();client=None
    if not client:
        st.info('공용 저장소는 Supabase 설정과 로그인이 필요합니다. README의 두 컴퓨터 설정 절차를 먼저 완료하세요.')
        if not cfg['url'] or not cfg['key']:
            st.warning('Supabase 접속 정보가 없습니다. 프로젝트 URL과 publishable/anon key를 이 컴퓨터의 secrets에 넣으세요. 실제 연결은 아직 하지 않았습니다.')
            st.stop()
        with st.form('cloud-login',clear_on_submit=True):
            email=st.text_input('Supabase 로그인 이메일')
            password=st.text_input('Supabase 비밀번호',type='password')
            if st.form_submit_button('공용 저장소 로그인'):
                try:
                    client=SupabaseClient(cfg['url'],cfg['key']);client.sign_in(email,password)
                except CloudError as error:st.error(str(error))
                else:st.session_state['cloud-client']=client;st.rerun()
        st.stop()
    st.sidebar.caption('공용 로그인: '+(client.email or '인증된 사용자'))
    if st.sidebar.button('공용 저장소 로그아웃'):
        try:client.logout()
        except CloudError:st.session_state['notice']='이 기기의 로그인 정보는 지웠습니다. 서버 로그아웃 확인은 인터넷 복구 후 진행하세요.'
        clear_cloud();st.rerun()
    spaces=client.rpc('workspaces')
    if not isinstance(spaces,dict) or spaces.get('schema_version')!=3:
        raise CloudError('Supabase SQL 마이그레이션 버전을 확인하세요.')
    choices={r['id']:r['name'] for r in spaces['workspaces']}
    with st.sidebar.expander('새 공용 작업공간 만들기',expanded=not choices):
        with st.form('cloud-create-workspace',clear_on_submit=True):
            name=st.text_input('공용 작업공간 이름',max_chars=200)
            if st.form_submit_button('작업공간 생성'):
                if not name.strip():st.error('작업공간 이름을 입력하세요.')
                else:
                    wid=client.rpc('create_workspace',{'p_name':name})
                    st.session_state['cloud-workspace']=wid;st.rerun()
    if not choices:
        st.info('작업공간을 만들거나 관리자에게 기존 팀 작업공간의 구성원으로 등록해 달라고 요청하세요.')
        st.stop()
    wid=st.sidebar.selectbox('공용 작업공간',list(choices),format_func=lambda v:choices[v]+' · '+v[:8],key='cloud-workspace')
    cache_key=f'cloud-library:{client.user_id}:{wid}:{mode}'
    if cache_key not in st.session_state:
        st.session_state[cache_key]=CloudLibrary(client,wid,mode,database_path(mode).parents[1])
    result=st.session_state[cache_key]
    st.sidebar.caption(f'공용 작업공간 {choices[wid]} · 읽은 버전 {result.revision}')
    if st.sidebar.button('공용 자료 새로고침'):
        result.reload();st.rerun()
    observe_changes(result,page)
    return result


@st.fragment(run_every=10.0)
def observe_changes(library,page):
    try:
        if library.remote_revision()!=library.revision:
            if page=='메인 대시보드' and not library._broken:
                library.reload();st.rerun()
            st.warning('다른 기기에서 변경한 자료가 있습니다. 입력 중인 내용을 복사한 뒤 공용 자료 새로고침을 누르세요. 이 화면의 오래된 내용으로는 저장할 수 없습니다.')
    except CloudError as error:st.warning(str(error))


def management(library,mode):
    if not getattr(library,'is_cloud',False):
        st.info('왼쪽 저장소에서 Supabase 공용 저장소를 선택하세요. 현재 로컬 자료는 그대로 유지됩니다.')
        st.caption('설정은 README의 Supabase 공용 데이터베이스 안내를 따르세요. 서비스 키·DB 비밀번호를 채팅이나 GitHub에 넣지 마세요.')
        return
    st.caption(f'작업공간 ID: {library.workspace_id} · 모드: {mode} · 공용 버전: {library.revision}')
    st.info('로컬 SQLite와 공용 DB는 별도 모드입니다. 자동 양방향 동기화는 하지 않습니다. 공용 모드를 선택한 두 컴퓨터가 같은 작업공간을 사용해야 합니다.')
    st.subheader('공용 데이터 전체 백업')
    st.caption('참고문헌·리뷰·모든 이력을 함께 담은 로컬 호환 SQLite 파일입니다. 날짜별 자동 사본은 이 기기에 최근 30개가 보관됩니다. 앱 종료 중에도 필요한 서버 측 백업은 Supabase 플랜·백업 설정에서 별도로 확인하세요.')
    download_key=f'cloud-download:{library.workspace_id}:{mode}'
    if st.button('전체 SQLite 백업 만들기',disabled=library._broken):
        st.session_state[download_key]=library.snapshot()
    backup=st.session_state.get(download_key)
    if backup and Path(backup).exists():
        stamp=datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y-%m-%d_%H%M%S')
        st.download_button('전체 SQLite 백업 다운로드',Path(backup).read_bytes(),f'reviewlab-{mode}-full-{stamp}-KST.sqlite3','application/octet-stream')
    st.code(str(library.root/'backups'))
    st.caption('기존 CSV 백업·복원 메뉴도 유지됩니다. SQLite 전체 복원은 아래 추가 전용 이전 절차를 이용하고 기존 공용 DB를 강제로 교체하지 마세요.')
    st.subheader('미확인 저장 요청')
    pending=library.pending_requests()
    if not pending:st.caption('이 작업공간·모드에 남은 복구 요청이 없습니다.')
    for item in pending:
        with st.expander('요청 '+item['operation_id']+' · '+('저장 확인됨·백업 정리 필요' if item['state']=='confirmed' else '저장 결과 확인 필요')):
            st.caption('같은 요청 ID와 같은 내용으로 재확인합니다. 이미 저장된 요청은 다시 적용하지 않습니다. 다른 변경과 충돌하면 자동 덮어쓰지 않습니다.')
            st.code(str(item['path']))
            if Path(item['path']).exists():
                st.download_button('복구 사본 다운로드',Path(item['path']).read_bytes(),Path(item['path']).name,'application/octet-stream',key='pending-dl-'+item['operation_id'])
            if st.button('같은 요청 결과 확인·재시도',key='pending-retry-'+item['operation_id']):
                library.retry_pending(item['operation_id']);st.session_state['notice']='저장 요청 결과와 최신 공용 자료를 확인했습니다.';st.rerun()
    st.subheader('기존 SQLite 이전 / 전체 백업 추가 복원')
    source_kind=st.radio('이전할 자료 위치',('이 컴퓨터의 기존 SQLite','전체 SQLite 백업 파일 업로드'))
    source_key=None
    if source_kind=='이 컴퓨터의 기존 SQLite':
        source_path=st.text_input('기존 로컬 SQLite 파일 경로',value=str(database_path(mode)),key='cloud-source-path-'+mode)
        source_key=source_path
    else:
        uploaded=st.file_uploader('ReviewLab 전체 SQLite 백업',type=['sqlite3','db'],key='cloud-full-upload-'+mode)
        source_path=None
        if uploaded:
            content=uploaded.getvalue()
            if len(content)>50*1024*1024:raise CloudError('전체 SQLite 파일 업로드는 50MB 이하만 지원합니다.')
            digest=hashlib.sha256(content).hexdigest()
            source_path=library.root/'incoming'/f'{digest}.sqlite3'
            source_path.parent.mkdir(parents=True,exist_ok=True)
            if not source_path.exists():source_path.write_bytes(content);private_file(source_path)
            source_key=digest
    preview_key=f'cloud-preview:{library.workspace_id}:{mode}'
    if st.button('이전 전 백업 만들기·미리보기',disabled=not source_path):
        result=migration.prepare(library,source_path)
        result['source_key']=source_key;st.session_state[preview_key]=result
    preview=st.session_state.get(preview_key)
    if not preview or preview['source_key']!=source_key:return
    st.caption('아래 사본 시점의 자료를 이전합니다. 원본 SQLite를 초기화·삭제·수정하지 않습니다.')
    st.code(str(preview['backup_path']))
    st.dataframe(pd.DataFrame([{'항목':NAMES[t],'추가 예정':n,'동일 ID 건너뛰기':preview['skipped'][t]} for t,n in preview['added'].items()]),hide_index=True)
    st.write(f"중복 후보 {len(preview['duplicates'])}건 · 태그 연결 재사용 {len(preview['tag_reuse'])}건 · 오류 {len(preview['errors'])}건")
    for key in ('duplicates','tag_reuse','errors'):
        if preview[key]:st.dataframe(pd.DataFrame(preview[key]).rename(columns={'table':'항목','id':'ID','title':'제목','reason':'이유','name':'태그','existing_id':'기존 ID','original_id':'이전 ID','target_id':'연결 ID'}),hide_index=True)
    if preview['errors']:st.error('같은 ID의 내용·이력 충돌은 자동으로 해결하지 않습니다. 별도 빈 작업공간에 이전하거나 기존 자료를 검토한 뒤 다시 미리보기를 만드세요.')
    approval_key=f'{library.workspace_id}-{mode}-{preview["incoming_fingerprint"]}-{preview["expected_revision"]}'
    keep=st.checkbox('중복 후보를 검토했고 서로 다른 ID의 자료를 합치지 않고 모두 보존합니다',disabled=not bool(preview['duplicates']),key='migrate-duplicates-'+approval_key)
    tags=st.checkbox('동일 이름 태그는 기존 연결 ID를 재사용함을 확인했습니다 (자료는 합치지 않음)',disabled=not bool(preview['tag_reuse']),key='migrate-tags-'+approval_key)
    confirm=st.checkbox('이전 전 사본과 건수를 확인했고 신뢰하는 과거 검증·검토 이력을 승계합니다',key='migrate-confirm-'+approval_key)
    if st.button('공용 DB로 추가 이전 실행',disabled=bool(preview['errors']) or not confirm or (bool(preview['duplicates']) and not keep) or (bool(preview['tag_reuse']) and not tags)):
        counts=migration.execute(library,preview,confirm,keep,tags)
        st.session_state.pop(preview_key,None)
        st.session_state['notice']=f"이전 완료: 참고문헌 {counts['sources']}건 · 리뷰 {counts['reviews']}건 추가. 원본 SQLite와 이전 전 사본은 유지했습니다."
        st.rerun()
