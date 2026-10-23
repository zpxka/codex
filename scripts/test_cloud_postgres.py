"""자기 소유의 임시 Docker PostgreSQL에서만 전체 테스트를 실행한다.

실제 Supabase 접속·서명 검증을 대신하지 않는다. Python 테스트와 SQL/RLS만 검증한다.
"""
from pathlib import Path
import os
import secrets
import subprocess
import sys
import time
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
BOOTSTRAP='''
CREATE ROLE anon NOLOGIN;
CREATE ROLE authenticated NOLOGIN;
CREATE SCHEMA auth;
CREATE TABLE auth.users(id uuid PRIMARY KEY);
CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS $$ SELECT nullif(current_setting('request.jwt.claim.sub',true),'')::uuid $$;
GRANT USAGE ON SCHEMA auth TO authenticated,anon;
GRANT EXECUTE ON FUNCTION auth.uid() TO authenticated,anon;
'''


def main():
    name='reviewlab-pg-test-'+uuid4().hex[:12]
    try:
        # 테스트 전용 임의 비밀번호. 호스트 포트를 공개하지 않고 종료 시 컨테이너를 제거한다.
        environment=dict(os.environ,POSTGRES_PASSWORD=secrets.token_urlsafe(32))
        subprocess.run(['docker','run','-d','--name',name,'-e','POSTGRES_PASSWORD','-e','POSTGRES_DB=reviewlab_test','postgres:16-alpine'],env=environment,check=True)
        for _ in range(30):
            ready=subprocess.run(['docker','exec',name,'pg_isready','-U','postgres'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            if ready.returncode==0:break
            time.sleep(.5)
        else:raise RuntimeError('테스트 PostgreSQL 시작 실패')
        command=['docker','exec','-i',name,'psql','-U','postgres','-d','reviewlab_test','-v','ON_ERROR_STOP=1']
        subprocess.run(command,input=BOOTSTRAP,text=True,check=True,stdout=subprocess.DEVNULL)
        migration=ROOT/'supabase/migrations/202610240001_shared_storage.sql'
        subprocess.run(command,input=migration.read_text(),text=True,check=True,stdout=subprocess.DEVNULL)
        environment=dict(os.environ,REVIEWLAB_TEST_PG_CONTAINER=name,REVIEWLAB_STORAGE='local')
        return subprocess.run([sys.executable,'-m','pytest','-q'],cwd=ROOT,env=environment).returncode
    finally:
        subprocess.run(['docker','rm','-f','-v',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)


if __name__=='__main__':sys.exit(main())
