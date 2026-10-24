"""복구 파일 핸들 수명·실패 분류. Windows에서는 실제 파일 교체도 수행한다."""
from pathlib import Path
import logging
import sqlite3

import pytest

from cloud_helpers import MemoryServer
from reviewlab.cloud.client import CloudError
from reviewlab.cloud.library import CloudLibrary
from reviewlab.cloud.dataset import read_local, validate
from reviewlab.services.research import Research


@pytest.fixture
def example_cloud(tmp_path):
    server=MemoryServer();client=server.client()
    library=CloudLibrary(client,server.workspace,'example',tmp_path/'한글 사용자 폴더'/'Review Lab')
    yield library,client
    library.close()


def track_connections(monkeypatch):
    connections=[];original=sqlite3.connect
    class Tracked(sqlite3.Connection):
        def close(self):
            self.closed=True
            return super().close()
    def connect(path,*args,**kwargs):
        kwargs['factory']=Tracked
        connection=original(path,*args,**kwargs)
        connection.closed=False;connection.filename=str(path)
        connections.append(connection)
        return connection
    monkeypatch.setattr(sqlite3,'connect',connect)
    return connections


def test_example_registration_closes_handles_before_windows_style_replace(example_cloud,source,monkeypatch):
    library,client=example_cloud
    connections=track_connections(monkeypatch)
    original_replace=__import__('os').replace
    replacements=[]
    def windows_replace(src,dst):
        # Linux에서도 Windows의 열린 SQLite 파일 교체 금지 조건을 강제한다.
        if any(not c.closed and c.filename==str(src) for c in connections):
            error=PermissionError(13,'sensitive-file-path must not appear in logs')
            error.winerror=32
            raise error
        replacements.append(Path(dst))
        return original_replace(src,dst)
    monkeypatch.setattr('reviewlab.cloud.library.os.replace',windows_replace)
    sid=Research(library).save(dict(source,title='[가상·테스트용] 복구 검사'))
    assert library.list_sources()[0]['id']==sid
    assert all(c.closed for c in connections)
    assert replacements and library.automatic_backups()
    assert read_local(library.automatic_backups()[0],'example')['sources'][0]['id']==sid
    assert not library.pending_requests()
    library.close()  # Windows에서는 열려 있는 세션 DB가 있으면 여기서도 실패한다.


@pytest.mark.parametrize('stage,error,code',[
    ('recovery_copy',sqlite3.OperationalError('password=secret private@example.invalid'),'SQLITE_BACKUP'),
    ('recovery_copy',PermissionError(13,'C:/Users/PRIVATE/token-secret'),'FILE_WRITE'),
    ('dataset_read',sqlite3.DatabaseError('private body'),'SQLITE_READ'),
    ('fingerprint',TypeError('API_KEY=secret personal text'),'JSON_SERIALIZE'),
    ('metadata_write',TypeError('refresh-token-secret'),'JSON_SERIALIZE'),
    ('metadata_write',OSError(28,'personal text'),'FILE_WRITE'),
    ('validation',ValueError('name@example.invalid secret content'),'DATA_VALIDATION'),
])
def test_each_failure_has_safe_stage_code_no_send_or_automatic_retry(example_cloud,source,monkeypatch,caplog,stage,error,code):
    library,client=example_cloud
    before=library.automatic_backups()[0].read_bytes()
    commits=sum(name=='commit' for name,_ in client.calls)
    def fail(*args,**kwargs):raise error
    target={'recovery_copy':'_copy','dataset_read':'dataset','fingerprint':'fingerprint',
            'metadata_write':'_metadata','validation':'validate'}[stage]
    with monkeypatch.context() as patch:
        if stage in ('fingerprint','validation'):
            patch.setattr('reviewlab.cloud.library.'+target,fail)
        else:patch.setattr(library,target,fail)
        with caplog.at_level(logging.ERROR,logger='reviewlab.cloud.preflight'):
            with pytest.raises(CloudError) as raised:Research(library).save(source)
    assert f'[{code}/{stage}]' in str(raised.value)
    assert f'code={code} stage={stage}' in caplog.text
    for forbidden in ('secret','PRIVATE','private@example','name@example','personal text','C:/Users'):
        assert forbidden not in caplog.text and forbidden not in str(raised.value)
    assert not any(r.exc_info for r in caplog.records)
    assert sum(name=='commit' for name,_ in client.calls)==commits
    assert library.automatic_backup(only_if_needed=True) is None
    assert library.automatic_backups()[0].read_bytes()==before
    assert client.server.states[(client.server.workspace,'example')]['data']['sources']==[]
    library.reload()
    assert sum(name=='commit' for name,_ in client.calls)==commits


def test_real_validation_error_preserves_candidate_and_never_sends(example_cloud,source,caplog):
    library,client=example_cloud
    with caplog.at_level(logging.ERROR,logger='reviewlab.cloud.preflight'):
        with pytest.raises(CloudError,match='DATA_VALIDATION'):
            with library.connect() as db:
                # UI 검증을 우회한 손상 데이터를 가정한다. 본문/주소는 로그에 없어야 한다.
                library.insert_source(db,dict(source,id='bad-sensitive-id',created_at='invalid',updated_at='invalid'))
    assert 'bad-sensitive-id' not in caplog.text
    assert not any(name=='commit' for name,_ in client.calls)
    assert list((library.root/'pending').glob('*.sqlite3'))
    assert list((library.root/'pending').glob('*.json'))


def test_dataset_validation_and_read_close_every_connection(example_cloud,source,monkeypatch):
    library,_=example_cloud
    Research(library).save(source)
    path=library.snapshot()
    connections=track_connections(monkeypatch)
    for _ in range(3):
        data=read_local(path,'example')
        validate(data,'example')
        library.dataset()
    assert connections and all(c.closed for c in connections)
    # Windows의 rename/unlink 경로도 실제 실행한다. 테스트용 사본만 사용한다.
    renamed=path.with_name('한글 백업 사본.sqlite3')
    path.rename(renamed)
    assert renamed.exists()
