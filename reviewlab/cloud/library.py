"""기존 SQLite 저장소 API를 재사용하는 Supabase 단위 작업 어댑터.

SQLite는 사용자 세션 전용 작업 사본이다. 성공은 서버의 CAS 커밋 응답 뒤에만 반환한다.
"""
from contextlib import contextmanager, closing
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import tempfile
from uuid import uuid4

from reviewlab.cloud.client import CloudError, CloudConflict
from reviewlab.cloud.dataset import TABLES, read_dataset, insert_dataset, validate, read_local, fingerprint
from reviewlab.models import valid_id
from reviewlab.repositories.library import Library


LOGGER = logging.getLogger("reviewlab.cloud.preflight")

def private_file(path):
    os.chmod(path,0o600)


class CloudLibrary(Library):
    is_cloud=True

    def __init__(self,client,workspace_id,mode,root):
        valid_id(workspace_id)
        self.client,self.workspace_id=client,workspace_id
        self.project_key=hashlib.sha256(client.url.encode()).hexdigest()[:16]
        self.root=Path(root)/'cloud'/self.project_key/workspace_id/mode
        self.root.mkdir(parents=True,exist_ok=True)
        os.chmod(self.root,0o700)
        self._temporary=tempfile.TemporaryDirectory(prefix='reviewlab-cloud-')
        self._initializing=True;self._confirmed=False;self._broken=False
        super().__init__(Path(self._temporary.name)/'reviewlab.sqlite3',mode)
        self._initializing=False
        self.reload()

    @property
    def form_token(self):
        return f'{self.project_key}-{self.workspace_id}-{self.mode}-{self.client.user_id}-{self.revision}-{self.read_generation}'

    def _install(self,data):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('BEGIN IMMEDIATE')
            for table in reversed(TABLES):db.execute(f'DELETE FROM {table}')
            insert_dataset(db,data)
            if db.execute('PRAGMA foreign_key_check').fetchall():
                raise CloudError('공용 데이터의 참조 무결성 검사에 실패했습니다.')

    def dataset(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('BEGIN')
            return read_dataset(db)

    def reload(self):
        result=self.client.rpc('read',{'p_workspace':self.workspace_id,'p_mode':self.mode})
        if not isinstance(result,dict) or result.get('schema_version')!=1 or result.get('workspace_id')!=self.workspace_id or result.get('mode')!=self.mode or type(result.get('revision')) is not int or result['revision']<0:
            raise CloudError('공용 DB 스키마·작업공간·모드가 맞지 않습니다. SQL 마이그레이션을 확인하세요.')
        data=validate(result['data'],self.mode)
        self._install(data)
        self.revision=result['revision'];self.read_generation=getattr(self,'read_generation',0)+1;self._broken=False
        self.automatic_backup(only_if_needed=True)

    def remote_revision(self):
        result=self.client.rpc('status',{'p_workspace':self.workspace_id,'p_mode':self.mode})
        if not isinstance(result,dict) or result.get('schema_version')!=1 or type(result.get('revision')) is not int:
            raise CloudError('공용 DB 버전 응답이 올바르지 않습니다.')
        return result['revision']

    @contextmanager
    def connect(self):
        if self._initializing:
            with super().connect() as db:yield db
            return
        if self._broken:
            raise CloudError('저장 결과를 먼저 확인해야 합니다. 복구 요청을 확인하거나 공용 자료를 새로고침하세요.')
        changed=False
        with super().connect() as db:
            yield db
            changed=db.total_changes>0
        if changed:self._flush()

    def _copy(self,destination):
        destination=Path(destination);destination.parent.mkdir(parents=True,exist_ok=True)
        temporary=destination.with_name('.pending-'+str(uuid4())+'.sqlite3')
        try:
            with closing(sqlite3.connect(self.path)) as source, closing(sqlite3.connect(temporary)) as target, target:
                source.backup(target)
                if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise sqlite3.DatabaseError()
            private_file(temporary);os.replace(temporary,destination)
            return destination
        finally:
            # 이 요청이 만든 임시 파일만 정리하며, 정리 실패로 원래 예외를 가리지 않는다.
            try:temporary.unlink(missing_ok=True)
            except OSError:pass

    def snapshot(self):
        if not self._initializing and self._broken:
            raise CloudError('미확인 저장 요청을 먼저 처리하거나 새로고침하세요. 복구 사본은 별도로 보존되어 있습니다.')
        return self._copy(self.root/'backups'/f'before-change-{uuid4()}.sqlite3')

    def _metadata(self,path,metadata):
        temporary=path.with_name('.'+path.name+'.tmp')
        serialized=json.dumps(metadata,ensure_ascii=False,allow_nan=False)
        temporary.write_text(serialized,encoding='utf-8')
        private_file(temporary);os.replace(temporary,path)

    def _flush(self):
        operation=str(uuid4());self._broken=True
        pending=self.root/'pending'/f'{operation}.sqlite3'
        self._preflight('recovery_copy',operation,lambda:self._copy(pending))
        data=self._preflight('dataset_read',operation,self.dataset)
        digest=self._preflight('fingerprint',operation,lambda:fingerprint(data))
        info=dict(operation_id=operation,workspace_id=self.workspace_id,mode=self.mode,project_key=self.project_key,
                  expected_revision=self.revision,fingerprint=digest,state='unconfirmed')
        self._preflight('metadata_write',operation,lambda:self._metadata(pending.with_suffix('.json'),info))
        data=self._preflight('validation',operation,lambda:validate(data,self.mode))
        try:
            result=self._send(data,info)
        except CloudError as error:
            raise type(error)(f'{error} 복구 사본: {pending} · 요청 ID: {operation}. 연결 복구 후 데이터 관리의 미확인 요청에서 확인하세요.') from None
        self._broken=False;self.revision=result['revision']
        self._finish(pending,info)

    def _preflight(self,stage,operation,action):
        try:
            return action()
        except (OSError,sqlite3.Error,ValueError,TypeError,OverflowError,RecursionError) as error:
            if isinstance(error,OSError):
                code='FILE_WRITE'
                help_text='복구 파일 쓰기·교체에 실패했습니다. 폴더 권한·디스크 공간·파일 잠금을 확인하세요.'
            elif isinstance(error,sqlite3.Error):
                code='SQLITE_BACKUP' if stage=='recovery_copy' else 'SQLITE_READ'
                help_text='SQLite 복구 사본 생성·무결성 검사에 실패했습니다.' if stage=='recovery_copy' else '작업 사본을 읽지 못했습니다.'
            elif stage in ('fingerprint','metadata_write'):
                code='JSON_SERIALIZE'
                help_text='복구 정보의 JSON 직렬화에 실패했습니다. 입력값의 자료형·숫자를 확인하세요.'
            else:
                code='DATA_VALIDATION'
                help_text='데이터 유효성 검사에 실패했습니다. 필수값·형식·참조·자료 크기를 확인하세요.'
            # 예외 문자열/traceback/파일 경로/자료/계정/키/토큰은 기록하지 않는다.
            errno=getattr(error,'errno',None);winerror=getattr(error,'winerror',None)
            LOGGER.error('cloud_preflight_failed code=%s stage=%s operation=%s errno=%s winerror=%s',
                         code,stage,operation,errno if type(errno) is int else None,
                         winerror if type(winerror) is int else None)
            raise CloudError(f'서버 전송 전 중단 [{code}/{stage}]. {help_text} '
                             f'이 요청은 서버로 전송하지 않았습니다. 요청 ID: {operation}. '
                             '입력을 복사하고 기존 사본을 보존하세요. 자동 재전송하지 않습니다.') from None

    def _send(self,data,info):
        result=self.client.rpc('commit',{'p_workspace':self.workspace_id,'p_mode':self.mode,
            'p_expected_revision':info['expected_revision'],'p_operation':info['operation_id'],'p_data':data})
        if not isinstance(result,dict) or result.get('operation_id')!=info['operation_id'] or type(result.get('revision')) is not int or result['revision']<=info['expected_revision']:
            raise CloudError('서버 저장 응답을 확인하지 못했습니다. 같은 요청 ID로 결과를 확인하세요.')
        return result

    def _finish(self,pending,info):
        # pending_requests의 로컬 Path 객체는 저장 메타데이터에 넣지 않는다.
        info={k:v for k,v in info.items() if k!='path'}
        self._confirmed=True
        try:self.automatic_backup()
        finally:self._confirmed=False
        if not self.backup_warning:
            try:
                pending.unlink(missing_ok=True);pending.with_suffix('.json').unlink(missing_ok=True)
            except OSError:self.backup_warning='서버 저장은 완료됐지만 확인된 복구 사본 정리에 실패했습니다. 보존 폴더 권한을 확인하세요.'
        else:
            try:self._metadata(pending.with_suffix('.json'),dict(info,state='confirmed',confirmed_revision=self.revision))
            except OSError:pass

    def replace_dataset(self,data,expected_revision):
        if self._broken or expected_revision!=self.revision:
            raise CloudConflict('이전 미리보기 이후 공용 데이터가 변경되었습니다. 새로고침 후 이전 미리보기를 다시 만드세요.')
        data=validate(data,self.mode)
        self._install(data);self._flush()

    def pending_requests(self):
        result=[]
        for path in sorted((self.root/'pending').glob('*.json')):
            try:
                info=json.loads(path.read_text(encoding='utf-8'))
                if info['workspace_id']==self.workspace_id and info['mode']==self.mode and info['project_key']==self.project_key:
                    result.append(dict(info,path=path.with_suffix('.sqlite3')))
            except (OSError,ValueError,KeyError):continue
        return result

    def retry_pending(self,operation_id):
        info=next((p for p in self.pending_requests() if p['operation_id']==operation_id),None)
        if not info:raise CloudError('같은 작업공간·모드의 복구 요청을 찾지 못했습니다.')
        data=read_local(info['path'],self.mode)
        if fingerprint(data)!=info['fingerprint']:raise CloudError('복구 사본이 변경되었습니다. 원본을 보존하고 파일을 확인하세요.')
        result=self._send(data,info)
        self._broken=False
        try:self.reload()
        except CloudError:
            self._broken=True
            raise CloudError('서버 저장 응답은 확인했습니다. 최신 자료 조회에 실패했으므로 인터넷 복구 후 새로고침하세요. 복구 사본은 유지합니다.') from None
        self._finish(info['path'],info)
        return result

    def automatic_backups(self):
        return sorted((self.root/'backups'/'automatic').glob('auto-*.sqlite3'),reverse=True)

    def automatic_backup(self,only_if_needed=False):
        if self._initializing or self._broken or (not self._confirmed and not only_if_needed):return None
        directory=self.root/'backups'/'automatic'
        day=datetime.now(timezone.utc).date().isoformat()
        target=directory/f'auto-{day}.sqlite3'
        try:
            metadata=target.with_suffix('.json')
            if only_if_needed and target.exists() and metadata.exists() and json.loads(metadata.read_text())['revision']==self.revision:
                return target
            self._copy(target)
            self._metadata(metadata,dict(revision=self.revision,workspace_id=self.workspace_id,mode=self.mode))
            for old in self.automatic_backups()[30:]:
                old.unlink();old.with_suffix('.json').unlink(missing_ok=True)
            self.backup_warning=None
            return target
        except (OSError,ValueError,KeyError,sqlite3.Error):
            self.backup_warning='공용'+' DB 저장과 별개로 이 기기의 자동 백업에 실패했습니다. 디스크 공간·폴더 권한을 확인하고 수동 전체 백업을 보관하세요.'
            return None

    def close(self):
        self._temporary.cleanup()
