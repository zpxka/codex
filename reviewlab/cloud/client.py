"""Supabase Auth/JSON RPC의 최소 클라이언트. 키·토큰·원시 오류를 출력하지 않는다."""
import base64
import json
import time
from urllib.parse import urlsplit
from uuid import UUID

import requests


class CloudError(ValueError):
    pass


class CloudConflict(CloudError):
    pass


class CloudUnavailable(CloudError):
    pass


def settings(url, key):
    try:
        parsed=urlsplit(url)
        valid=(parsed.scheme=='https' and parsed.hostname and parsed.path in ('','/') and not parsed.query
               and not parsed.fragment and not parsed.username and not parsed.password and not any(c.isspace() for c in url))
    except (ValueError,TypeError):
        valid=False
    if not valid or not isinstance(key,str) or not key:
        raise CloudError('Supabase URL(HTTPS)과 publishable/anon key를 secrets 또는 환경변수에 설정하세요.')
    if key.startswith(('sb_secret_', 'service_role')):
        raise CloudError('서비스 키는 앱에 사용할 수 없습니다. publishable/anon key를 사용하세요.')
    if key.count('.')==2:
        try:
            body=key.split('.')[1]; decoded=json.loads(base64.urlsafe_b64decode(body+'='*(-len(body)%4)))
            if not isinstance(decoded,dict) or decoded.get('role')!='anon':
                raise CloudError('서비스 키는 앱에 사용할 수 없습니다. publishable/anon key를 사용하세요.')
        except (ValueError,KeyError,TypeError) as error:
            if isinstance(error,CloudError):raise
            raise CloudError('유효한 publishable/anon key 설정을 확인하세요.') from None
    return url.rstrip('/'),key


class SupabaseClient:
    def __init__(self,url,key,session=None):
        self.url,self.key=settings(url,key)
        self.http=session or requests.Session()
        self.access_token=None
        self.refresh_token=None
        self.expires_at=0
        self.user_id=None
        self.email=None

    def _request(self,method,path,payload=None,token=None):
        headers={'apikey':self.key,'Content-Type':'application/json'}
        if token:headers['Authorization']='Bearer '+token
        try:
            response=self.http.request(method,self.url+path,json=payload,headers=headers,timeout=(5,30),allow_redirects=False)
        except requests.RequestException:
            raise CloudUnavailable('Supabase에 연결하지 못했습니다. 자동으로 로컬 모드에 저장하지 않습니다. 인터넷 연결을 확인하세요.') from None
        if response.status_code>=300:
            try: body=response.json()
            except (ValueError,TypeError): body={}
            code=body.get('code','') if isinstance(body,dict) else ''
            message=body.get('message','') if isinstance(body,dict) else ''
            if 'RL_CONFLICT' in message or code=='40001':
                raise CloudConflict('다른 기기에서 자료가 변경되었습니다. 저장을 중단했습니다. 입력 내용을 복사한 뒤 공용 자료를 새로고침하고 다시 수정하세요.')
            if response.status_code in (401,403) or code=='42501':
                raise CloudError('로그인 만료 또는 작업공간 접근 권한이 없습니다. 다시 로그인하고 팀의 작업공간 권한을 확인하세요.')
            if response.status_code==404 and '/rpc/' in path and path.endswith('_v2'):
                raise CloudError('Supabase SQL v2가 필요합니다. 앱을 중지하고 안내된 백업·확인 절차 후 관리자가 새 마이그레이션을 적용하세요.')
            if response.status_code in (400,404) and ('/auth/' in path):
                raise CloudError('로그인 정보를 확인하세요. 이메일 확인·초대 수락·비밀번호 설정은 Supabase 프로젝트에서 먼저 완료해야 합니다.')
            raise CloudError('공용 DB 요청을 완료하지 못했습니다. 서버 스키마 버전·권한·입력값을 확인하세요. 원시 오류와 인증 정보는 표시하지 않습니다.')
        try:return response.json() if response.content else None
        except ValueError:
            raise CloudUnavailable('Supabase 응답을 확인하지 못했습니다. 저장 성공 여부를 확인한 뒤 다시 시도하세요.') from None

    def _tokens(self,result):
        if not isinstance(result,dict) or not result.get('access_token') or not result.get('refresh_token'):
            raise CloudError('인증 응답이 올바르지 않습니다. 다시 로그인하세요.')
        self.access_token=result['access_token'];self.refresh_token=result['refresh_token']
        self.expires_at=time.time()+int(result.get('expires_in',3600))

    def sign_in(self,email,password):
        self._tokens(self._request('POST','/auth/v1/token?grant_type=password',{'email':email,'password':password}))
        user=self._request('GET','/auth/v1/user',token=self.access_token)
        try:UUID(user['id'])
        except (ValueError,TypeError,KeyError):
            self.clear();raise CloudError('인증된 사용자 정보를 확인하지 못했습니다.') from None
        self.user_id=user['id'];self.email=user.get('email','')

    def clear(self):
        self.access_token=self.refresh_token=None;self.expires_at=0;self.user_id=self.email=None

    def refresh(self):
        if not self.access_token or not self.refresh_token:
            raise CloudError('공용 저장소는 로그인이 필요합니다.')
        if time.time()+60>=self.expires_at:
            try:self._tokens(self._request('POST','/auth/v1/token?grant_type=refresh_token',{'refresh_token':self.refresh_token}))
            except CloudError:
                self.clear();raise

    def logout(self):
        try:
            if self.access_token:self._request('POST','/auth/v1/logout?scope=local',token=self.access_token)
        finally:self.clear()

    def rpc(self,name,params=None):
        if name not in ('workspaces','create_workspace','status','read','commit'):
            raise CloudError('지원하지 않는 공용 저장소 요청입니다.')
        self.refresh()
        return self._request('POST','/rest/v1/rpc/reviewlab_'+name+('_v2' if name!='create_workspace' else ''),params or {},token=self.access_token)
