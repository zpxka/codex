"""Supabase Auth는 실제 접속 대신 HTTP 응답 대역으로 검증한다."""
import base64
import json
import time
from uuid import uuid4

import pytest
import requests

from reviewlab.cloud.client import SupabaseClient,CloudError,CloudConflict,CloudUnavailable


class Response:
    def __init__(self,status,data):self.status_code=status;self.data=data;self.content=b'json'
    def json(self):return self.data


class HTTP:
    def __init__(self,responses):self.responses=list(responses);self.calls=[]
    def request(self,*args,**kwargs):
        self.calls.append((args,kwargs))
        item=self.responses.pop(0)
        if isinstance(item,Exception):raise item
        return item


def token(access='access-test',refresh='refresh-test'):
    return dict(access_token=access,refresh_token=refresh,expires_in=3600)


def test_login_verified_user_refresh_rpc_and_logout():
    uid=str(uuid4())
    http=HTTP([Response(200,token()),Response(200,dict(id=uid,email='fake@example.invalid')),
               Response(200,token('new-access','new-refresh')),Response(200,dict(workspaces=[])),Response(200,{})])
    client=SupabaseClient('https://example.invalid','sb_publishable_fake',http)
    client.sign_in('fake@example.invalid','test-password-only')
    assert client.user_id==uid and client.refresh_token=='refresh-test'
    client.expires_at=time.time()-1
    client.rpc('workspaces')
    assert http.calls[2][1]['json']==dict(refresh_token='refresh-test')
    assert http.calls[3][1]['headers']['Authorization']=='Bearer new-access'
    assert http.calls[0][1]['timeout']==(5,30) and not http.calls[0][1]['allow_redirects']
    client.logout();assert client.access_token is None and client.user_id is None


@pytest.mark.parametrize('url,key',[('http://insecure.example','sb_publishable_fake'),
    ('https://user:secret@example.invalid','key'),('https://example.invalid?secret=x','key'),
    ('https://example.invalid','sb_secret_do_not_use')])
def test_reject_insecure_configuration_or_privileged_key(url,key):
    with pytest.raises(CloudError):SupabaseClient(url,key)


def test_legacy_service_role_key_is_rejected():
    encoded=base64.urlsafe_b64encode(json.dumps({'role':'service_role'}).encode()).decode().rstrip('=')
    with pytest.raises(CloudError):SupabaseClient('https://example.invalid','head.'+encoded+'.sig')


@pytest.mark.parametrize('status,body,exception',[(401,{'message':'secret-raw-error'},CloudError),
    (403,{},CloudError),(400,{'code':'40001','message':'RL_CONFLICT raw-secret'},CloudConflict),
    (500,{'message':'postgres://password-should-not-leak'},CloudError)])
def test_errors_are_sanitized(status,body,exception):
    client=SupabaseClient('https://example.invalid','sb_publishable_fake',HTTP([Response(status,body)]))
    client.access_token='sensitive-token';client.refresh_token='sensitive-refresh';client.expires_at=time.time()+500
    with pytest.raises(exception) as raised:client.rpc('read')
    assert 'sensitive' not in str(raised.value) and 'secret' not in str(raised.value) and 'postgres://' not in str(raised.value)


def test_timeout_unauthenticated_and_refresh_failure():
    client=SupabaseClient('https://example.invalid','sb_publishable_fake',HTTP([requests.Timeout('token=private')]))
    with pytest.raises(CloudError):client.rpc('read')
    client.access_token='private';client.refresh_token='private';client.expires_at=time.time()+500
    with pytest.raises(CloudUnavailable) as raised:client.rpc('read')
    assert 'private' not in str(raised.value)
    client.http=HTTP([Response(401,{'message':'bad refresh'})]);client.expires_at=0
    with pytest.raises(CloudError):client.rpc('read')
    assert client.access_token is None
