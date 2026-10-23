import pytest
from reviewlab.repositories.library import Library
from reviewlab.services.research import Research


@pytest.fixture(autouse=True)
def local_storage_by_default(monkeypatch):
    # 사용자의 실제 Supabase 설정이 있어도 기존 UI 테스트가 접속하지 않게 한다.
    monkeypatch.setenv('REVIEWLAB_STORAGE', 'local')
    from reviewlab.ui import cloud
    monkeypatch.setattr(cloud, 'config', lambda: dict(url='', key='', storage='local'))


@pytest.fixture
def library(tmp_path):
    return Library(tmp_path / 'actual' / 'reviewlab.sqlite3', 'actual')


@pytest.fixture
def service(library):
    return Research(library)


@pytest.fixture
def source():
    # 실제 참고문헌이 아닌 테스트 전용 허구 자료.
    return dict(title='[허구] 소비자 리뷰 연구', authors='가상 연구팀', publication_date='2025-02',
                source_type='학술논문', original_url='https://example.com/paper',
                notes='실제 논문이 아닙니다.\n한글, 쉼표와 "인용" 보존', purpose='테스트 전용')


@pytest.fixture
def event():
    return dict(status='검증 완료', checked_on='2025-02-28', checked_by='테스트 담당',
                evidence='허구 테스트 원문 3쪽', memo='테스트 안에서만 검증 흐름 확인')
