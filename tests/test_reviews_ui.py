from io import BytesIO
from pathlib import Path
import streamlit as st
from streamlit.testing.v1 import AppTest
from reviewlab.config import database_path
from reviewlab.repositories.library import Library
from reviewlab.services.reviews import ReviewService
from reviewlab.services import review_transfer as transfer
from reviewlab.ui.reviews import sample_csv, PAGES

APP=str(Path(__file__).parents[1]/'app.py')


def element(items,label):
    return next(item for item in items if item.label==label)


def navigate(app,page):
    element(app.radio,'리뷰 분석 메뉴').set_value(page).run(timeout=30)
    assert not app.exception
    return app


def start(tmp_path,monkeypatch):
    monkeypatch.setenv('REVIEWLAB_DATA_DIR',str(tmp_path))
    app=AppTest.from_file(APP,default_timeout=30).run()
    app.sidebar.selectbox[0].set_value('예시·연습 자료').run()
    app.sidebar.radio[0].set_value('리뷰 분석').run()
    assert not app.exception
    return app


def test_ui_create_upload_all_pages_notes_checks_and_mode(tmp_path,monkeypatch):
    app=start(tmp_path,monkeypatch)
    element(app.text_input,'프로젝트 이름').set_value('가상 화면 프로젝트')
    element(app.button,'프로젝트 생성').click().run()
    assert app.success
    monkeypatch.setattr(st,'file_uploader',lambda *a,**kw:BytesIO(sample_csv()))
    app.run()
    assert not app.exception
    element(app.text_area,'수집 경위·이용 권한 기록 (필수)').set_value('허구 예제 테스트')
    element(app.checkbox,'합법적으로 확보했고, 내용·메모의 개인정보를 제거했으며 프로젝트 별점 척도와 데이터 모드를 확인했습니다').check()
    element(app.button,'리뷰 저장').click().run()
    assert app.success and not app.exception
    library=Library(database_path('example'),'example')
    service=ReviewService(library)
    pid=service.repo.projects()[0]['id']
    assert len(service.repo.rows(pid))==12
    monkeypatch.setattr(st,'file_uploader',lambda *a,**kw:None)
    navigate(app,'리뷰 목록')
    assert len(app.dataframe[0].value)==12
    element(app.text_area,'추가 연구자 메모').set_value('화면 메모')
    element(app.button,'메모 추가').click().run()
    assert app.success
    navigate(app,'리뷰 검토 관리')
    element(app.selectbox,'검토 상태').set_value('검토 완료')
    element(app.text_input,'검토자').set_value('연구자 테스트')
    element(app.text_area,'판단 근거 (완료·제외 시 필수)').set_value('가상 원문 검토')
    element(app.button,'검토 기록 저장').click().run()
    assert app.success and not app.exception
    assert service.repo.rows(pid)[0]['status']=='검토 완료'
    navigate(app,'리뷰 통계 분석')
    assert len(app.get('plotly_chart'))==6
    assert len(app.get('download_button'))==6
    navigate(app,'리뷰 패턴 탐색')
    assert len(app.dataframe)==4
    element(app.button,'현재 분석 조건·결과 기록 저장').click().run()
    assert app.success
    navigate(app,'분석 결과 내보내기')
    assert len(app.get('download_button'))==3
    app.sidebar.selectbox[0].set_value('실제 연구자료').run()
    assert not app.exception
    assert not ReviewService(Library(database_path('actual'),'actual')).repo.projects()
    assert any('프로젝트를 만들거나' in e.value for e in app.info)


def test_ui_restore_from_disk_preview_and_list(tmp_path,monkeypatch):
    donor=ReviewService(Library(tmp_path/'donor.db','example'))
    pid=donor.create_project('복원 화면 가상 프로젝트')
    donor.upload(pid,sample_csv(),'허구 수집',True)
    path=tmp_path/'sample-backup.csv'
    path.write_bytes(transfer.export_backup(donor.repo,pid))
    app=start(tmp_path/'application',monkeypatch)
    monkeypatch.setattr(st,'file_uploader',lambda *a,**kw:BytesIO(path.read_bytes()))
    navigate(app,'분석 결과 내보내기')
    assert element(app.button,'리뷰 백업 복원 실행').disabled
    assert not ReviewService(Library(database_path('example'),'example')).repo.projects()
    element(app.checkbox,'추가 전용 복원 정책을 확인하고 백업의 과거 검토 이력을 승계합니다').check().run()
    element(app.button,'리뷰 백업 복원 실행').click().run()
    assert app.success and not app.exception
    monkeypatch.setattr(st,'file_uploader',lambda *a,**kw:None)
    navigate(app,'리뷰 목록')
    assert len(app.dataframe[0].value)==12
    assert '가상·테스트용' in app.dataframe[0].value.iloc[0]['리뷰 내용']


def test_ui_empty_charts_and_invalid_upload(tmp_path,monkeypatch):
    app=start(tmp_path,monkeypatch)
    element(app.text_input,'프로젝트 이름').set_value('빈 프로젝트')
    element(app.button,'프로젝트 생성').click().run()
    navigate(app,'리뷰 통계 분석')
    assert not app.exception
    assert len(app.get('plotly_chart'))==6
    monkeypatch.setattr(st,'file_uploader',lambda *a,**kw:BytesIO(b'wrong,columns\n1,2'))
    navigate(app,'리뷰 데이터 업로드')
    assert app.error and not app.exception
    navigate(app,'분석 결과 내보내기')
    assert app.error and not app.exception
