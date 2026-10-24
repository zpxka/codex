"""자기 소유의 임시 Docker PostgreSQL에서만 전체 테스트를 실행한다.

실제 Supabase 접속·서명 검증을 대신하지 않는다. Python 테스트와 SQL/RLS만 검증한다.
"""
from pathlib import Path
import json
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
    run_id=uuid4().hex
    created=False
    phase='docker_start'
    exit_code=2
    status='setup_failed'
    try:
        # 테스트 전용 임의 비밀번호. 호스트 포트를 공개하지 않고 종료 시 컨테이너를 제거한다.
        environment=dict(os.environ,POSTGRES_PASSWORD=secrets.token_urlsafe(32))
        subprocess.run(['docker','run','-d','--name',name,'--label','reviewlab.test-run='+run_id,'-e','POSTGRES_PASSWORD','-e','POSTGRES_DB=reviewlab_test','postgres:16-alpine'],env=environment,check=True,capture_output=True)
        created=True
        phase='postgres_ready'
        for _ in range(30):
            ready=subprocess.run(['docker','exec',name,'pg_isready','-U','postgres'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            if ready.returncode==0:break
            time.sleep(.5)
        else:raise RuntimeError('테스트 PostgreSQL 시작 실패')
        command=['docker','exec','-i',name,'psql','-U','postgres','-d','reviewlab_test','-v','ON_ERROR_STOP=1']
        phase='auth_stub_and_sql_migration'
        subprocess.run(command,input=BOOTSTRAP,text=True,encoding='utf-8',check=True,stdout=subprocess.DEVNULL)
        migration=ROOT/'supabase/migrations/202610240001_shared_storage.sql'
        subprocess.run(command,input=migration.read_text(encoding='utf-8'),text=True,encoding='utf-8',check=True,stdout=subprocess.DEVNULL)
        environment=dict(os.environ,REVIEWLAB_TEST_PG_CONTAINER=name,REVIEWLAB_TEST_PG_RUN=run_id,REVIEWLAB_STORAGE='local',PYTHONUTF8='1')
        phase='pytest'
        exit_code=subprocess.run([sys.executable,'-m','pytest','-q',*sys.argv[1:]],cwd=ROOT,env=environment).returncode
        status='tests_finished'
    except (OSError, subprocess.SubprocessError, RuntimeError):
        # 준비 실패와 pytest assertion 실패(exit 1)를 분리한다. 환경/비밀번호 출력 금지.
        print('POSTGRES_TEST_SETUP_FAILED: Docker availability/startup/migration setup (exit 2).')
    finally:
        if created:
            try:
                result=subprocess.run(['docker','rm','-f','-v',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                if result.returncode:raise RuntimeError()
            except (OSError, RuntimeError):
                print('POSTGRES_TEST_CLEANUP_FAILED: only the run-owned test container needs cleanup (exit 2).')
                status='cleanup_failed';exit_code=2
        report=ROOT/'test-results/postgres-environment.json'
        report.parent.mkdir(parents=True,exist_ok=True)
        report.write_text(json.dumps(dict(status=status,phase=phase,exit_code=exit_code)),encoding='utf-8')
    return exit_code


if __name__=='__main__':sys.exit(main())
