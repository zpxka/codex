from pathlib import Path
from io import BytesIO
import streamlit as st
from streamlit.testing.v1 import AppTest
from reviewlab.repositories.library import Library
from test_documents import seed


def item(items,label):return next(x for x in items if x.label==label)
def test_document_search_manual_analysis_and_draft_ui(tmp_path,monkeypatch,source):
    monkeypatch.setenv('REVIEWLAB_DATA_DIR',str(tmp_path))
    library=Library(tmp_path/'actual/reviewlab.sqlite3','actual');service,doc=seed(library,source)
    app=AppTest.from_file(str(Path(__file__).parents[1]/'app.py'),default_timeout=25).run()
    app.sidebar.radio[0].set_value('원문·논문 검토').run()
    item(app.radio,'원문·논문 검토 화면').set_value('검색·문헌 비교').run()
    item(app.multiselect,'분석할 원문 범위').set_value([doc['id']]).run()
    item(app.text_input,'질문 또는 검색 문구').set_value('리뷰').run()
    item(app.multiselect,'인용할 근거 행').set_value([0]).run()
    item(app.text_area,'연구자가 작성한 분석·수정 의견').set_value('가상 수동 해석')
    item(app.button,'근거 연결 기록 추가').click().run()
    assert app.success and not app.exception and service.rows('document_notes')
    item(app.radio,'원문·논문 검토 화면').set_value('초고 검토').run()
    item(app.text_area,'직접 작성한 초고 텍스트 (선택)').set_value('서론\n모든 소비자가 만족한다.\n결론')
    item(app.button,'초고 버전 저장').click().run()
    assert not app.exception and len(service.rows())==2
    item(app.radio,'원문·논문 검토 화면').set_value('파일 포함 백업·복원').run()
    item(app.button,'파일 포함 전체 백업 만들기').click().run()
    assert not app.exception
    item(app.radio,'원문·논문 검토 화면').set_value('AI 연결 설정').run()
    assert not item(app.checkbox,'외부 AI 사용 허용 (기본 꺼짐)').value

    item(app.checkbox,'외부 AI 사용 허용 (기본 꺼짐)').set_value(True)
    item(app.button,'AI 연결 설정 저장').click().run()
    item(app.radio,'원문·논문 검토 화면').set_value('원문 업로드').run()
    assert app.session_state['document-ai-config']['enabled']
    item(app.button,'선택 원문 불러오기').click().run()
    assert not app.exception
