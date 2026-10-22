from io import BytesIO
from pathlib import Path

import streamlit as st
from streamlit.testing.v1 import AppTest

from reviewlab.config import database_path
from reviewlab.repositories.library import Library
from reviewlab.services.source_import import LABELS
from reviewlab.services.transfer import write_csv

APP = str(Path(__file__).parents[1] / 'app.py')


def element(items, label):
    return next(item for item in items if item.label == label)


def payload(rows=None):
    return write_csv(list(LABELS.values()), rows or [['[허구] 화면 자료', '가상 기관', '2025-02', '학술논문',
        'https://example.com/ui-import', '화면 메모', '테스트 목적', '소비자 신뢰도|화면 태그']])


def start(tmp_path, monkeypatch, content):
    monkeypatch.setenv('REVIEWLAB_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(st, 'file_uploader', lambda *args, **kwargs: BytesIO(content))
    app = AppTest.from_file(APP, default_timeout=20).run()
    app.sidebar.radio[0].set_value('자료 등록').run()
    assert element(app.radio, '등록 방식').value == '직접 등록'
    assert element(app.button, '자료 저장')
    element(app.radio, '등록 방식').set_value('CSV 일괄 등록').run()
    assert not app.exception
    return app


def test_ui_upload_preview_commit_search_tags_and_separate_mode(tmp_path, monkeypatch):
    app = start(tmp_path, monkeypatch, payload())
    assert [m.value for m in app.metric] == ['1', '1', '0', '0']
    assert not Library(database_path('actual'), 'actual').list_sources()
    assert element(app.button, 'CSV 일괄 등록 실행').disabled
    assert len(app.get('download_button')) == 1
    element(app.checkbox, '실제 연구자료에 등록하고 새 자료는 미검증으로 저장함을 확인했습니다').check().run()
    element(app.button, 'CSV 일괄 등록 실행').click().run()
    assert not app.exception and app.success
    assert '추가 1건 · 중복 0건 · 실패 0건' in app.success[0].value
    assert [m.value for m in app.metric] == ['1', '0', '1', '0']
    assert element(app.button, 'CSV 일괄 등록 실행').disabled
    app.sidebar.radio[0].set_value('자료 목록').run()
    element(app.text_input, '제목 및 저자 검색').set_value('가상 기관').run()
    element(app.selectbox, '태그 필터').set_value('화면 태그').run()
    assert app.dataframe[0].value.iloc[0]['제목'] == '[허구] 화면 자료'
    assert app.dataframe[0].value.iloc[0]['검증 상태'] == '미검증'
    app.sidebar.selectbox[0].set_value('예시·연습 자료').run()
    assert not Library(database_path('example'), 'example').list_sources()
    assert len(Library(database_path('actual'), 'actual').list_sources()) == 1
    app.sidebar.radio[0].set_value('자료 등록').run()
    element(app.radio, '등록 방식').set_value('CSV 일괄 등록').run()
    assert not element(app.checkbox, '예시·연습 자료에 등록하고 새 자료는 미검증으로 저장함을 확인했습니다').value
    assert element(app.button, 'CSV 일괄 등록 실행').disabled


def test_ui_cp949_bad_default_then_encoding_selection(tmp_path, monkeypatch):
    cp949 = payload().decode('utf-8-sig').encode('cp949')
    app = start(tmp_path, monkeypatch, cp949)
    assert app.error and element(app.button, 'CSV 일괄 등록 실행').disabled
    element(app.selectbox, '참고문헌 CSV 인코딩').set_value('cp949').run()
    assert not app.exception and not app.error
    assert [m.value for m in app.metric] == ['1', '1', '0', '0']
    element(app.checkbox, '실제 연구자료에 등록하고 새 자료는 미검증으로 저장함을 확인했습니다').check().run()
    element(app.button, 'CSV 일괄 등록 실행').click().run()
    assert app.success
    assert Library(database_path('actual'), 'actual').list_sources()[0]['authors'] == '가상 기관'


def test_ui_error_rows_are_identified_and_registration_blocked(tmp_path, monkeypatch):
    content = payload([['[허구] 정상', '', '', '기타', 'https://example.com/ok', '', '', '새 태그'],
                       ['[허구] 잘못된 주소', '', '', '기타', 'javascript:alert(1)', '', '', '']])
    app = start(tmp_path, monkeypatch, content)
    assert [m.value for m in app.metric] == ['2', '1', '0', '1']
    assert app.error
    error_table = app.dataframe[-1].value
    assert error_table.iloc[0]['CSV 행'] == 3
    assert 'http' in error_table.iloc[0]['오류 원인']
    element(app.checkbox, '실제 연구자료에 등록하고 새 자료는 미검증으로 저장함을 확인했습니다').check().run()
    assert element(app.button, 'CSV 일괄 등록 실행').disabled
    assert not Library(database_path('actual'), 'actual').list_sources()


def test_ui_write_failure_reports_zero_additions(tmp_path, monkeypatch):
    import sqlite3
    original = Library.insert_source
    def fail(db, source):
        if source['title'] == '실패 주입':
            raise sqlite3.OperationalError('test database write error')
        return original(db, source)
    monkeypatch.setattr(Library, 'insert_source', staticmethod(fail))
    content = payload([['[허구] 정상', '', '', '기타', 'https://example.com/ok', '', '', '롤백 태그'],
                       ['실패 주입', '', '', '기타', 'https://example.com/fail', '', '', '']])
    app = start(tmp_path, monkeypatch, content)
    element(app.checkbox, '실제 연구자료에 등록하고 새 자료는 미검증으로 저장함을 확인했습니다').check().run()
    element(app.button, 'CSV 일괄 등록 실행').click().run()
    assert not app.exception and app.error
    assert '추가 0건 · 중복 0건 · 실패 2건' in app.error[0].value
    library = Library(database_path('actual'), 'actual')
    assert not library.list_sources()
    assert '롤백 태그' not in {t['name'] for t in library.tags()}
