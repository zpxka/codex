"""클라우드 장애/충돌/전체 백업의 빈틈을 보강한다. 네트워크·운영 DB 없음."""
from copy import deepcopy

import pytest
import requests

from cloud_helpers import MemoryServer
from reviewlab.cloud.client import SupabaseClient, CloudError, CloudConflict, CloudUnavailable
from reviewlab.cloud.dataset import fingerprint, read_local, TABLES
from reviewlab.cloud.library import CloudLibrary
from reviewlab.repositories.library import Library
from reviewlab.repositories.reviews import Reviews
from reviewlab.services.research import Research
from reviewlab.services.reviews import ReviewService
from reviewlab.services import transfer, review_transfer
from reviewlab.analysis.reviews import statistics, context, patterns


@pytest.fixture
def pair(tmp_path):
    server = MemoryServer()
    first = CloudLibrary(server.client(), server.workspace, 'example', tmp_path / '노트북')
    second = CloudLibrary(server.client(), server.workspace, 'example', tmp_path / '데스크탑')
    yield first, second, server
    first.close()
    second.close()


def test_conflict_refresh_and_explicit_new_edit_succeeds(pair, source):
    first, second, _ = pair
    sid = Research(first).save(source)
    second.reload()
    Research(first).save(dict(source, title='[가상] 먼저 저장'), source_id=sid)
    with pytest.raises(CloudConflict):
        Research(second).save(dict(source, title='[가상] 오래된 수정'), source_id=sid)
    failed_id = second.pending_requests()[0]['operation_id']
    second.reload()
    assert second.list_sources()[0]['title'] == '[가상] 먼저 저장'
    Research(second).save(dict(source, title='[가상] 새로 읽고 명시적 수정'), source_id=sid)
    first.reload()
    assert first.list_sources() == second.list_sources()
    assert first.list_sources()[0]['title'] == '[가상] 새로 읽고 명시적 수정'
    # 과거 충돌 사본은 새 저장이 성공해도 임의로 지우거나 재전송하지 않는다.
    assert second.pending_requests()[0]['operation_id'] == failed_id


@pytest.mark.parametrize('after_commit', [False, True])
def test_delayed_response_uses_one_request_and_explicit_same_id_recovery(pair, source, after_commit):
    library, observer, server = pair
    client = library.client
    original = client.rpc
    attempts = []

    def delay(name, params=None):
        if name == 'commit':
            attempts.append(deepcopy(params))
            if after_commit:
                original(name, params)
            # 즉시 주입해 30초 실대기 없이 read timeout과 동일한 실패 경로 검증.
            raise CloudUnavailable('테스트 응답 지연')
        return original(name, params)

    client.rpc = delay
    before_backup = library.automatic_backups()[0].read_bytes()
    with pytest.raises(CloudUnavailable):
        Research(library).save(source)
    assert len(attempts) == 1
    pending = library.pending_requests()[0]
    assert pending['state'] == 'unconfirmed'
    assert library.automatic_backups()[0].read_bytes() == before_backup
    observer.reload()
    assert len(observer.list_sources()) == int(after_commit)
    library.reload()
    assert len(attempts) == 1  # 조회/새로고침은 자동 재전송이 아니다.
    client.rpc = original
    result = library.retry_pending(pending['operation_id'])  # 테스트의 명시적 복구
    assert result['replayed'] == after_commit
    observer.reload()
    assert len(observer.list_sources()) == 1
    assert not library.pending_requests()
    assert len(server.receipts) == 1
    assert attempts[0]['p_operation'] == pending['operation_id']


@pytest.mark.parametrize('exception', [requests.ReadTimeout, requests.ConnectionError])
def test_http_transport_never_retries_or_logs_sensitive_details(exception, caplog):
    import time
    class BrokenHTTP:
        calls = 0
        def request(self, *args, **kwargs):
            self.calls += 1
            assert kwargs['timeout'] == (5, 30)
            raise exception('password=PRIVATE token=PRIVATE personal@example.invalid')
    http = BrokenHTTP()
    client = SupabaseClient('https://test.example.invalid', 'sb_publishable_not_real', http)
    client.access_token = 'PRIVATE'
    client.refresh_token = 'PRIVATE_REFRESH'
    client.expires_at = time.time() + 3600
    with pytest.raises(CloudUnavailable) as raised:
        client.rpc('commit', {'p_operation': 'test-request'})
    assert http.calls == 1
    assert 'PRIVATE' not in str(raised.value) + caplog.text
    assert 'personal@' not in str(raised.value) + caplog.text


def test_cloud_full_backup_restore_integrity_and_corruption_no_overwrite(pair, source, event, tmp_path):
    library, observer, _ = pair
    research = Research(library)
    research.add_tag('[가상] 백업 태그')
    sid = research.save(source, [library.tags()[0]['id']])
    research.verify(sid, dict(event, status='원문 확인 중'))
    research.verify(sid, event, True)
    reviews = ReviewService(library)
    pid = reviews.create_project('[가상·테스트용] 전체 백업')
    reviews.upload(pid, transfer.write_csv(['content', 'rating'], [['가상 한글 리뷰', 5]]), '가상 수집', True)
    row = reviews.repo.rows(pid)[0]
    reviews.note(row['id'], '가상 연구자 메모')
    reviews.check(row['id'], '검토 완료', '가상 담당', '2025-02-01', '허구 검토 근거')
    reviews.repo.link_sources(pid, [sid])
    rows = reviews.repo.rows(pid)
    tables, _ = statistics(rows)
    ctx = context(reviews.repo.project(pid), rows, rows, {})
    ctx['input_ids'] = [row['id']]
    review_transfer.save_run(reviews.repo, pid, tables, ctx, patterns(rows))
    original = library.dataset()
    assert all(original[t] for t in TABLES)  # 11개 테이블 모두 실제로 검사
    snapshot = library.snapshot()
    assert fingerprint(read_local(snapshot, 'example')) == fingerprint(original)
    source_csv = transfer.export_backup(library)
    review_csv = review_transfer.export_backup(reviews.repo, pid)
    for name, blob in [('sources.csv', source_csv), ('reviews.csv', review_csv)]:
        path = tmp_path / name
        path.write_bytes(blob)
        assert path.read_bytes().startswith(b'\xef\xbb\xbf')
    restored = Library(tmp_path / '복원.sqlite3', 'example')
    prior_tags = {tag['name'] for tag in restored.tags()}
    transfer.restore(restored, (tmp_path / 'sources.csv').read_bytes(), True)
    review_transfer.restore(restored, (tmp_path / 'reviews.csv').read_bytes(), True)
    # 자료 CSV는 태그 UUID를 이름으로 재연결한다. 의미/검증 이력과 리뷰 전체 CSV로 비교한다.
    original_names, original_sources = transfer.parse_backup(source_csv, 'example')
    restored_names, restored_sources = transfer.parse_backup(transfer.export_backup(restored), 'example')
    assert restored_sources == original_sources
    assert set(restored_names) == set(original_names) | prior_tags
    assert review_transfer.export_backup(Reviews(restored), pid) == review_csv
    assert len(restored.list_sources()[0]['events']) == 2
    observer.reload()
    assert fingerprint(observer.dataset()) == fingerprint(original)
    for restore_function in (transfer.restore, review_transfer.restore):
        with pytest.raises(ValueError):
            restore_function(library, b'format,mode,record_type,payload\ncorrupted', True)
        assert fingerprint(library.dataset()) == fingerprint(original)
    assert transfer.restore(library, source_csv, True)['added'] == 0
    assert review_transfer.restore(library, review_csv, True)['duplicate_project']
    assert fingerprint(library.dataset()) == fingerprint(original)


def test_tampered_pending_copy_is_rejected_without_transmission(pair, source):
    library, _, _ = pair
    client = library.client
    client.offline = True
    with pytest.raises(CloudUnavailable):
        Research(library).save(source)
    pending = library.pending_requests()[0]
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect(pending['path'])) as db, db:
        db.execute('UPDATE sources SET title=?', ('[가상] 변조된 사본',))
    client.offline = False
    count = len(client.calls)
    with pytest.raises(CloudError, match='변경'):
        library.retry_pending(pending['operation_id'])
    assert len(client.calls) == count
    assert pending['path'].exists()
