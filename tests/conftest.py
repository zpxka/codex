import json
from pathlib import Path
import platform

import pytest
import requests
from reviewlab.repositories.library import Library
from reviewlab.services.research import Research


@pytest.fixture(autouse=True)
def local_storage_by_default(monkeypatch, tmp_path):
    # 사용자의 실제 Supabase 설정이 있어도 기존 UI 테스트가 접속하지 않게 한다.
    monkeypatch.setenv('REVIEWLAB_STORAGE', 'local')
    monkeypatch.setenv('REVIEWLAB_DATA_DIR', str(tmp_path / 'isolated-app-data'))
    for key in ('REVIEWLAB_SUPABASE_URL', 'REVIEWLAB_SUPABASE_KEY'):
        monkeypatch.delenv(key, raising=False)
    # 기본 회귀 테스트는 HTTP 대역만 사용한다. 실접속은 별도 실행 파일에서만 한다.
    def no_network(*args, **kwargs):
        raise AssertionError('기본 테스트의 외부 HTTP 접속은 차단되어 있습니다.')
    monkeypatch.setattr(requests.sessions.Session, 'request', no_network)
    from reviewlab.ui import cloud
    monkeypatch.setattr(cloud, 'config', lambda: dict(url='', key='', storage='local'))


def pytest_addoption(parser):
    parser.addoption('--cloud-report', help='비밀값/자료 없이 테스트 ID와 상태만 JSON으로 기록')


def pytest_configure(config):
    config._cloud_results = {}


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    report = (yield).get_result()
    result = item.config._cloud_results.setdefault(item.nodeid, {'status': 'passed'})
    if report.failed:
        result.update(status='failed', phase=report.when)
    elif report.skipped and result['status'] != 'failed':
        result.update(status='skipped', phase=report.when)


def pytest_sessionfinish(session, exitstatus):
    target = session.config.getoption('--cloud-report')
    if not target:
        return
    results = session.config._cloud_results
    payload = dict(schema_version=1, platform=platform.system(), python=platform.python_version(),
                   exit_code=int(exitstatus),
                   counts={s: sum(r['status'] == s for r in results.values())
                           for s in ('passed', 'failed', 'skipped')}, tests=results,
                   scope='temporary data only; HTTP blocked; PostgreSQL auth.uid is a local stub')
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')


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
