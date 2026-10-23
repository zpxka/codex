"""2단계 리뷰 분석 화면."""
import hashlib
from html import escape
from datetime import date
import pandas as pd
import streamlit as st
from reviewlab.models import now
from reviewlab.services.reviews import ReviewService, LABELS, FLAGS, STATES, read_csv, mapping_for
from reviewlab.services import review_transfer as transfer
from reviewlab.analysis.reviews import select, statistics, patterns, context
from reviewlab.analysis.charts import plot, png, TITLES
from reviewlab.services.transfer import write_csv

PAGES=('리뷰 데이터 업로드','리뷰 목록','리뷰 통계 분석','리뷰 패턴 탐색','리뷰 검토 관리','분석 결과 내보내기')


def done(message):
    st.session_state['notice']=message
    st.rerun()


def sample_csv():
    return write_csv(list(LABELS.values()),[
        [f'TEST-{i}', '가상플랫폼 가' if i%2 else '가상플랫폼 나','가상 음식업' if i%2 else '가상 생활업',
         [5,4,3,''][i%4],f'2025-01-{i:02}',f'[가상·테스트용] '+('리뷰 이벤트 참여 후 무료 제공' if i%3==0 else '테스트 서비스 이용 후기'),
         '예' if i%3==0 else ('미확인' if i%4==0 else '아니오'),'미확인','2025-02-01','https://example.com/test',
         '논문 근거 사용 금지','가상·테스트용'] for i in range(1,13)])


def frame(rows):
    return pd.DataFrame([{'내부 ID':r['id'],'리뷰 ID':r['review_key'] or '미확인','플랫폼':r['platform'] or '미확인',
        '업종':r['industry'] or '미확인','별점':str(r['rating']) if r['rating'] is not None else '미확인',
        '작성일':r['review_date'] or '미확인','리뷰 내용':r['content'] or '미확인',
        '이벤트 언급':r['event_mentioned'],'검토 상태':r['status']} for r in rows])


def filters(rows,key):
    with st.expander('필터 및 검색',expanded=False):
        platforms=st.multiselect('플랫폼',sorted({r['platform'] or '미확인' for r in rows}),key=key+'platforms')
        ratings=st.multiselect('별점',sorted({str(r['rating']) if r['rating'] is not None else '미확인' for r in rows}),key=key+'ratings')
        events=st.multiselect('이벤트 언급 여부',FLAGS,key=key+'events')
        keyword=st.text_input('내용·연구자 메모 키워드',key=key+'keyword')
        start=st.date_input('작성일 시작 (선택)',value=None,key=key+'start')
        end=st.date_input('작성일 종료 (선택)',value=None,key=key+'end')
        unknown=st.checkbox('기간 필터 사용 시 작성일 미확인도 포함',key=key+'unknown')
        exclude=st.checkbox('연구자가 분석 제외한 리뷰 제외',value=True,key=key+'exclude')
    if start and end and start>end:
        raise ValueError('시작일은 종료일보다 늦을 수 없습니다.')
    return dict(platforms=platforms,ratings=ratings,events=events,keyword=keyword,start=start.isoformat() if start else None,
        end=end.isoformat() if end else None,unknown_dates=unknown,exclude=exclude)


def show_context(ctx,summary):
    st.caption(f"전체 {ctx['project_n']}건 → 필터 적용 {ctx['selected_n']}건 / 필터 제외 {ctx['filtered_out']}건. "
        f"프로젝트 내 연구자 '분석 제외' {ctx['review_excluded']}건. 별점 척도 {ctx['rating_scale']}.")
    st.caption(f"작성일 관측 기간 {' ~ '.join(ctx['period'])} · 작성일 미확인 {ctx['unknown_dates']}건 · "
        f"별점 관측 {summary['rating_n']}건 · 내용 관측 {summary['text_n']}건")
    st.caption(f"필터 조건: {ctx['filter_description']}")
    if ctx['selected_n']<30:
        st.warning('표본이 30건 미만입니다. 기술 통계만 확인하세요. 30건 이상도 대표성이나 유의성을 보장하지 않습니다.')
    st.info(ctx['caution'])


def references(service,pid):
    form_token=getattr(service.library,"form_token","local")
    with st.expander('프로젝트에 참고문헌 연결'):
        sources=service.library.list_sources()
        choices={r['id']:r for r in sources}
        selected=st.multiselect('기존 연구자료 선택',list(choices),format_func=lambda sid:choices[sid]['title'],key='source-links-'+pid+'-'+form_token)
        if st.button('선택 자료 연결',disabled=not selected):
            service.repo.link_sources(pid,selected)
            done('참고자료를 연결했습니다.')
        linked=service.repo.links(pid)
        if linked:
            st.dataframe(pd.DataFrame([{'제목':r['title'],'URL':r['original_url'],
                '연결 상태':'현재 참고문헌 존재' if r['source_id'] else '원본 참고문헌 없음 — 제목·URL 기록만 보존'} for r in linked]),hide_index=True)
        st.caption('연결은 연구 맥락을 기록합니다. 참고문헌이 분석 결과를 자동으로 입증하지 않습니다. 리뷰 백업에는 참고문헌 본문·검증 이력이 포함되지 않습니다.')


def upload(service,pid):
    st.download_button('가상·테스트용 샘플 CSV',sample_csv(),'reviewlab-example-reviews.csv','text/csv')
    st.warning('샘플은 예시·연습 자료 모드에서만 올리세요. 실제 업체·작성자 자료가 아니며 논문 근거로 사용할 수 없습니다.')
    st.caption('필수: 리뷰 내용 또는 별점. 나머지는 선택이며 공란은 미확인입니다. 날짜 YYYY-MM-DD, 여부 예/아니오/미확인. 파일 10MB·5,000행, 프로젝트 5,000리뷰 한도입니다.')
    file=st.file_uploader('리뷰 CSV 업로드',type=['csv'],key=f'review-upload-{pid}')
    if not file:
        return
    content=file.getvalue()
    encoding=st.selectbox('CSV 인코딩',['utf-8-sig','cp949'])
    headers,_=read_csv(content,encoding)
    initial=mapping_for(headers)
    mapping={}
    with st.expander('열 연결 확인·변경',expanded=True):
        for k,label in LABELS.items():
            options=['저장하지 않음']+headers
            value=st.selectbox(label,options,index=options.index(initial[k]) if k in initial else 0,
                key=f'mapping-{pid}-{hashlib.sha256(content).hexdigest()}-{k}')
            if value!='저장하지 않음':
                mapping[k]=value
    report=service.preview(pid,content,encoding,mapping)
    st.write(f"추가 {report['added']}건 · 중복 {report['skipped']}건 · 오류 {len(report['errors'])}건 · 저장하지 않는 열 {report['ignored_columns']}개")
    st.caption('동일 플랫폼+리뷰 ID는 중복으로 건너뜁니다. ID가 없으면 정규화된 리뷰 필드 전체 일치로 판단합니다. 같은 문구라도 ID가 다르면 유지하여 패턴 탐색에서 확인합니다.')
    for error in report['errors'][:30]:
        st.error(error)
    if report['records']:
        st.dataframe(pd.DataFrame([{LABELS[k]:r.get(k) for k in ('platform','rating','content')} for r in report['records'][:20]]),hide_index=True)
    with st.form(f'import-{pid}-{getattr(service.library,"form_token","local")}'):
        source_note=st.text_area('수집 경위·이용 권한 기록 (필수)')
        confirmed=st.checkbox('합법적으로 확보했고, 내용·메모의 개인정보를 제거했으며 프로젝트 별점 척도와 데이터 모드를 확인했습니다')
        st.caption('작성자·계정·연락처 열은 기본 저장 대상이 아닙니다. 지원 필드로 직접 연결하거나 내용에 포함된 개인정보는 자동 삭제되지 않습니다. 원본 파일은 별도로 안전하게 보관하세요.')
        if st.form_submit_button('리뷰 저장',disabled=bool(report['errors'])):
            result=service.upload(pid,content,source_note,confirmed,encoding,mapping)
            done(f"리뷰 저장 완료: {result['added']}건 추가, {result['skipped']}건 중복 건너뜀")


def listing(service,rows,checking=False):
    st.dataframe(frame(rows),hide_index=True,use_container_width=True)
    if not rows:
        st.info('조건에 맞는 리뷰가 없습니다.')
        return
    choices={r['id']:r for r in rows}
    rid=st.selectbox('리뷰 상세 선택',list(choices),format_func=lambda v:f"{choices[v]['review_key'] or v[:8]} · {(choices[v]['content'] or '내용 미확인')[:50]}")
    r=choices[rid]
    with st.expander('보존한 원본 필드',expanded=True):
        st.json({LABELS[k]:v for k,v in r['raw'].items()})
    st.write('추가 연구자 메모 (원본과 별도 보존)')
    st.dataframe(pd.DataFrame(r['notes']),hide_index=True)
    st.write('연구자 검토 이력 (자동 분석 결과와 별도)')
    st.dataframe(pd.DataFrame(r['checks']),hide_index=True)
    if checking:
        with st.form(f'check-{rid}-{getattr(service.library,"form_token","local")}'):
            status=st.selectbox('검토 상태',STATES)
            reviewer=st.text_input('검토자')
            checked=st.date_input('검토일',value=date.today(),max_value=date.today())
            evidence=st.text_area('판단 근거 (완료·제외 시 필수)')
            memo=st.text_area('검토 메모')
            if st.form_submit_button('검토 기록 저장'):
                service.check(rid,status,reviewer,checked.isoformat(),evidence,memo)
                done('검토 이력을 추가했습니다. 원본 리뷰는 유지했습니다.')
    else:
        with st.form(f'note-{rid}-{getattr(service.library,"form_token","local")}'):
            memo=st.text_area('추가 연구자 메모')
            if st.form_submit_button('메모 추가'):
                service.note(rid,memo)
                done('연구자 메모를 추가했습니다.')


def restore_ui(service):
    st.subheader('리뷰 프로젝트 백업 복원')
    st.caption('프로젝트 단위 추가 복원입니다. 같은 프로젝트 ID가 있으면 전체를 건너뛰며 병합·덮어쓰기를 하지 않습니다. 참고문헌 백업은 기존 데이터 백업 및 복원 메뉴를 사용하세요.')
    file=st.file_uploader('리뷰 전용 백업 CSV',type=['csv'],key='reviews-backup-'+service.library.mode)
    if file:
        content=file.getvalue()
        try:
            report=transfer.preview_restore(service.library,content)
        except ValueError as e:
            st.error(f'파일 오류 1건 · 복원 차단: {e}')
            return
        st.write(f"프로젝트: {report['project']} · 추가 {report['added']}건 · 중복 {report['skipped']}건 · 오류 0건")
        if report['duplicate_project']:
            st.warning('이미 있는 프로젝트입니다. 내용이 달라도 기존 프로젝트를 그대로 유지합니다.')
        confirmed=st.checkbox('추가 전용 복원 정책을 확인하고 백업의 과거 검토 이력을 승계합니다',key='review-confirm-'+hashlib.sha256(content).hexdigest()+'-'+getattr(service.library,"form_token","local"))
        if st.button('리뷰 백업 복원 실행',disabled=not confirmed):
            result=transfer.restore(service.library,content,confirmed)
            done(f"복원 완료: {result['added']}건 추가 · {result['skipped']}건 건너뜀")


def main(library):
    service=ReviewService(library)
    page=st.radio('리뷰 분석 메뉴',PAGES,key='review-page')
    st.caption('원문 수집·가짜 리뷰 판정·AI 생성 판정·논문 본문 생성 기능은 없습니다. 리뷰 삭제는 단일 시점 자료로 판단하지 않습니다.')
    if page=='리뷰 데이터 업로드':
        with st.expander('새 분석 프로젝트 만들기',expanded=not service.repo.projects()):
            with st.form('new-review-project-'+getattr(library,"form_token","local")):
                name=st.text_input('프로젝트 이름')
                minimum=st.number_input('별점 최솟값',value=0.0)
                maximum=st.number_input('별점 최댓값',value=5.0)
                description=st.text_area('프로젝트 설명·비교 가능성 메모')
                st.caption('기본 척도는 0~5로 가정합니다. 플랫폼 척도가 다르면 프로젝트를 분리하세요. 자동 환산하지 않습니다.')
                if st.form_submit_button('프로젝트 생성'):
                    service.create_project(name,minimum,maximum,description)
                    done('프로젝트를 만들었습니다. 아래에서 선택하세요.')
    if page=='분석 결과 내보내기':
        restore_ui(service)
    projects={p['id']:p for p in service.repo.projects()}
    if not projects:
        st.info('리뷰 데이터 업로드에서 프로젝트를 만들거나 분석 결과 내보내기에서 리뷰 백업을 복원하세요.')
        return
    pid=st.selectbox('분석 프로젝트',list(projects),format_func=lambda v:projects[v]['name'],key='review-project-'+library.mode)
    p=projects[pid]
    st.caption(f"{'가상·테스트용 (논문 근거 사용 금지)' if library.mode=='example' else '실제 연구자료'} · 척도 {p['rating_min']}~{p['rating_max']} · {p['description']}")
    if page=='리뷰 데이터 업로드':
        references(service,pid)
        upload(service,pid)
        return
    all_rows=service.repo.rows(pid)
    f=filters(all_rows,page+pid)
    rows=select(all_rows,f)
    if page in ('리뷰 목록','리뷰 검토 관리'):
        listing(service,rows,page=='리뷰 검토 관리')
        return
    group=st.selectbox('비교 기준',['platform','industry','event_mentioned'],format_func=lambda k:LABELS[k])
    metric=st.selectbox('비교 지표',['자료 수','평균 별점','중앙 리뷰 길이'])
    tables,summary=statistics(rows,group,metric)
    ctx=context(p,all_rows,rows,f)
    ctx.update(generated_at=now(),tool_version='reviewlab-0.2.0',rating_n=summary['rating_n'],text_n=summary['text_n'],comparison=f'{LABELS[group]} / {metric}',input_ids=[r['id'] for r in rows],
        review_states={r['id']:r['status'] for r in rows},source_links=service.repo.links(pid))
    show_context(ctx,summary)
    if page=='리뷰 통계 분석':
        references(service,pid)
        st.write(f"이벤트 언급 '예' {summary['event_yes']}건 / 전체 {summary['total']}건 / 여부 확인 {summary['event_known']}건")
        percent=lambda value:'산출 불가' if value is None else f'{value:.2f}%'
        st.caption(f"전체 대비 비율: {percent(summary['event_percent_all'])} · 여부 확인 자료 대비 비율: {percent(summary['event_percent_known'])}")
        st.caption('앞뒤 공백을 제거한 분석용 내용으로 계산하며 입력 원문은 별도 보존합니다. 내용 결측은 길이·단어 수 계산에서 제외합니다. 단어 수는 형태소 분석이 아닌 공백 기준입니다. 날짜 결측은 날짜 그래프에서 제외하며 관측되지 않은 날짜를 0으로 채우지 않습니다.')
        for kind,title in TITLES.items():
            fig=plot(kind,tables)
            fig.update_layout(xaxis_title={'ratings':'플랫폼','events':'플랫폼','lengths':'별점','dates':'작성일','comparison':LABELS[group],'words':'공백 기준 단어 수'}[kind],
                yaxis_title=metric if kind=='comparison' else ('비율 (%)' if kind=='events' else '문자 수' if kind=='lengths' else '리뷰 수'))
            fig.update_layout(title=dict(text=TITLES[kind]+'<br><sup>'+escape(f"N={ctx['selected_n']} · {'가상·테스트용' if library.mode=='example' else '실제 연구자료'} · 기간 {' ~ '.join(ctx['period'])}")+'</sup>'))
            st.plotly_chart(fig,use_container_width=True)
            st.download_button(title+' PNG 다운로드',png(kind,tables,ctx),transfer.filename(library.mode,kind).replace('.csv','.png'),'image/png',key='png-'+kind)
        st.subheader('업종별 비교와 분모')
        st.dataframe(tables['industries'].rename(columns={'industry':'업종','count':'자료 수','rating_n':'별점 관측 수','mean_rating':'평균 별점','text_n':'내용 관측 수','median_length':'중앙 문자 수'}),hide_index=True)
        st.dataframe(tables['comparison'].rename(columns={'group':'비교 집단','count':'자료 수','rating_n':'별점 관측 수','mean_rating':'평균 별점','text_n':'내용 관측 수','median_length':'중앙 문자 수','value':'선택 지표 값','denominator':'지표 분모'}),hide_index=True)
    else:
        threshold=st.slider('문자 3-gram 유사도 기준',.5,1.0,.8,.05)
        pattern=patterns(rows,threshold)
        if page=='리뷰 패턴 탐색':
            st.warning('자동 탐색 후보입니다. 광고 표현이 있어도 대가성·허위·AI 생성을 확정할 수 없습니다. 직접 원문과 수집 맥락을 검토하세요.')
            st.caption(f"정확 문구·반복 어구는 선택 전체 대상. 유사도는 저장 순서 첫 {pattern['parameters']['compared']}건, 각 내용 첫 1,000자만 비교합니다. 한도 200건. 동일 문구는 별도 표로 표시합니다.")
            for key,title in [('exact','동일 문구 리뷰'),('phrases','2~3단어 반복 어구 (리뷰별 1회 계수)'),('expressions','광고·이벤트 관련 표현'),('similar','유사 표현 후보')]:
                st.subheader(title)
                st.dataframe(pd.DataFrame(pattern[key]).rename(columns={'review_id':'리뷰 내부 ID','text':'정규화한 내용','count':'반복 리뷰 수','phrase':'반복 어구','review_count':'해당 리뷰 수','term':'탐색 표현','evidence':'주변 문구','review_a':'리뷰 A','review_b':'리뷰 B','similarity':'유사도'}),hide_index=True)
        else:
            st.subheader('CSV 다운로드')
            st.table(pd.DataFrame([{'파일':'리뷰 목록 CSV','용도':'프로젝트 전체 원본 필드와 현재 검토·메모 확인. 복원용 아님'},
                {'파일':'분석 결과 CSV','용도':'현재 필터의 집계·패턴·분모·분석 조건. 복원용 아님'},
                {'파일':'리뷰 전용 백업 CSV','용도':'프로젝트 전체 원본·수집·메모·검토 이력·분석 실행 기록 복원'}]))
            st.download_button('프로젝트 전체 리뷰 목록 CSV',transfer.export_reviews(service.repo,pid),transfer.filename(library.mode,'list'),'text/csv')
            st.download_button('현재 분석 결과 CSV',transfer.result_csv(tables,ctx,pattern),transfer.filename(library.mode,'results'),'text/csv')
            st.download_button('리뷰 전용 백업 CSV',transfer.export_backup(service.repo,pid),transfer.filename(library.mode,'backup'),'text/csv')
            st.caption('모두 UTF-8 BOM입니다. 목록·결과 CSV는 Excel 수식 실행 방지를 위해 위험한 문자열 앞에 작은따옴표를 붙입니다. 백업은 편집하지 마세요. PNG는 리뷰 통계 분석에서 다운로드합니다.')
        if st.button('현재 분석 조건·결과 기록 저장'):
            transfer.save_run(service.repo,pid,tables,ctx,pattern)
            done('분석 조건·입력 리뷰 ID·결과를 기록했습니다. 리뷰 백업에도 포함됩니다.')
