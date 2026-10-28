"""별도 Supabase 테스트 프로젝트에서만 수동 실행. 운영 secrets/작업공간을 읽지 않는다.

출력은 고정 단계 ID/상태/분류만 허용한다. 원시 예외/HTTP 응답/인증값은 출력하지 않는다.
"""
from contextlib import ExitStack
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from uuid import uuid4
import xml.etree.ElementTree as ET

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reviewlab.cloud.client import SupabaseClient, CloudError, CloudUnavailable, settings
from reviewlab.cloud.dataset import TABLES, fingerprint, read_local
from reviewlab.cloud.library import CloudLibrary
from reviewlab.services.research import Research

PREFIX = 'REVIEWLAB_TEST_'
FIELDS = ('SUPABASE_URL', 'SUPABASE_KEY', 'PROJECT_REF', 'EMAIL_A', 'PASSWORD_A', 'EMAIL_B', 'PASSWORD_B')
TESTS = (
    'auth_two_users_three_sessions', 'create_run_owned_workspaces', 'empty_isolated_modes',
    'source_create_search_cross_session_mode_isolation', 'conflict_refresh_explicit_reedit',
    'publishable_key_without_auth_denied', 'outsider_read_denied', 'outsider_write_denied',
    'reverse_workspace_read_denied', 'direct_table_rls_and_no_direct_write',
    'all_tables_histories_and_analysis', 'snapshot_and_csv_roundtrip',
    'lost_ack_explicit_same_id_recovery', 'explicit_delete_cross_session',
    'cleanup_only_run_owned_workspace_data',
)


class UnsafeConfiguration(Exception):
    pass


@dataclass(repr=False)
class Configuration:
    url: str
    key: str
    email_a: str
    password_a: str
    email_b: str
    password_b: str


def configuration(environment):
    # 앱의 REVIEWLAB_SUPABASE_* 및 Streamlit secrets로 절대 fallback하지 않는다.
    values = {name: environment.get(PREFIX + name, '') for name in FIELDS}
    if not any(values.values()) and not environment.get('REVIEWLAB_LIVE_TEST_CONFIRM'):
        return None
    if not all(values.values()) or environment.get('REVIEWLAB_LIVE_TEST_CONFIRM') != 'isolated-test-project-only':
        raise UnsafeConfiguration()
    ref = values['PROJECT_REF']
    if not re.fullmatch('[a-z0-9]{20}', ref):
        raise UnsafeConfiguration()
    if values['SUPABASE_URL'] != f'https://{ref}.supabase.co':
        raise UnsafeConfiguration()
    try:
        url, key = settings(values['SUPABASE_URL'], values['SUPABASE_KEY'])
    except CloudError:
        raise UnsafeConfiguration() from None
    if values['EMAIL_A'].casefold() == values['EMAIL_B'].casefold():
        raise UnsafeConfiguration()
    return Configuration(url, key, values['EMAIL_A'], values['PASSWORD_A'], values['EMAIL_B'], values['PASSWORD_B'])


def classification(error):
    if isinstance(error, (CloudUnavailable, requests.RequestException)):
        return 'external_environment_unavailable'
    if isinstance(error, CloudError):
        # Auth/권한/SQL 미적용도 가능하므로 코드 결함으로 단정하지 않는다.
        return 'auth_schema_or_access_configuration'
    if isinstance(error, AssertionError):
        return 'behavior_mismatch'
    if isinstance(error, UnsafeConfiguration):
        return 'unsafe_or_incomplete_test_configuration'
    return 'unexpected_test_error'


def record(results, name, action):
    start = time.monotonic()
    try:
        outcome = action()
    except Exception as error:
        results.append(dict(test=name, status='failed', category=classification(error),
                            seconds=round(time.monotonic()-start, 3)))
        return False
    results.append(dict(test=name, status='skipped' if outcome == 'skipped' else 'passed',
                        category='schema_not_exposed' if outcome == 'skipped' else 'verified',
                        seconds=round(time.monotonic()-start, 3)))
    return True


def check(value):
    if not value:
        raise AssertionError()


def denied(client, workspace, mode='example', write=False):
    params = dict(p_workspace=workspace, p_mode=mode)
    if write:
        params.update(p_expected_revision=0, p_operation=str(uuid4()), p_data={t: [] for t in TABLES})
    # HTTP 상태/SQLSTATE만 판정하며 원시 응답은 결과/로그에 기록하지 않는다.
    response = client.http.request('POST', client.url + '/rest/v1/rpc/reviewlab_' + ('commit_v3' if write else 'read_v3'),
        headers={'apikey': client.key, **({'Authorization': 'Bearer ' + client.access_token} if client.access_token else {})},
        json=params, timeout=(5, 30), allow_redirects=False)
    if response.status_code >= 500:
        raise CloudUnavailable()
    check(response.status_code in (401, 403))


def direct_rls(member, outsider, workspace):
    headers = {'apikey': member.key, 'Accept-Profile': 'reviewlab'}
    endpoint = member.url + '/rest/v1/sources'
    args = dict(params={'workspace_id': 'eq.' + workspace, 'select': 'id'}, timeout=(5, 30), allow_redirects=False)
    response = member.http.request('GET', endpoint, headers=dict(headers, Authorization='Bearer '+member.access_token), **args)
    if response.status_code == 406:
        # Supabase 기본값에서는 reviewlab schema가 Data API에 공개되지 않는다.
        # 스키마 비공개와 실제 RLS 행 필터 통과를 혼동하지 않는다.
        try:
            if response.json().get('code') == 'PGRST106':
                return 'skipped'
        except (ValueError, AttributeError):
            pass
    check(response.status_code == 200 and len(response.json()) > 0)
    response = outsider.http.request('GET', endpoint, headers=dict(headers, Authorization='Bearer '+outsider.access_token), **args)
    check(response.status_code == 200 and response.json() == [])
    response = member.http.request('DELETE', endpoint, headers=dict(headers, Authorization='Bearer '+member.access_token), **args)
    check(response.status_code in (401, 403))  # CAS 우회 직접 쓰기는 구성원도 불가


def exercise(cfg, results):
    # 모든 삭제 대상은 이 실행의 create_workspace 응답 ID에서만 온다.
    # 기존 작업공간 목록/운영 DB/서비스 키/관리 SQL은 사용하지 않는다.
    owned = []
    libraries = []
    clients = [SupabaseClient(cfg.url, cfg.key) for _ in range(3)]
    first, second, outsider = clients
    with tempfile.TemporaryDirectory(prefix='reviewlab-live-test-') as directory, ExitStack() as stack:
        def login():
            first.sign_in(cfg.email_a, cfg.password_a)
            second.sign_in(cfg.email_a, cfg.password_a)
            outsider.sign_in(cfg.email_b, cfg.password_b)
            check(first.user_id == second.user_id and first.user_id != outsider.user_id)
        if not record(results, 'live.auth_two_users_three_sessions', login):
            return
        def create():
            for client in (first, outsider):
                wid = client.rpc('create_workspace', {'p_name': 'REVIEWLAB-AUTOTEST-' + uuid4().hex})
                owned.append((client, wid))
        try:
            if not record(results, 'live.create_run_owned_workspaces', create):
                return
            wid = owned[0][1]
            def setup():
                for client, mode, label in ((first, 'example', 'laptop'), (second, 'example', 'desktop'), (first, 'actual', 'actual')):
                    library = CloudLibrary(client, wid, mode, Path(directory) / label)
                    libraries.append(library)
                    stack.callback(library.close)
                    check(not any(library.dataset().values()))
            if not record(results, 'live.empty_isolated_modes', setup):
                return
            a, b, actual = libraries
            source = dict(title='[가상·테스트용] 자동 검증', authors='가상 팀', publication_date='2025-01',
                          source_type='기타', original_url='https://example.invalid/reviewlab-test', notes='논문 근거 사용 금지', purpose='테스트')
            saved = {}
            def crud():
                saved['id'] = Research(a).save(source)
                b.reload()
                check(b.list_sources() == a.list_sources())
                check(len(Research(b).search(query='자동 검증')) == 1)
                check(not actual.list_sources())
            if not record(results, 'live.source_create_search_cross_session_mode_isolation', crud):
                return
            def conflict():
                from reviewlab.cloud.client import CloudConflict
                Research(a).save(dict(source, title='[가상] 먼저 저장'), source_id=saved['id'])
                try:
                    Research(b).save(dict(source, title='[가상] 오래된 수정'), source_id=saved['id'])
                except CloudConflict:
                    pass
                else:
                    raise AssertionError()
                b.reload()
                check(b.list_sources()[0]['title'] == '[가상] 먼저 저장')
                Research(b).save(dict(source, title='[가상] 새로 읽고 재편집'), source_id=saved['id'])
                a.reload()
                check(a.list_sources() == b.list_sources())
            if not record(results, 'live.conflict_refresh_explicit_reedit', conflict):
                return
            anonymous = SupabaseClient(cfg.url, cfg.key)
            record(results, 'live.publishable_key_without_auth_denied', lambda: denied(anonymous, wid))
            record(results, 'live.outsider_read_denied', lambda: denied(outsider, wid))
            record(results, 'live.outsider_write_denied', lambda: denied(outsider, wid, write=True))
            record(results, 'live.reverse_workspace_read_denied', lambda: denied(first, owned[1][1]))
            record(results, 'live.direct_table_rls_and_no_direct_write', lambda: direct_rls(first, outsider, wid))
            def histories():
                from reviewlab.services.reviews import ReviewService
                from reviewlab.services.transfer import write_csv
                from reviewlab.services.review_transfer import save_run
                from reviewlab.analysis.reviews import statistics, context, patterns
                Research(a).add_tag('[가상] 자동 검증 태그')
                Research(a).save(dict(source, title='[가상] 새로 읽고 재편집'), [a.tags()[0]['id']], saved['id'])
                Research(a).verify(saved['id'], dict(status='검증 완료', checked_on='2025-01-01',
                    checked_by='가상 담당', evidence='허구 테스트 원문', memo='실제 검증 아님'), True)
                service = ReviewService(a)
                pid = service.create_project('[가상·테스트용] 통합 검증')
                service.upload(pid, write_csv(['content', 'rating'], [['가상 한글 리뷰', 5]]), '허구 수집', True)
                row = service.repo.rows(pid)[0]
                service.note(row['id'], '가상 연구자 메모')
                service.check(row['id'], '검토 완료', '가상 담당', '2025-01-01', '허구 검토 근거')
                service.repo.link_sources(pid, [saved['id']])
                rows = service.repo.rows(pid)
                tables, _ = statistics(rows)
                ctx = context(service.repo.project(pid), rows, rows, {})
                ctx['input_ids'] = [row['id']]
                save_run(service.repo, pid, tables, ctx, patterns(rows))
                saved['project'] = pid
                check(all(a.dataset()[t] for t in TABLES[:11]))
                b.reload()
                check(fingerprint(a.dataset()) == fingerprint(b.dataset()))
            if not record(results, 'live.all_tables_histories_and_analysis', histories):
                return
            def backup():
                from reviewlab.services.transfer import export_backup, restore, parse_backup
                from reviewlab.services import review_transfer
                from reviewlab.repositories.reviews import Reviews
                from reviewlab.repositories.library import Library
                check(fingerprint(read_local(a.snapshot(), 'example')) == fingerprint(a.dataset()))
                blob = export_backup(a)
                path = Path(directory) / 'sources.csv'
                path.write_bytes(blob)
                local = Library(Path(directory) / 'restore.sqlite3', 'example')
                prior_tags = {tag['name'] for tag in local.tags()}
                restore(local, path.read_bytes(), True)
                names, records = parse_backup(blob, 'example')
                restored_names, restored_records = parse_backup(export_backup(local), 'example')
                check(records == restored_records and set(restored_names) == set(names) | prior_tags)
                reviews = review_transfer.export_backup(Reviews(a), saved['project'])
                review_transfer.restore(local, reviews, True)
                check(review_transfer.export_backup(Reviews(local), saved['project']) == reviews)
            record(results, 'live.snapshot_and_csv_roundtrip', backup)
            def lost_ack():
                original = first.rpc
                def lose(name, params=None):
                    result = original(name, params)
                    if name == 'commit':
                        raise CloudUnavailable()  # 실제 서버 응답만 테스트 안에서 숨긴다.
                    return result
                first.rpc = lose
                try:
                    try:
                        Research(a).save(dict(source, title='[가상] 응답 유실', original_url='https://example.invalid/lost-ack'))
                    except CloudUnavailable:
                        pass
                    else:
                        raise AssertionError()
                finally:
                    first.rpc = original
                pending = a.pending_requests()
                check(len(pending) == 1)
                # 자동 전송 대신 테스트가 명시적으로 같은 ID 결과를 재확인한다.
                result = a.retry_pending(pending[0]['operation_id'])
                check(result['replayed'] is True and not a.pending_requests())
                b.reload()
                check(len(b.list_sources()) == 2)
            record(results, 'live.lost_ack_explicit_same_id_recovery', lost_ack)
            def delete():
                Research(a).delete(saved['id'], True)
                b.reload()
                check(all(row['id'] != saved['id'] for row in b.list_sources()))
            record(results, 'live.explicit_delete_cross_session', delete)
        finally:
            def cleanup():
                for client, owned_id in owned:
                    for mode in ('actual', 'example'):
                        state = client.rpc('read', {'p_workspace': owned_id, 'p_mode': mode})
                        if any(state['data'].values()):
                            client.rpc('commit', dict(p_workspace=owned_id, p_mode=mode,
                                p_expected_revision=state['revision'], p_operation=str(uuid4()), p_data={t: [] for t in TABLES}))
                        state = client.rpc('read', {'p_workspace': owned_id, 'p_mode': mode})
                        check(not any(state['data'].values()))
            record(results, 'live.cleanup_only_run_owned_workspace_data', cleanup)
            # workspace/receipt 메타데이터 삭제 RPC가 없으므로 유지한다. 관리자 수동 정리 대상.
            for client in clients:
                client.clear()


def write_reports(results, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    counts = {status: sum(r['status'] == status for r in results) for status in ('passed', 'failed', 'skipped')}
    payload = dict(schema_version=1, environment='separate_supabase_test_project', counts=counts, tests=results,
                   runner_file='scripts/test_supabase_live.py',
                   notes=['No production configuration read.', 'Run-owned workspace and receipt metadata retained; no admin delete API.'])
    (directory / 'supabase-live.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')
    suite = ET.Element('testsuite', name='supabase-live', tests=str(len(results)), failures=str(counts['failed']), skipped=str(counts['skipped']))
    for row in results:
        case = ET.SubElement(suite, 'testcase', classname='supabase_live', name=row['test'], time=str(row.get('seconds', 0)))
        if row['status'] != 'passed':
            ET.SubElement(case, 'failure' if row['status'] == 'failed' else 'skipped', message=row['category'])
        print(row['test'], row['status'], row['category'])
    ET.ElementTree(suite).write(directory / 'supabase-live.xml', encoding='utf-8', xml_declaration=True)
    return 2 if any(r['status'] == 'failed' and r['category'] != 'behavior_mismatch' for r in results) else 1 if counts['failed'] else 0


def main():
    results = []
    try:
        cfg = configuration(os.environ)
        if cfg is None:
            results.append(dict(test='live.configuration', status='skipped', category='no_dedicated_test_credentials'))
        else:
            exercise(cfg, results)
    except Exception as error:
        results.append(dict(test='live.runner', status='failed', category=classification(error)))
    recorded = {row['test'] for row in results}
    for name in TESTS:
        if 'live.' + name not in recorded:
            results.append(dict(test='live.' + name, status='skipped', category='not_executed'))
    return write_reports(results, ROOT / 'test-results')


if __name__ == '__main__':
    sys.exit(main())
