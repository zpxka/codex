"""자료 등록 메뉴 안의 일반 참고문헌 CSV 일괄 등록 화면."""
import hashlib
import sqlite3

import pandas as pd
import streamlit as st

from reviewlab.config import MODES
from reviewlab.services.source_import import LABELS, import_csv, preview_import, template_csv


def main(library):
    st.subheader('CSV 일괄 등록')
    mode_label = next(label for label, value in MODES.items() if value == library.mode)
    st.caption(f'등록 대상: {mode_label}. 새 자료의 검증 상태는 모두 미검증입니다.')
    st.download_button('일반 참고문헌 CSV 양식 다운로드', data=template_csv(),
                       file_name='reviewlab-sources-template.csv', mime='text/csv')
    st.info('필수 열: 제목, 자료 유형, 원문 URL. 선택 열: 저자·기관, 발행일, 핵심 메모, 활용 목적, 태그. '
            '여러 태그는 소비자 신뢰도|구매 의사결정처럼 |로 구분하세요.')
    st.caption('자료 유형: 학술논문 / 뉴스기사 / 정부·공공기관 자료 / 플랫폼 정책 / 기타. '
               '발행일: YYYY, YYYY-MM, YYYY-MM-DD 또는 공란. 최대 10MB·5,000개 자료.')
    st.caption('이 기능은 일반 자료의 신규 등록입니다. 검증 이력을 되살리려면 데이터 백업 및 복원에서 전용 백업을 사용하세요. '
               '중복 자료의 내용·태그·검증 이력은 변경하지 않습니다.')
    file = st.file_uploader('일반 참고문헌 CSV 업로드', type=['csv'], key=f'source-import-file-{library.mode}')
    if not file:
        return
    encoding = st.selectbox('참고문헌 CSV 인코딩', ['utf-8-sig', 'cp949'],
                            format_func=lambda value: 'UTF-8 / UTF-8 BOM (권장)' if value == 'utf-8-sig' else 'CP949 (한글 Windows CSV)',
                            key=f'source-import-encoding-{library.mode}')
    content = file.getvalue()
    try:
        report = preview_import(library, content, encoding)
    except ValueError as error:
        st.error(f'파일 오류 1건 · 등록 차단: {error}')
        st.button('CSV 일괄 등록 실행', disabled=True)
        return
    with st.expander('자동으로 연결한 CSV 열'):
        st.table(pd.DataFrame([{'CSV 열': column, '등록 항목': LABELS.get(field, '데이터 구분')}
                              for field, column in report['mapping'].items()]))
        if report['ignored_columns']:
            st.caption('가져오지 않는 열: ' + ', '.join(report['ignored_columns']) + '. 검증 상태·담당자·이력도 가져오지 않습니다.')
    st.subheader('등록 미리보기')
    for column, label, value in zip(st.columns(4), ('전체 자료', '추가 예정', '중복 건너뛰기', '오류 자료'),
                                    (report['total'], report['added'], report['skipped'], report['failed'])):
        column.metric(label, value)
    visible = report['rows'][:200]
    st.dataframe(pd.DataFrame([{'CSV 행': r['row'], '제목': r['title'], '저자·기관': r['source']['authors'],
        '자료 유형': r['source']['source_type'], '원문 URL': r['source']['original_url'], '태그': ' | '.join(r['tags']),
        '처리': r['status'], '이유': r['reason']} for r in visible]), hide_index=True, width='stretch')
    if len(report['rows']) > 200:
        st.caption('미리보기 표는 처음 200개 자료입니다. 건수와 오류 목록은 전체 파일을 검사한 결과입니다.')
    if report['failed']:
        errors = [r for r in report['rows'] if r['status'] == '오류']
        st.error('오류가 있어 전체 등록을 막았습니다. CSV 원본에서 표시된 행을 수정하고 저장한 뒤 다시 업로드하세요. 아직 추가된 자료는 없습니다.')
        st.dataframe(pd.DataFrame([{'CSV 행': r['row'], '제목': r['title'], '오류 원인': r['reason']} for r in errors]),
                     hide_index=True, width='stretch')
        st.caption('CSV 행은 첫 제목 행을 1행으로 센 자료 순번입니다. 셀 안 줄바꿈은 별도 자료로 세지 않습니다.')
    if report['skipped']:
        st.caption('기존 자료 및 CSV 내부에서 같은 URL 또는 같은 제목·저자·발행일을 찾았습니다. 먼저 나온 정상 자료를 유지하고 중복은 건너뜁니다.')
    digest = hashlib.sha256(content).hexdigest()
    confirmed = st.checkbox(f'{mode_label}에 등록하고 새 자료는 미검증으로 저장함을 확인했습니다',
                            key=f'source-import-confirm-{library.mode}-{encoding}-{digest}')
    if st.button('CSV 일괄 등록 실행', disabled=not confirmed or not report['can_import'] or report['added'] == 0, type='primary'):
        try:
            result = import_csv(library, content, encoding, confirmed_mode=library.mode if confirmed else None)
        except ValueError as error:
            st.error(str(error))
        except sqlite3.Error:
            st.error(f'저장 오류로 전체 등록을 취소했습니다. 추가 0건 · 중복 {report["skipped"]}건 · '
                     f'실패 {report["added"]}건. 기존 자료는 유지했습니다. 디스크 공간·저장 권한을 확인하고 다시 시도하세요.')
        else:
            st.session_state['notice'] = (f'CSV 일괄 등록 완료: 추가 {result["added"]}건 · 중복 {result["skipped"]}건 · '
                                          f'실패 {result["failed"]}건. 새 자료는 미검증입니다. 자료 목록에서 검색·태그 필터로 확인하세요.')
            st.rerun()
    st.caption('CSV의 수식처럼 보이는 값도 실행하지 않고 텍스트로 저장합니다. 목록 CSV 내보내기는 기존 수식 실행 방지 처리를 적용합니다.')
