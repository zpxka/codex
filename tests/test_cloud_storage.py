import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

import pytest

from reviewlab.cloud.client import CloudError, CloudConflict, CloudUnavailable
from reviewlab.cloud.dataset import TABLES, read_local, fingerprint
from reviewlab.cloud.library import CloudLibrary
from reviewlab.cloud.migration import prepare,execute,plan
from reviewlab.repositories.library import Library
from reviewlab.repositories.reviews import Reviews
from reviewlab.services.research import Research
from reviewlab.services.reviews import ReviewService
from reviewlab.services import review_transfer
from reviewlab.services.source_import import import_csv
from reviewlab.services.transfer import export_backup,restore,write_csv
from cloud_helpers import MemoryServer


@pytest.fixture
def cloud(tmp_path):
    server=MemoryServer();client=server.client()
    library=CloudLibrary(client,server.workspace,'actual',tmp_path/'cloud-data')
    yield library,server,client
    library.close()


def test_existing_source_crud_bulk_tags_verification_and_backup_on_cloud(cloud,source,event,tmp_path):
    library,server,client=cloud;service=Research(library)
    service.add_tag('공용 태그');tag=library.tags()[0]
    sid=service.save(source,[tag['id']])
    service.verify(sid,event,True)
    assert service.search(query='허구',tag='공용 태그',status='검증 완료')[0]['id']==sid
    service.save(dict(source,title='[허구] 수정됨'),[tag['id']],sid)
    assert library.list_sources()[0]['events'][0]['checked_by']==event['checked_by']
    payload=write_csv(['제목','자료 유형','원문 URL','태그'],[['[허구] CSV 공용','기타','https://example.com/cloud-csv','새 공용 태그']])
    assert import_csv(library,payload,confirmed_mode='actual')['added']==1
    assert import_csv(library,payload,confirmed_mode='actual')['skipped']==1
    reader=CloudLibrary(server.client(),server.workspace,'actual',tmp_path/'second')
    assert reader.list_sources()==library.list_sources()
    assert export_backup(reader)==export_backup(library)
    assert library.automatic_backups()
    with sqlite3.connect(library.automatic_backups()[0]) as db:
        assert db.execute('SELECT count(*) FROM sources').fetchone()[0]==2
    backup=library.snapshot();assert read_local(backup,'actual')==library.dataset()
    service.delete(sid,True)
    reader.reload();assert len(reader.list_sources())==1
    assert restore(reader,export_backup(library),True)['added']==0
    reader.close()


def test_review_upload_analysis_notes_checks_and_csv_restore_cloud(cloud,tmp_path,source):
    library,server,_=cloud
    sid=Research(library).save(source)
    service=ReviewService(library);pid=service.create_project('공용 허구 프로젝트')
    payload=write_csv(['리뷰 고유 ID','리뷰 내용','별점'],[['FAKE-1','허구 테스트 리뷰',5]])
    assert service.upload(pid,payload,'허구 수집 테스트',True)['added']==1
    row=service.repo.rows(pid)[0]
    service.note(row['id'],'공용 메모')
    service.check(row['id'],'검토 완료','테스트 담당','2025-02-01','허구 근거')
    service.repo.link_sources(pid,[sid])
    from reviewlab.analysis.reviews import statistics,context,patterns
    rows=service.repo.rows(pid);tables,_=statistics(rows)
    ctx=context(service.repo.project(pid),rows,rows,{})
    ctx['input_ids']=[row['id']]
    review_transfer.save_run(service.repo,pid,tables,ctx,patterns(rows))
    reader=CloudLibrary(server.client(),server.workspace,'actual',tmp_path/'reader')
    assert Reviews(reader).rows(pid)==service.repo.rows(pid)
    backup=review_transfer.export_backup(service.repo,pid)
    assert review_transfer.export_backup(Reviews(reader),pid)==backup
    target=Library(tmp_path/'restored.db','actual')
    restore(target,export_backup(library),True)
    review_transfer.restore(target,backup,True)
    assert Reviews(target).rows(pid)==service.repo.rows(pid)
    assert read_local(library.snapshot(),'actual')['review_analysis_runs']
    reader.close()


def test_two_clients_conflict_never_silently_overwrites(cloud,source,tmp_path):
    first,server,_=cloud
    Research(first).save(source)
    second=CloudLibrary(server.client(),server.workspace,'actual',tmp_path/'second')
    sid=first.list_sources()[0]['id']
    Research(first).save(dict(source,title='노트북 수정'),source_id=sid)
    with pytest.raises(CloudConflict):
        Research(second).save(dict(source,title='데스크탑 오래된 수정'),source_id=sid)
    pending=second.pending_requests()
    assert len(pending)==1
    assert read_local(pending[0]['path'],'actual')['sources'][0]['title']=='데스크탑 오래된 수정'
    with pytest.raises(CloudError):second.list_sources()
    second.reload();assert second.list_sources()[0]['title']=='노트북 수정'
    with pytest.raises(CloudConflict):second.retry_pending(pending[0]['operation_id'])
    assert first.list_sources()[0]['title']=='노트북 수정'
    second.close()


def test_lost_response_idempotent_retry_does_not_revert_later_changes(cloud,source,tmp_path):
    library,server,client=cloud
    client.lose_response=True
    with pytest.raises(CloudUnavailable):Research(library).save(source)
    item=library.pending_requests()[0]
    other=CloudLibrary(server.client(),server.workspace,'actual',tmp_path/'other')
    Research(other).save(dict(source,title='다른 기기의 새 자료',original_url='https://example.com/later'))
    assert len(other.list_sources())==2
    library.retry_pending(item['operation_id'])
    assert len(library.list_sources())==2
    assert library.revision==other.revision
    assert not library.pending_requests()
    other.close()


def test_network_failure_keeps_pending_no_local_mode_fallback(cloud,source,tmp_path):
    library,server,client=cloud
    confirmed_backup=library.automatic_backups()[0].read_bytes()
    client.offline=True
    with pytest.raises(CloudUnavailable):Research(library).save(source)
    item=library.pending_requests()[0]
    assert item['path'].exists()
    assert library.automatic_backup(only_if_needed=True) is None
    assert library.automatic_backups()[0].read_bytes()==confirmed_backup
    with pytest.raises(CloudError):library.snapshot()
    assert not list((tmp_path/'cloud-data'/'actual').glob('*.sqlite3'))
    metadata=item['path'].with_suffix('.json').read_text()
    assert client.key not in metadata and client.access_token not in metadata
    client.offline=False
    library.retry_pending(item['operation_id'])
    assert len(library.list_sources())==1


def test_modes_and_membership_are_separate(cloud,source,tmp_path):
    library,server,client=cloud
    Research(library).save(source)
    example=CloudLibrary(client,server.workspace,'example',tmp_path/'example')
    assert not example.list_sources()
    Research(example).save(dict(source,title='가상·테스트용'))
    assert library.list_sources()[0]['title']==source['title']
    server.members.remove(client.user_id)
    with pytest.raises(CloudError):library.reload()
    example.close()


def test_migration_preserves_all_histories_original_and_backup(cloud,library,service,source,event):
    target,_,_=cloud
    sid=service.save(source,[library.tags()[0]['id']]);service.verify(sid,event,True)
    reviews=ReviewService(library);pid=reviews.create_project('허구 원본 프로젝트')
    reviews.upload(pid,write_csv(['content'],[['허구 리뷰']]),'테스트 수집',True)
    row=reviews.repo.rows(pid)[0]
    reviews.note(row['id'],'원본 메모');reviews.check(row['id'],'검토 완료','담당','2025-02-01','검토 근거')
    reviews.repo.link_sources(pid,[sid])
    from reviewlab.analysis.reviews import statistics,context,patterns
    rows=reviews.repo.rows(pid);tables,_=statistics(rows)
    ctx=context(reviews.repo.project(pid),rows,rows,{});ctx['input_ids']=[row['id']]
    review_transfer.save_run(reviews.repo,pid,tables,ctx,patterns(rows))
    before=hashlib.sha256(library.path.read_bytes()).hexdigest()
    preview=prepare(target,library.path)
    assert preview['backup_path'].exists() and preview['added']['sources']==1
    assert not target.list_sources()
    execute(target,preview,True,True,True)
    assert fingerprint(read_local(library.path,'actual'))==fingerprint(target.dataset())
    assert hashlib.sha256(library.path.read_bytes()).hexdigest()==before
    assert export_backup(target)==export_backup(library)
    assert review_transfer.export_backup(Reviews(target),pid)==review_transfer.export_backup(reviews.repo,pid)
    again=prepare(target,library.path)
    assert not any(again['added'].values()) and not again['errors']


def test_migration_duplicates_preserved_only_after_explicit_review(cloud,library,service,source,event):
    target,_,_=cloud
    service.save(source);Research(target).save(source)
    preview=prepare(target,library.path)
    assert preview['duplicates']
    with pytest.raises(CloudError,match='중복'):execute(target,preview,True)
    assert len(target.list_sources())==1
    execute(target,preview,True,True,True)
    assert len(target.list_sources())==2
    assert len({r['id'] for r in target.list_sources()})==2


def test_tag_name_remapping_requires_confirmation_and_preserves_links(cloud,library,service,source):
    target,_,_=cloud
    tag=library.tags()[0];service.save(source,[tag['id']])
    Research(target).add_tag(tag['name'])
    target_tag=target.tags()[0]
    preview=prepare(target,library.path)
    assert preview['tag_reuse']
    with pytest.raises(CloudError,match='태그'):execute(target,preview,True)
    execute(target,preview,True,True,True)
    assert target.list_sources()[0]['tags']==[target_tag]


def test_same_id_changed_history_is_blocked_and_stale_preview_rejected(cloud,library,service,source,event):
    target,_,_=cloud
    sid=service.save(source)
    execute(target,prepare(target,library.path),True,True,True)
    service.verify(sid,event,True)
    preview=prepare(target,library.path)
    assert preview['errors']
    with pytest.raises(CloudError):execute(target,preview,True,True,True)
    assert target.list_sources()[0]['events']==[]
    fresh=prepare(target,library.path)
    fresh['errors']=[] # 테스트 대역이 잘못된 UI 승인을 보내도 대상 변경을 검사한다.
    Research(target).add_tag('이전 미리보기 후 추가')
    with pytest.raises(CloudConflict):execute(target,fresh,True,True,True)


def test_cloud_backup_warning_does_not_report_server_commit_as_failure(cloud,source,monkeypatch):
    library,server,_=cloud
    original=library.automatic_backup
    def fail(*args,**kwargs):
        library.backup_warning='테스트 백업 실패';return None
    monkeypatch.setattr(library,'automatic_backup',fail)
    Research(library).save(source)
    assert library.list_sources()[0]['title']==source['title']
    assert library.pending_requests()[0]['state']=='confirmed'
    # 백업 장애가 계속돼도 재확인한 서버 저장을 실패로 보고하지 않는다.
    library.retry_pending(library.pending_requests()[0]['operation_id'])
    assert library.pending_requests()[0]['state']=='confirmed'
    monkeypatch.setattr(library,'automatic_backup',original)
    library.retry_pending(library.pending_requests()[0]['operation_id'])
    assert not library.pending_requests()


def test_failed_migration_keeps_original_and_remote_dataset(cloud,library,service,source):
    target,server,client=cloud
    service.save(source)
    original=library.path.read_bytes()
    preview=prepare(target,library.path)
    client.offline=True
    with pytest.raises(CloudUnavailable):execute(target,preview,True,True,True)
    assert library.path.read_bytes()==original
    assert preview['backup_path'].exists()
    assert target.pending_requests()
    client.offline=False;target.reload()
    assert not target.list_sources()


def test_same_verification_ids_with_reordered_history_is_conflict(cloud,library,service,source,event):
    target,_,_=cloud
    sid=service.save(source)
    service.verify(sid,event,True)
    service.verify(sid,dict(event,status='원문 확인 중'))
    execute(target,prepare(target,library.path),True,True,True)
    incoming=read_local(library.path,'actual')
    a,b=incoming['verification_events']
    a['sequence'],b['sequence']=b['sequence'],a['sequence']
    assert plan(incoming,target.dataset(),'actual')['errors']


def test_wrong_mode_local_db_and_future_cloud_version_fail_closed(cloud,library,source,tmp_path):
    target,server,client=cloud
    with pytest.raises(ValueError):read_local(library.path,'example')
    original=client.rpc
    def future(name,params=None):
        response=original(name,params)
        if name=='read':response['schema_version']=2
        return response
    client.rpc=future
    with pytest.raises(CloudError,match='스키마'):
        CloudLibrary(client,server.workspace,'actual',tmp_path/'future')
    assert not list((tmp_path/'future').glob('actual/reviewlab.sqlite3'))
