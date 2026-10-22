from pathlib import Path

from streamlit.testing.v1 import AppTest

from reviewlab.config import database_path
from reviewlab.repositories.library import Library
from reviewlab.services.research import Research
from reviewlab.services.transfer import export_backup

APP = str(Path(__file__).parents[1] / 'app.py')


def element(elements, label):
    return next(e for e in elements if e.label == label)


def navigate(app, page):
    app.sidebar.radio[0].set_value(page).run()
    assert not app.exception
    return app


def test_ui_registration_edit_verification_delete_and_restart(tmp_path, monkeypatch):
    monkeypatch.setenv('REVIEWLAB_DATA_DIR', str(tmp_path))
    app = AppTest.from_file(APP, default_timeout=15).run()
    assert not app.exception
    assert app.metric[0].value == '0'
    navigate(app, '자료 등록')
    element(app.button, '자료 저장').click().run()
    assert app.error
    element(app.text_input, '자료 제목 *').set_value('[허구] 화면 테스트')
    element(app.text_input, '저자 또는 작성 기관 (선택)').set_value('가상 팀')
    element(app.text_input, '원문 URL *').set_value('https://example.com/ui')
    element(app.button, '자료 저장').click().run()
    assert not app.exception
    assert app.success
    app = AppTest.from_file(APP).run()  # 새로운 세션에서도 DB 유지
    assert app.metric[0].value == '1'
    navigate(app, '자료 목록')
    element(app.text_input, '제목 및 저자 검색').set_value('없는 자료').run()
    assert any('조건에 맞는' in e.value for e in app.info)
    element(app.text_input, '제목 및 저자 검색').set_value('가상').run()
    element(app.text_input, '자료 제목 *').set_value('[허구] 수정 자료')
    element(app.button, '수정 저장').click().run()
    assert app.success
    navigate(app, '검증 관리')
    element(app.selectbox, '새 검증 상태').set_value('검증 완료')
    element(app.button, '검증 기록 저장').click().run()
    assert app.error
    element(app.selectbox, '새 검증 상태').set_value('검증 완료')
    element(app.text_input, '검증 담당자 (별칭 권장)').set_value('테스트 담당')
    element(app.text_area, '확인한 원문 또는 공식 자료').set_value('허구 문서')
    element(app.text_area, '검증 관련 메모').set_value('테스트용 확인')
    element(app.checkbox, '원문을 직접 확인하고 등록 정보와 대조했습니다').check()
    element(app.button, '검증 기록 저장').click().run()
    assert app.success
    navigate(app, '메인 대시보드')
    assert app.metric[1].value == '1'
    navigate(app, '자료 목록')
    assert element(app.button, '자료 삭제').disabled
    element(app.checkbox, '선택한 자료와 검증 이력을 삭제하겠습니다').check().run()
    element(app.button, '자료 삭제').click().run()
    assert not app.exception
    assert app.success
    assert Library(database_path('actual'), 'actual').list_sources() == []


def test_ui_tags_modes_and_settings(tmp_path, monkeypatch):
    monkeypatch.setenv('REVIEWLAB_DATA_DIR', str(tmp_path))
    app = AppTest.from_file(APP).run()
    navigate(app, '설정 및 데이터 관리')
    element(app.text_input, '새 태그 이름').set_value('허구 태그')
    element(app.button, '태그 추가').click().run()
    assert app.success
    tag = next(t for t in Library(database_path('actual'), 'actual').tags() if t['name'] == '허구 태그')
    element(app.selectbox, '수정할 태그').set_value(tag['id']).run()
    element(app.text_input, '변경할 태그 이름').set_value('수정 태그')
    element(app.button, '태그 이름 변경').click().run()
    assert app.success
    app.sidebar.selectbox[0].set_value('예시·연습 자료').run()
    assert not app.exception
    assert '수정 태그' not in [t['name'] for t in Library(database_path('example'), 'example').tags()]


def test_ui_csv_restore_flow(tmp_path, monkeypatch, source):
    # AppTest는 업로드 위젯 조작을 지원하지 않아 업로드 객체만 주입한다.
    from io import BytesIO
    import streamlit as st
    donor = Library(tmp_path / 'donor.sqlite3', 'actual')
    Research(donor).save(source)
    uploaded = BytesIO(export_backup(donor))
    monkeypatch.setenv('REVIEWLAB_DATA_DIR', str(tmp_path / 'appdata'))
    monkeypatch.setattr(st, 'file_uploader', lambda *args, **kwargs: uploaded)
    app = AppTest.from_file(APP).run()
    navigate(app, '설정 및 데이터 관리')
    assert element(app.button, 'CSV 복원 실행').disabled
    element(app.checkbox, '추가 복원 정책을 확인했으며, 신뢰하는 백업의 기존 검증 이력을 승계합니다').check().run()
    element(app.button, 'CSV 복원 실행').click().run()
    assert not app.exception
    assert app.success
    assert len(Library(database_path('actual'), 'actual').list_sources()) == 1


def test_invalid_form_preserves_input(tmp_path, monkeypatch):
    monkeypatch.setenv('REVIEWLAB_DATA_DIR', str(tmp_path))
    app = AppTest.from_file(APP).run()
    navigate(app, '자료 등록')
    element(app.text_input, '자료 제목 *').set_value('보존할 제목')
    element(app.text_input, '원문 URL *').set_value('invalid')
    element(app.button, '자료 저장').click().run()
    assert app.error
    assert element(app.text_input, '자료 제목 *').value == '보존할 제목'
    element(app.text_input, '원문 URL *').set_value('https://example.com/valid')
    element(app.button, '자료 저장').click().run()
    assert app.success
    assert element(app.text_input, '자료 제목 *').value == ''


def test_dashboard_counts_and_mode_switch(tmp_path, monkeypatch, source, event):
    monkeypatch.setenv('REVIEWLAB_DATA_DIR', str(tmp_path))
    library = Library(database_path('actual'), 'actual')
    service = Research(library)
    tags = library.tags()[:2]
    sid = service.save(source, [t['id'] for t in tags])
    service.verify(sid, event, True)
    service.save(dict(source, title='미검증 자료', original_url='https://example.com/unverified', source_type='기타'))
    app = AppTest.from_file(APP).run()
    assert [m.value for m in app.metric] == ['2', '1', '1']
    kinds = app.dataframe[0].value
    assert kinds.loc['학술논문', '자료 수'] == 1
    assert kinds.loc['기타', '자료 수'] == 1
    tag_counts = app.dataframe[2].value.set_index('태그')
    assert all(tag_counts.loc[t['name'], '자료 수'] == 1 for t in tags)
    app.sidebar.selectbox[0].set_value('예시·연습 자료').run()
    assert app.metric[0].value == '0'
    assert app.warning


def test_settings_shows_automatic_backup(tmp_path, monkeypatch):
    monkeypatch.setenv('REVIEWLAB_DATA_DIR', str(tmp_path))
    app = AppTest.from_file(APP).run()
    navigate(app, '설정 및 데이터 관리')
    assert any(s.value == '자동 백업' for s in app.subheader)
    assert any('최근 백업 시각' in s.value for s in app.text)
    assert any('backups' in s.value and 'automatic' in s.value for s in app.code)


def test_backup_failure_warning_is_visible(tmp_path, monkeypatch):
    monkeypatch.setenv('REVIEWLAB_DATA_DIR', str(tmp_path))
    def failed_backup(self, **kwargs):
        self.backup_warning = '자동 백업 실패 테스트'
        return None
    monkeypatch.setattr(Library, 'automatic_backup', failed_backup)
    app = AppTest.from_file(APP).run()
    assert not app.exception
    assert any('자동 백업 실패 테스트' in w.value for w in app.warning)


def test_new_backup_menu_and_dated_downloads(tmp_path, monkeypatch, source):
    import re
    import streamlit as st
    monkeypatch.setenv('REVIEWLAB_DATA_DIR', str(tmp_path))
    library = Library(database_path('actual'), 'actual')
    Research(library).save(source)
    downloads = {}
    original = st.download_button
    def capture(label, data, **kwargs):
        downloads[label] = (data, kwargs['file_name'])
        return original(label, data, **kwargs)
    monkeypatch.setattr(st, 'download_button', capture)
    app = AppTest.from_file(APP).run()
    navigate(app, '데이터 백업 및 복원')
    assert len(downloads) == 2
    for data, filename in downloads.values():
        assert data.startswith(b'\xef\xbb\xbf')
        assert re.search(r'\d{4}-\d{2}-\d{2}_\d{6}-KST\.csv$', filename)
    comparison = app.table[0].value
    assert comparison['앱 복원'].tolist() == ['불가', '가능']


def test_new_backup_menu_preview_restore_and_display(tmp_path, monkeypatch, source):
    from io import BytesIO
    import streamlit as st
    donor = Library(tmp_path / 'donor.sqlite3', 'actual')
    Research(donor).save(source)
    # 디스크에 실제 저장한 파일을 다시 읽어 업로드 객체에 전달한다.
    csv_path = tmp_path / 'saved-backup.csv'
    csv_path.write_bytes(export_backup(donor))
    monkeypatch.setenv('REVIEWLAB_DATA_DIR', str(tmp_path / 'application'))
    monkeypatch.setattr(st, 'file_uploader', lambda *args, **kwargs: BytesIO(csv_path.read_bytes()))
    app = AppTest.from_file(APP).run()
    navigate(app, '데이터 백업 및 복원')
    assert [m.value for m in app.metric] == ['1', '1', '0', '0']
    assert Library(database_path('actual'), 'actual').list_sources() == []
    assert element(app.button, 'CSV 복원 실행').disabled
    element(app.checkbox, '추가 복원 정책을 확인했으며, 신뢰하는 백업의 기존 검증 이력을 승계합니다').check().run()
    element(app.button, 'CSV 복원 실행').click().run()
    assert app.success and not app.exception
    assert [m.value for m in app.metric] == ['1', '0', '1', '0']
    navigate(app, '자료 목록')
    assert app.dataframe[0].value.iloc[0]['제목'] == source['title']
    assert app.dataframe[0].value.iloc[0]['저자·기관'] == source['authors']


def test_new_backup_menu_rejects_invalid_upload(tmp_path, monkeypatch):
    from io import BytesIO
    import streamlit as st
    monkeypatch.setenv('REVIEWLAB_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(st, 'file_uploader', lambda *args, **kwargs: BytesIO(b'wrong,columns\n1,2'))
    app = AppTest.from_file(APP).run()
    navigate(app, '데이터 백업 및 복원')
    assert not app.exception
    assert [m.value for m in app.metric] == ['확인 불가', '확인 불가', '확인 불가', '1']
    assert app.error
    assert element(app.button, 'CSV 복원 실행').disabled
    assert Library(database_path('actual'), 'actual').list_sources() == []
