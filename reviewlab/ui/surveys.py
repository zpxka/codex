"""설문 프로젝트·진단·변수·통계·백업 화면."""
import json
import hashlib
from datetime import datetime
import pandas as pd
import streamlit as st
from reviewlab.services.surveys import Surveys,TYPES
from reviewlab.services import survey_transfer
from reviewlab.services.transfer import write_csv,safe_cell
from reviewlab.analysis.surveys import descriptive,cross,figure

PAGES=('설문 프로젝트','설문 CSV 업로드','설문 문항·변수 관리','기초 통계','교차분석','그래프 및 결과 내보내기')


def config_editor(definitions,key):
    rows=[]
    for name,c in definitions.items():
        rows.append(dict(문항=name,유형=c['kind'],범주='|'.join(c['categories']),최소=float(c['minimum']),최대=float(c['maximum']),역문항=c['reverse'],결측기준=c['missing'],구분자=c['delimiter'],결측표시='|'.join(c['missing_values'])))
    edited=st.data_editor(pd.DataFrame(rows),disabled=['문항'],hide_index=True,key=key,
        column_config={'유형':st.column_config.SelectboxColumn(options=TYPES),'결측기준':st.column_config.SelectboxColumn(options=['유효 응답 기준','전체 응답 기준'])})
    return {r['문항']:dict(kind=r['유형'],categories=[s.strip() for s in r['범주'].split('|') if s.strip()],minimum=r['최소'],maximum=r['최대'],reverse=bool(r['역문항']),missing=r['결측기준'],delimiter=r['구분자'],missing_values=[s.strip() for s in r['결측표시'].split('|') if s.strip()]) for r in edited.to_dict('records')}


def main(library):
    service=Surveys(library);token=getattr(library,'form_token',library.mode)
    page=st.radio('설문 분석 화면',PAGES,horizontal=True)
    st.info('기술통계·탐색 도구입니다. 작은 표본의 일반화·인과관계·통계적 유의성을 주장하지 않습니다. 이메일·연락처·서술형 재식별 정보를 원본에서 제거하세요.')
    if page=='설문 프로젝트':
        with st.form('survey-create-'+token):
            name=st.text_input('설문 프로젝트 이름')
            fields={k:st.text_input(label) for k,label in [('purpose','조사 목적'),('period_start','조사 시작일 (YYYY-MM-DD, 선택)'),('period_end','조사 종료일 (YYYY-MM-DD, 선택)'),('sample','표본 설명'),('method','조사 방법'),('memo','프로젝트 메모')]}
            if st.form_submit_button('설문 프로젝트 생성'):
                service.project(name,**fields);st.success('설문 프로젝트를 생성했습니다.')
    if page=='그래프 및 결과 내보내기': backup_panel(library,token)
    projects=service.rows('survey_projects')
    if not projects: st.info('먼저 설문 프로젝트를 만드세요.');return
    names={p['id']:p['name'] for p in projects}
    pid=st.selectbox('설문 프로젝트 선택',list(names),format_func=names.get,key='survey-project-'+token)
    project=next(p for p in projects if p['id']==pid)
    if page=='설문 프로젝트':
        st.dataframe(pd.DataFrame(projects),hide_index=True)
        with st.form('survey-metadata-'+pid+'-'+token):
            fields={k:st.text_input(k,project[k]) for k in ('name','purpose','period_start','period_end','sample','method','memo')}
            if st.form_submit_button('프로젝트 정보 수정'):
                service.update_project(pid,fields);st.success('프로젝트 정보만 수정했습니다. 원본 응답은 유지됩니다.')
        return
    if page=='설문 CSV 업로드':
        sample=write_csv(['데이터 구분','리뷰 이벤트 경험','리뷰 신뢰도','구매 후 만족도','참고하는 리뷰'],[['가상·테스트용','있음',5,4,'배달;포털'],['가상·테스트용','없음',2,3,'포털'],['가상·테스트용','있음','','','배달']])
        st.download_button('가상·테스트용 설문 CSV',sample,'survey-example.csv','text/csv')
        st.caption('예제는 실제 모드에 저장할 수 없습니다. Google Forms 체크박스 응답은 CSV 셀 안의 쉼표 구분도 직접 설정하세요. 리커트 척도는 숫자형 응답에만 적용합니다.')
        encoding=st.selectbox('설문 CSV 인코딩',['auto','utf-8-sig','utf-8','cp949'])
        uploaded=st.file_uploader('설문 CSV (10MB·5,000응답·100문항 이내)',type=['csv'],key='survey-upload-'+pid+'-'+token)
        if uploaded:
            blob=uploaded.getvalue();preview=service.preview(pid,blob,encoding=encoding)
            definitions=config_editor(preview['definitions'],'survey-def-'+pid+'-'+token+'-'+preview['sha256'])
            preview=service.preview(pid,blob,definitions,encoding)
            st.write({k:preview[k] for k in ('total','excluded','duplicates','repeated')})
            if preview['pii']: st.warning('식별정보 의심 열: '+', '.join(preview['pii'])+' — 기본 제외. 선택 복원 시 수집 근거와 최소화를 확인하세요.')
            if preview['errors']: st.dataframe(pd.DataFrame(preview['errors']),hide_index=True)
            st.caption('완전히 빈 응답만 제외합니다. 내용 중복은 경고하고 보존합니다. 결측값은 임의 대치하지 않습니다. 오류 1건 이상이면 전체 중단합니다.')
            confirmed=st.checkbox('식별정보 제거·문항 유형·중복/제외 건수를 확인했습니다',key='survey-confirm-'+token+preview['sha256']+hashlib.sha256(json.dumps(definitions,sort_keys=True).encode()).hexdigest())
            if st.button('설문 응답 저장',disabled=not confirmed or bool(preview['errors']) or preview['repeated']):
                result=service.upload(pid,blob,definitions,confirmed,encoding);st.success(str(result));st.rerun()
        return
    questions=service.rows('survey_questions',pid)
    if not questions: st.info('설문 CSV를 업로드하세요.');return
    if page=='설문 문항·변수 관리':
        qid=st.selectbox('설문 문항 선택',[q['id'] for q in questions],format_func=lambda i:next(q['name'] for q in questions if q['id']==i))
        q=next(q for q in questions if q['id']==qid)
        name=st.text_input('표시 문항명',q['name'],key=qid+'name'+token)
        variable=st.text_input('변수명',q['variable'],key=qid+'var'+token)
        definitions=config_editor({q['variable']:json.loads(q['config_json'])},qid+token)
        st.caption('설정만 바꿉니다. 원본 응답을 수정하지 않으며 기존 응답에 적용 불가능한 유형 변경은 차단합니다.')
        if st.button('문항 설정 저장'):
            service.update_question(qid,name,variable,definitions[q['variable']]);st.success('문항 설정을 저장했습니다.')
        return
    frame,questions=service.frame(pid);full_n=len(frame)
    choices={q['variable']:q for q in questions if json.loads(q['config_json'])['kind']!='분석 제외'}
    filters={}
    with st.expander('응답 집단 필터'):
        filterable=[k for k,q in choices.items() if json.loads(q['config_json'])['kind'] in ('단일 선택형','리커트 척도형')]
        field=st.selectbox('필터 변수',['전체']+filterable)
        if field!='전체':
            options=st.multiselect('포함할 응답값',list(frame[field].dropna().unique()))
            if options: frame=frame[frame[field].isin(options)];filters={field:options}
    st.write(f'분석 표본 N={len(frame)} / 프로젝트 전체 N={full_n}')
    with st.expander('분석 적용 응답 목록 (읽기 전용)'):
        st.dataframe(frame,hide_index=True)
    with st.expander('원본 답변 조회 (읽기 전용)'):
        raw=service.original_answers(pid)
        st.dataframe(pd.DataFrame(raw),hide_index=True)
    if len(frame)<30: st.warning('표본이 30건 미만입니다. 탐색 결과를 전체 소비자로 일반화하지 마세요. 30건은 유의성 기준이 아닙니다.')
    if not choices: st.info('분석할 변수가 없습니다.');return
    tables={};meta={}
    if page=='교차분석':
        eligible=[k for k,q in choices.items() if json.loads(q['config_json'])['kind'] in ('단일 선택형','리커트 척도형')]
        if len(eligible)<2: st.info('범주형·리커트 변수 2개 이상이 필요합니다.');return
        first=st.selectbox('집단 변수',eligible);second=st.selectbox('비교 변수',[k for k in eligible if k!=first])
        meta,counts,percents,group=cross(frame,choices[first],choices[second]);st.write(meta)
        st.dataframe(counts);st.caption('집단별 행 비율 (%)');st.dataframe(percents)
        if group is not None: st.caption('집단별 리커트 평균과 유효 N');st.dataframe(group)
        tables={'counts':counts,'row_percent':percents}
        if group is not None: tables['group_mean']=group
        context={'title':first+' × '+second,'caption':f'{library.mode} | {names[pid]} | N={meta["n"]}/{full_n} | {meta["policy"]} | 필터={filters}'}
        if group is not None:
            image=figure(group.reset_index(),'막대',context)
            st.image(image);st.download_button('집단별 평균 PNG',image,'survey-group.png','image/png')
        st.image(figure(counts,'히트맵',context))
        st.download_button('교차표 PNG',figure(counts,'히트맵',context),'survey-cross.png','image/png')
        for label,t in tables.items(): st.download_button(label+' CSV',write_csv(['집단']+[safe_cell(c) for c in t.columns]+['유효N','결측제외N','처리조건','데이터모드','필터'],[[safe_cell(i)]+[safe_cell(v) for v in row]+[meta['n'],meta['excluded'],meta['policy'],library.mode,safe_cell(json.dumps(filters,ensure_ascii=False))] for i,row in t.iterrows()]),'survey-'+label+'.csv','text/csv')
    else:
        selected=st.selectbox('분석 문항',list(choices),format_func=lambda k:choices[k]['name'])
        meta,table=descriptive(frame,choices[selected]);st.write(meta);st.dataframe(table,hide_index=True)
        kind=st.selectbox('설문 그래프',['막대','가로 막대','도넛','리커트 분포'])
        context={'title':names[pid]+' / '+selected,'caption':f'{library.mode} | N={len(frame)}/{full_n} | 결측={meta["missing"]} | 분모={meta["denominator"]} | {meta["missing_policy"]} | 필터={filters}'}
        if kind=='도넛' and json.loads(choices[selected]['config_json'])['kind']=='다중 선택형':
            st.info('다중 선택 비율은 합이 100%를 넘을 수 있어 도넛을 제공하지 않습니다.')
        else:
            image=figure(table,kind,context);st.image(image);st.download_button('설문 그래프 PNG',image,'survey-chart.png','image/png')
        table=table.assign(데이터모드=library.mode,프로젝트=names[pid],전체N=full_n,선택N=len(frame),결측N=meta['missing'],결측기준=meta['missing_policy'],역문항=meta['reverse'],필터=json.dumps(filters,ensure_ascii=False))
        st.download_button('설문 집계 CSV',write_csv(list(table.columns),[[safe_cell(v) for v in row] for row in table.itertuples(index=False,name=None)]),'survey-summary.csv','text/csv')
        tables={'distribution':table}
    if st.button('현재 분석 결과 기록'):
        results={k:t.reset_index().to_json(orient='records',force_ascii=False) for k,t in tables.items()}
        service.save_analysis(pid,dict(filters=filters,input_ids=frame['id'].tolist(),questions=questions,meta=meta),results);st.success('분석 조건·문항 정의·결과를 저장했습니다.')
    if page=='그래프 및 결과 내보내기':
        st.dataframe(pd.DataFrame(service.rows('survey_analysis_runs',pid)),hide_index=True)


def backup_panel(library,token):
    stamp=datetime.now().strftime('%Y%m%d-%H%M%S')
    st.download_button('설문 전체 백업 CSV',survey_transfer.export_backup(library),f'survey-backup-{library.mode}-{stamp}.csv','text/csv')
    uploaded=st.file_uploader('설문 전용 백업 복원',type=['csv'],key='survey-restore-'+token)
    if uploaded:
        preview=survey_transfer.preview(library,uploaded.getvalue());st.write('추가 예정 레코드',preview['added'])
        confirmed=st.checkbox('기존 자료를 덮어쓰지 않는 추가 복원과 과거 분석 기록 승계를 확인했습니다',key='survey-restore-confirm-'+token+hashlib.sha256(uploaded.getvalue()).hexdigest())
        if st.button('설문 백업 추가 복원',disabled=not confirmed):
            count=survey_transfer.restore(library,uploaded.getvalue(),confirmed);st.success(f'{count}레코드 추가 복원')
