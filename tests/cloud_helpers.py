"""실제 Supabase가 아닌 명시적 테스트 대역."""
from copy import deepcopy
from threading import Lock
from uuid import uuid4
from reviewlab.cloud.dataset import TABLES, canonical, validate
from reviewlab.cloud.client import CloudError, CloudConflict, CloudUnavailable


class MemoryServer:
    def __init__(self):
        self.workspace=str(uuid4());self.members=set();self.states={};self.receipts={};self.lock=Lock()

    def client(self,user=None):
        result=MemoryClient(self,user or str(uuid4()))
        self.members.add(result.user_id)
        return result


class MemoryClient:
    url='https://cloud-test.example.invalid'
    key='sb_publishable_test_not_a_real_key'
    email='fictional@example.invalid'
    access_token='test-only-not-a-token'

    def __init__(self,server,user):
        self.server=server;self.user_id=user;self.offline=False;self.lose_response=False;self.calls=[]

    def rpc(self,name,params=None):
        p=params or {};self.calls.append((name,deepcopy(p)))
        if self.offline:raise CloudUnavailable('테스트 대역: 연결 끊김')
        s=self.server
        if self.user_id not in s.members:raise CloudError('테스트 대역: 권한 없음')
        if name=='workspaces':return dict(schema_version=3,workspaces=[dict(id=s.workspace,name='테스트 작업공간')])
        if name=='create_workspace':raise CloudError('테스트 대역은 작업공간 생성 미지원')
        if p.get('p_workspace')!=s.workspace:raise CloudError('테스트 대역: 다른 작업공간')
        mode=p['p_mode'];key=(s.workspace,mode)
        if mode not in ('actual','example'):raise CloudError('테스트 대역: 잘못된 모드')
        with s.lock:
            state=s.states.setdefault(key,dict(revision=0,data={t:[] for t in TABLES}))
            if name=='status':return dict(schema_version=3,revision=state['revision'])
            if name=='read':return dict(schema_version=3,workspace_id=s.workspace,mode=mode,**deepcopy(state))
            if name=='commit':
                op=(key,p['p_operation']);payload=canonical(p['p_data'])
                if op in s.receipts:
                    digest,revision=s.receipts[op]
                    if digest!=payload:raise CloudError('테스트 대역: 요청 내용 불일치')
                    return dict(operation_id=p['p_operation'],revision=revision,replayed=True)
                if state['revision']!=p['p_expected_revision']:raise CloudConflict('테스트 대역: 버전 충돌')
                data=validate(p['p_data'],mode)
                state.update(revision=state['revision']+1,data=deepcopy(data))
                s.receipts[op]=(payload,state['revision'])
                if self.lose_response:
                    self.lose_response=False;raise CloudUnavailable('테스트 대역: 커밋 후 응답 소실')
                return dict(operation_id=p['p_operation'],revision=state['revision'],replayed=False)
        raise AssertionError(name)
