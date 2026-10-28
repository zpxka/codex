import hashlib,json,os
import pandas as pd
import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError
from reviewlab.services.documents import Documents,CATEGORIES
from reviewlab.services.document_transfer import export_bundle,preview_bundle,restore_bundle
from reviewlab.analysis.documents import compare,suggestions,claim_checks
from reviewlab.services.document_ai import prepare,analyze,reserve_budget


def main(library):
    cfg=st.session_state.get('document-ai-config',{})
    service=Documents(library);token=getattr(library,'form_token',library.mode)
    page=st.radio('원문·논문 검토 화면',('원문 업로드','검색·문헌 비교','초고 검토','파일 포함 백업·복원','AI 연결 설정'))
    st.caption('원문 확인과 해석은 연구자가 수행합니다. 텍스트 추출은 출처 검증 완료를 뜻하지 않습니다.')
    if page=='AI 연결 설정':
        enabled=st.checkbox('외부 AI 사용 허용 (기본 꺼짐)',value=cfg.get('enabled',False),key='document-ai-enabled')
        input_price=st.number_input('입력 100만 토큰당 USD (제공사 최신 요금 확인)',min_value=0.0,value=float(cfg.get('input_price',0)),key='ai-input-price')
        output_price=st.number_input('출력 100만 토큰당 USD (제공사 최신 요금 확인)',min_value=0.0,value=float(cfg.get('output_price',0)),key='ai-output-price')
        daily_limit=st.number_input('이 기기의 일간 예상 비용 상한 USD',min_value=0.01,value=float(cfg.get('daily_limit',1)),key='ai-daily-limit')
        if st.button('AI 연결 설정 저장'):
            st.session_state['document-ai-config']=dict(enabled=enabled,input_price=input_price,output_price=output_price,daily_limit=daily_limit)
            st.success('설정을 저장했습니다. 외부 전송은 별도 승인이 필요합니다.')
        st.info('OPENAI_API_KEY 환경변수 또는 secrets.toml의 OPENAI_API_KEY를 사용합니다. 전송은 검색 결과에서 별도로 승인합니다. 최대 12KB 입력·800 출력 토큰, 세션당 10회 제한. 제공사에서 추가 과금 한도를 설정하세요.')
        return
    if page=='파일 포함 백업·복원':
        st.warning('SQLite/DB 백업만으로 원본 파일은 복구되지 않습니다. 파일 포함 ZIP도 보관하세요. 자동 파일 삭제는 제공하지 않습니다.')
        if st.button('파일 포함 전체 백업 만들기'):
            st.download_button('ZIP 다운로드',export_bundle(library),'reviewlab-files-'+library.mode+'.zip','application/zip')
        uploaded=st.file_uploader('파일 포함 ZIP 추가 복원',type=['zip'])
        if uploaded:
            blob=uploaded.getvalue();preview=preview_bundle(library,blob)
            st.write(preview['added']);st.write(preview['errors']);st.write(preview['duplicates']);st.write(preview['tag_reuse'])
            yes=st.checkbox('중복 후보·태그 연결 재사용·과거 이력을 확인했고 추가 복원합니다',key='docrestore'+token+hashlib.sha256(blob).hexdigest())
            if st.button('파일과 데이터 추가 복원',disabled=not yes or bool(preview['errors'])):restore_bundle(library,blob,yes);st.success('추가 복원 완료')
        return
    if page=='원문 업로드':
        sources=library.list_sources()
        if not sources:st.info('참고문헌부터 등록하세요.');return
        sid=st.selectbox('연결할 참고문헌',[r['id'] for r in sources],format_func=lambda x:next(r['title'] for r in sources if r['id']==x),key='doc-source-'+token)
        upload=st.file_uploader('PDF·DOCX·UTF-8 TXT (10MB)',type=['pdf','docx','txt'],key='doc-upload-'+token)
        if upload and st.button('원문 저장·추출'):
            result=service.upload(upload.getvalue(),upload.name,sid);st.write('처리 상태: '+result['status'])
    if page=='초고 검토':
        upload=st.file_uploader('초고/수정본 파일',type=['pdf','docx','txt'],key='draft-'+token)
        text=st.text_area('직접 작성한 초고 텍스트 (선택)')
        title=st.text_input('초고 버전 이름','초고')
        if st.button('초고 버전 저장'):
            if upload:service.upload(upload.getvalue(),upload.name,kind='draft')
            elif text.strip():service.upload(text.encode(),title+'.txt',kind='draft')
            else:raise ValueError('직접 작성한 초고를 입력하세요.')
            st.success('초고 버전을 보존했습니다.')
    rows=service.rows();st.dataframe(pd.DataFrame(rows),hide_index=True)
    if rows:
        with st.expander('저장한 원문 읽기·다운로드'):
            original=st.selectbox('읽을 원문 버전',[r['id'] for r in rows],format_func=lambda x:next(r['title']+' · '+r['sha256'][:8] for r in rows if r['id']==x),key='doc-read-'+token)
            if st.button('선택 원문 불러오기'):
                row=next(r for r in rows if r['id']==original)
                st.download_button('원본 파일 다운로드',service.files.get(row['sha256']),row['title'],'application/octet-stream')
                st.write('추출 상태:',row['status'])
                st.dataframe(pd.DataFrame(service.units(original,require_ready=False)),hide_index=True)
                st.caption('추출 불완전 상태의 내용은 분석 근거로 사용할 수 없습니다.')
    usable=[r for r in rows if r['status']=='ready']
    if not usable:st.info('정상 추출된 원문이 없습니다. encrypted/ocr_needed/partial_ocr_needed/extraction_failed는 정상 처리 상태가 아닙니다.');return
    names={r['id']:r['title']+' · '+r['sha256'][:8] for r in usable}
    if page=='초고 검토':
        drafts=[r['id'] for r in usable if r['kind']=='draft']
        if not drafts:return
        current=st.selectbox('검토할 초고',drafts,format_func=names.get)
        text='\n'.join(u['text'] for u in service.units(current))
        st.caption('검토 제안입니다. 맞춤법 사전 검사나 논리의 확정 판정이 아닙니다.')
        st.dataframe(pd.DataFrame(suggestions(text)),hide_index=True)
        other=st.selectbox('비교할 이전 버전',[None]+[i for i in drafts if i!=current],format_func=lambda x:names.get(x,'비교 안 함'))
        if other:st.dataframe(pd.DataFrame(compare('\n'.join(u['text'] for u in service.units(other)),text)),hide_index=True)
        st.info('주장 문장과 근거를 연결하려면 아래에서 초고 및 참고문헌을 함께 선택하고 주장 문구를 검색하세요. 불일치 여부와 수정 의견은 연구자가 기록합니다.')
    selected=st.multiselect('분석할 원문 범위',list(names),format_func=names.get,key='doc-scope-'+token)
    if selected:
        records=service.rows('document_notes')
        matrix=[]
        for doc_id in selected:
            entry={'문헌/버전':names[doc_id]}
            for category in CATEGORIES:
                matches=[n for n in records if n['document_id']==doc_id and n['category']==category]
                if matches:
                    n=matches[-1]
                    entry[category]=n['content']+' ['+n['state']+']'
                else:entry[category]='기록 없음'
            matrix.append(entry)
        st.caption('문헌별 연구자 분석 비교표 — 각 문헌에 기록한 최신 의견입니다. 기록 없음은 결과가 없다는 뜻이 아닙니다.')
        st.dataframe(pd.DataFrame(matrix),hide_index=True)
    question=st.text_input('질문 또는 검색 문구',help='단어 포함 점수로 검색합니다. 의미 검색·자동 결론이 아닙니다.')
    if not selected or not question:return
    hits=service.search(selected,question)
    if not hits:st.info('원문에서 확인되지 않음');return
    st.dataframe(pd.DataFrame(hits),hide_index=True)
    chosen=st.multiselect('인용할 근거 행',list(range(len(hits))),format_func=lambda i:names[hits[i]['document_id']]+' / '+hits[i]['location']+' / '+hits[i]['quote'][:80],key='doc-evidence-'+token+hashlib.sha256((str(selected)+question).encode()).hexdigest())
    evidence=[hits[i] for i in chosen]
    category=st.selectbox('분석·비교 항목',CATEGORIES)
    if category=='주장 검토' and evidence:
        kinds={r['id']:r['kind'] for r in rows}
        claim=' '.join(e['quote'] for e in evidence if kinds[e['document_id']]=='draft')
        support=' '.join(e['quote'] for e in evidence if kinds[e['document_id']]=='source')
        if claim and support:st.write(claim_checks(claim,support))
    content=st.text_area('연구자가 작성한 분석·수정 의견')
    state=st.selectbox('연구자 확인 상태',('미확인','연구자 확인','보류'))
    if st.button('근거 연결 기록 추가',disabled=not evidence):service.note(selected[0],category,content,evidence,selected,state);st.success('자료 범위·위치·근거·새 검토 이력을 저장했습니다.')
    st.caption('기존 기록은 덮어쓰지 않습니다. 수정·검증 판단도 같은 범위를 선택해 새 기록으로 추가합니다.')
    notes=[r for r in service.rows('document_notes') if r['document_id'] in selected]
    st.caption('선택한 문헌의 분석·비교 기록: 공통/차이 판단은 연구자가 실제 근거와 조사 조건을 확인하여 작성합니다.')
    st.dataframe(pd.DataFrame(notes),hide_index=True)
    if evidence and cfg.get('enabled',False):
        payload,approval=prepare(question,evidence)
        st.json(payload)
        st.warning('표시한 질문·근거만 외부 OpenAI로 보냅니다. 개인정보/저작권을 확인하세요. 입력 최대 약 16,000토큰·출력 800토큰의 비용이 발생할 수 있습니다. 모델 최신 가격과 계정 사용 한도를 확인하세요.')
        st.write('이번 호출 예상 최대 비용(입력 요금 기준) USD:',(16000*cfg.get('input_price',0)+800*cfg.get('output_price',0))/1000000)
        approved=st.checkbox('표시한 범위의 외부 전송과 비용을 승인합니다',key='ai-approve-'+approval+token)
        if st.button('승인한 근거 탐색 1회 실행',disabled=not approved):
            key=os.environ.get('OPENAI_API_KEY','')
            if not key:
                try:key=st.secrets.get('OPENAI_API_KEY','')
                except StreamlitSecretNotFoundError:pass
            input_price=cfg.get('input_price',0);output_price=cfg.get('output_price',0)
            if not key or input_price<=0 or output_price<=0:raise ValueError('API 키와 제공사 최신 토큰 요금을 AI 연결 설정에서 확인하세요.')
            reserve_budget(service.files.root,(16000*input_price+800*output_price)/1000000,cfg.get('daily_limit',1.0))
            count=st.session_state.get('document-ai-count',0)
            if count>=10:raise ValueError('세션 10회 한도입니다. 사용량을 확인하세요.')
            st.session_state['document-ai-count']=count+1
            result=analyze(service,payload,approval,key,True)
            st.write('검토 제안: 원문 일치 구절 (해석의 타당성을 보증하지 않음)');st.dataframe(pd.DataFrame(result))
