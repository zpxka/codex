from io import BytesIO
from pathlib import Path
import streamlit as st
from streamlit.testing.v1 import AppTest
from reviewlab.services.surveys import Surveys
from reviewlab.repositories.library import Library
from test_surveys import seed

APP=str(Path(__file__).parents[1]/'app.py')
def element(items,label):return next(x for x in items if x.label==label)

def test_project_create_upload_statistics_cross_and_backup_ui(tmp_path,monkeypatch):
    monkeypatch.setenv('REVIEWLAB_DATA_DIR',str(tmp_path))
    app=AppTest.from_file(APP,default_timeout=25).run()
    app.sidebar.radio[0].set_value('설문조사 분석').run()
    element(app.text_input,'설문 프로젝트 이름').set_value('[가상] 화면 설문')
    element(app.button,'설문 프로젝트 생성').click().run()
    assert app.success and not app.exception
    element(app.radio,'설문 분석 화면').set_value('설문 CSV 업로드').run()
    from reviewlab.services.transfer import write_csv
    blob=write_csv(['집단','신뢰'],[['A',1],['B',5]])
    monkeypatch.setattr(st,'file_uploader',lambda *a,**kw:BytesIO(blob))
    app.run()
    assert not app.exception and element(app.button,'설문 응답 저장').disabled
    element(app.checkbox,'식별정보 제거·문항 유형·중복/제외 건수를 확인했습니다').check().run()
    element(app.button,'설문 응답 저장').click().run()
    assert not app.exception
    library=Library(tmp_path/'actual/reviewlab.sqlite3','actual')
    service=Surveys(library);pid=service.rows('survey_projects')[0]['id']
    assert len(service.frame(pid)[0])==2
    element(app.radio,'설문 분석 화면').set_value('기초 통계').run()
    assert not app.exception
    element(app.button,'현재 분석 결과 기록').click().run()
    assert app.success and service.rows('survey_analysis_runs',pid)
    monkeypatch.setattr(st,'file_uploader',lambda *a,**kw:None)
    element(app.radio,'설문 분석 화면').set_value('설문 문항·변수 관리').run()
    assert not app.exception
    element(app.text_input,'표시 문항명').set_value('수정된 문항')
    element(app.button,'문항 설정 저장').click().run()
    assert app.success and not app.exception


def test_all_analysis_views_existing_data_and_invalid_upload(tmp_path,monkeypatch):
    monkeypatch.setenv('REVIEWLAB_DATA_DIR',str(tmp_path))
    library=Library(tmp_path/'actual/reviewlab.sqlite3','actual');seed(library)
    app=AppTest.from_file(APP,default_timeout=25).run()
    app.sidebar.radio[0].set_value('설문조사 분석').run()
    for page in ('기초 통계','교차분석','그래프 및 결과 내보내기'):
        element(app.radio,'설문 분석 화면').set_value(page).run()
        assert not app.exception
    monkeypatch.setattr(st,'file_uploader',lambda *a,**kw:BytesIO(b'bad,bad\n1,2'))
    element(app.radio,'설문 분석 화면').set_value('설문 CSV 업로드').run()
    assert app.error and not app.exception


def test_backup_restore_accessible_without_project(tmp_path,monkeypatch):
    monkeypatch.setenv('REVIEWLAB_DATA_DIR',str(tmp_path/'app'))
    donor=Library(tmp_path/'donor.db','actual');seed(donor)
    from reviewlab.services.survey_transfer import export_backup
    monkeypatch.setattr(st,'file_uploader',lambda *a,**kw:BytesIO(export_backup(donor)))
    app=AppTest.from_file(APP,default_timeout=25).run()
    app.sidebar.radio[0].set_value('설문조사 분석').run()
    element(app.radio,'설문 분석 화면').set_value('그래프 및 결과 내보내기').run()
    assert not app.exception and element(app.button,'설문 백업 추가 복원').disabled
    element(app.checkbox,'기존 자료를 덮어쓰지 않는 추가 복원과 과거 분석 기록 승계를 확인했습니다').check().run()
    element(app.button,'설문 백업 추가 복원').click().run()
    assert app.success and not app.exception
