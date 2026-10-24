"""테스트 실행기 자체의 안전장치. 실제 접속 정보는 사용하지 않는다."""
import json
import xml.etree.ElementTree as ET

import pytest
import requests

from scripts import test_supabase_live as live
from reviewlab.cloud.client import CloudError, CloudUnavailable


def environment():
    return dict(REVIEWLAB_LIVE_TEST_CONFIRM='isolated-test-project-only',
                REVIEWLAB_TEST_SUPABASE_URL='https://abcdefghijklmnopqrst.supabase.co',
                REVIEWLAB_TEST_PROJECT_REF='abcdefghijklmnopqrst',
                REVIEWLAB_TEST_SUPABASE_KEY='sb_publishable_test_fake',
                REVIEWLAB_TEST_EMAIL_A='test-a@example.invalid', REVIEWLAB_TEST_PASSWORD_A='PRIVATE_A',
                REVIEWLAB_TEST_EMAIL_B='test-b@example.invalid', REVIEWLAB_TEST_PASSWORD_B='PRIVATE_B')


def test_live_configuration_never_falls_back_to_app_secrets():
    assert live.configuration({'REVIEWLAB_SUPABASE_URL': 'https://production.invalid',
                               'REVIEWLAB_SUPABASE_KEY': 'production-secret'}) is None
    config = live.configuration(environment())
    assert config.url == 'https://abcdefghijklmnopqrst.supabase.co'
    assert 'PRIVATE' not in repr(config)


@pytest.mark.parametrize('key,value', [
    ('REVIEWLAB_LIVE_TEST_CONFIRM', ''), ('REVIEWLAB_LIVE_TEST_CONFIRM', 'yes'),
    ('REVIEWLAB_TEST_PROJECT_REF', 'differentprojectxxxx'),
    ('REVIEWLAB_TEST_SUPABASE_URL', 'https://production.invalid'),
    ('REVIEWLAB_TEST_SUPABASE_KEY', 'sb_secret_PRIVATE'),
    ('REVIEWLAB_TEST_PASSWORD_A', ''), ('REVIEWLAB_TEST_EMAIL_B', 'TEST-A@EXAMPLE.INVALID'),
])
def test_live_refuses_partial_wrong_or_privileged_configuration(key, value):
    config = environment()
    config[key] = value
    with pytest.raises(live.UnsafeConfiguration):
        live.configuration(config)


@pytest.mark.parametrize('error,category', [
    (CloudUnavailable('PRIVATE'), 'external_environment_unavailable'),
    (requests.ReadTimeout('PRIVATE'), 'external_environment_unavailable'),
    (CloudError('PRIVATE'), 'auth_schema_or_access_configuration'),
    (AssertionError('PRIVATE'), 'behavior_mismatch'),
    (OSError('PRIVATE'), 'unexpected_test_error'),
])
def test_live_reports_only_fixed_categories_not_raw_exception(tmp_path, capsys, error, category):
    def fail():
        raise error
    results = []
    assert not live.record(results, 'live.synthetic_failure', fail)
    assert results[0]['category'] == category
    code = live.write_reports(results, tmp_path)
    assert code == (1 if category == 'behavior_mismatch' else 2)
    assert 'PRIVATE' not in capsys.readouterr().out
    for path in tmp_path.iterdir():
        assert 'PRIVATE' not in path.read_text(encoding='utf-8')
    assert ET.parse(tmp_path / 'supabase-live.xml').getroot().attrib['failures'] == '1'


def test_missing_live_credentials_reports_skipped_not_passed(tmp_path, monkeypatch):
    for name in (*[live.PREFIX + field for field in live.FIELDS], 'REVIEWLAB_LIVE_TEST_CONFIRM'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(live, 'ROOT', tmp_path)
    assert live.main() == 0
    result = json.loads((tmp_path / 'test-results/supabase-live.json').read_text())
    assert result['counts']['passed'] == result['counts']['failed'] == 0
    assert result['counts']['skipped'] == len(live.TESTS) + 1


def test_default_suite_blocks_real_http_and_uses_temporary_data_directory(tmp_path):
    import os
    with pytest.raises(AssertionError, match='차단'):
        requests.get('https://must-never-connect.example.invalid')
    assert os.environ['REVIEWLAB_DATA_DIR'].startswith(str(tmp_path))
    assert os.environ['REVIEWLAB_STORAGE'] == 'local'


def test_live_early_failure_cleans_only_ids_created_in_this_run(tmp_path, monkeypatch):
    calls = []
    class FakeClient:
        user_id = None
        def __init__(self, *args):
            self.label = len([call for call in calls if call[0] == 'init'])
            calls.append(('init', self.label))
        def sign_in(self, email, password):
            self.user_id = 'user-a' if email.endswith('a@example.invalid') else 'user-b'
        def rpc(self, name, params):
            calls.append((name, dict(params)))
            if name == 'create_workspace':
                return 'run-owned-' + str(self.label)
            assert params['p_workspace'].startswith('run-owned-')
            if name == 'read':
                return {'revision': 0, 'data': {t: [] for t in live.TABLES}}
            raise AssertionError()
        def clear(self):
            pass
    def fail(*args):
        raise CloudUnavailable('PRIVATE')
    monkeypatch.setattr(live, 'SupabaseClient', FakeClient)
    monkeypatch.setattr(live, 'CloudLibrary', fail)
    results = []
    live.exercise(live.configuration(environment()), results)
    assert results[-1]['test'] == 'live.cleanup_only_run_owned_workspace_data'
    assert results[-1]['status'] == 'passed'
    assert not any(name == 'commit' for name, _ in calls)
    assert any(row['category'] == 'external_environment_unavailable' for row in results)


def test_live_scenarios_run_end_to_end_against_explicit_fake_only(monkeypatch):
    from cloud_helpers import MemoryServer, MemoryClient
    servers = {}
    class Response:
        status_code = 406
        def json(self):
            return {'code': 'PGRST106'}
    class HTTP:
        def request(self, method, url, **kwargs):
            response = Response()
            if method == 'POST':
                response.status_code = 403
            return response
    class FakeSupabase:
        def __init__(self, url, key):
            self.url, self.key, self.http = url, key, HTTP()
            self.user_id = self.access_token = None
        def sign_in(self, email, password):
            self.user_id = 'a' if email.endswith('a@example.invalid') else 'b'
            self.access_token = 'fake-token'
        def rpc(self, name, params):
            if name == 'create_workspace':
                server = MemoryServer()
                server.members.add(self.user_id)
                servers[server.workspace] = server
                return server.workspace
            return MemoryClient(servers[params['p_workspace']], self.user_id).rpc(name, params)
        def clear(self):
            self.user_id = self.access_token = None
    monkeypatch.setattr(live, 'SupabaseClient', FakeSupabase)
    results = []
    live.exercise(live.configuration(environment()), results)
    assert len(results) == len(live.TESTS)
    assert all(row['status'] == 'passed' for row in results if 'direct_table_rls' not in row['test'])
    assert next(row for row in results if 'direct_table_rls' in row['test'])['status'] == 'skipped'
    assert all(not any(state['data'].values()) for server in servers.values() for state in server.states.values())


@pytest.mark.parametrize('failure', ['start', 'cleanup', None])
def test_postgres_runner_only_cleans_its_created_container_and_classifies_setup(tmp_path, monkeypatch, capsys, failure):
    import subprocess
    from scripts import test_cloud_postgres as runner
    migration = tmp_path / 'supabase/migrations/202610240001_shared_storage.sql'
    migration.parent.mkdir(parents=True)
    migration.write_text('-- 허구 테스트용 SQL', encoding='utf-8')
    (migration.parent/'202610250002_surveys.sql').write_text('-- 테스트 SQL',encoding='utf-8')
    monkeypatch.setattr(runner, 'ROOT', tmp_path)
    monkeypatch.setattr(runner.sys, 'argv', ['test_cloud_postgres.py'])
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        if command[:2] == ['docker', 'run'] and failure == 'start':
            raise subprocess.CalledProcessError(1, command, stderr='PRIVATE')
        return subprocess.CompletedProcess(command, int(command[:2] == ['docker', 'rm'] and failure == 'cleanup'))
    monkeypatch.setattr(runner.subprocess, 'run', run)
    assert runner.main() == (2 if failure else 0)
    cleanup = [call for call in calls if call[:2] == ['docker', 'rm']]
    assert bool(cleanup) == (failure != 'start')
    docker_run = calls[0]
    assert '-p' not in docker_run and '--publish' not in docker_run
    assert any(arg.startswith('reviewlab.test-run=') for arg in docker_run)
    if cleanup:
        assert cleanup[0][-1] == docker_run[docker_run.index('--name') + 1]
    assert 'PRIVATE' not in capsys.readouterr().out
    report = json.loads((tmp_path / 'test-results/postgres-environment.json').read_text())
    assert report['status'] == {'start': 'setup_failed', 'cleanup': 'cleanup_failed', None: 'tests_finished'}[failure]
