"""기존 v1은 변경하지 않고 추가 migration과 버전별 RPC만 생성한다."""
from pathlib import Path
import re
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.build_cloud_sql import render
from reviewlab.services.surveys import TABLES


def main():
    full=render()
    out=['-- ReviewLab Supabase v2: v1 이후 1회 적용. 기존 데이터 삭제/초기화 없음.\nBEGIN;\n',
         "DO $$ BEGIN IF (SELECT max(version) FROM reviewlab.schema_migrations) IS DISTINCT FROM 1 THEN RAISE EXCEPTION 'Expected schema v1'; END IF; END; $$;\n"]
    for t in TABLES:
        out.append(re.search(r'CREATE TABLE reviewlab\.'+t+r'\(.*?;\n',full,re.S).group())
        out.append(f'ALTER TABLE reviewlab.{t} ENABLE ROW LEVEL SECURITY;\nCREATE POLICY member_read ON reviewlab.{t} FOR SELECT TO authenticated USING(reviewlab.allowed(workspace_id));\nGRANT SELECT ON reviewlab.{t} TO authenticated;\nREVOKE ALL ON reviewlab.{t} FROM anon;\nREVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON reviewlab.{t} FROM authenticated;\n')
    out.extend([
        'ALTER TABLE reviewlab.survey_projects ADD CHECK(data_kind=data_mode);\n',
        'ALTER TABLE reviewlab.survey_questions ADD UNIQUE(workspace_id,data_mode,project_id,variable);\n',
        'ALTER TABLE reviewlab.survey_imports ADD UNIQUE(workspace_id,data_mode,project_id,file_sha256);\n',
        'ALTER TABLE reviewlab.survey_answers ADD UNIQUE(workspace_id,data_mode,response_id,question_id);\n',
        'ALTER TABLE reviewlab.survey_responses ADD CHECK(source_row>=2);\n',
    ])
    for name in ('workspaces','status','read','commit'):
        definition=re.search(r'CREATE FUNCTION public\.reviewlab_'+name+r'\(.*?\$\$;',full,re.S).group()
        if name=='commit':
            check = ''' IF EXISTS(SELECT 1 FROM reviewlab.survey_answers a JOIN reviewlab.survey_responses r ON r.workspace_id=a.workspace_id AND r.data_mode=a.data_mode AND r.id=a.response_id JOIN reviewlab.survey_questions q ON q.workspace_id=a.workspace_id AND q.data_mode=a.data_mode AND q.id=a.question_id WHERE a.workspace_id=p_workspace AND a.data_mode=p_mode AND q.project_id<>r.project_id) OR EXISTS(SELECT 1 FROM reviewlab.survey_responses r JOIN reviewlab.survey_imports i ON i.workspace_id=r.workspace_id AND i.data_mode=r.data_mode AND i.id=r.import_id WHERE r.workspace_id=p_workspace AND r.data_mode=p_mode AND r.project_id<>i.project_id) THEN RAISE EXCEPTION 'RL_SURVEY_PROJECT_MISMATCH' USING ERRCODE='22023'; END IF;
'''
            definition=definition.replace(' SET CONSTRAINTS ALL IMMEDIATE;',check+' SET CONSTRAINTS ALL IMMEDIATE;')
        out.append(definition.replace('public.reviewlab_'+name+'(', 'public.reviewlab_'+name+'_v2(').replace("'schema_version',1", "'schema_version',2")+'\n')
        if name!='commit':
            out.append(definition.replace('CREATE FUNCTION','CREATE OR REPLACE FUNCTION',1).replace("'schema_version',1","'schema_version',2")+'\n')
    out.append("""
CREATE OR REPLACE FUNCTION public.reviewlab_commit(p_workspace uuid,p_mode text,p_expected_revision bigint,p_operation uuid,p_data jsonb) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,reviewlab AS $$
BEGIN
 IF NOT reviewlab.allowed(p_workspace) THEN RAISE EXCEPTION 'RL_FORBIDDEN' USING ERRCODE='42501'; END IF;
 RAISE EXCEPTION 'RL_SCHEMA_UPGRADE_REQUIRED' USING ERRCODE='22023';
END; $$;
REVOKE ALL ON FUNCTION public.reviewlab_workspaces_v2(), public.reviewlab_status_v2(uuid,text), public.reviewlab_read_v2(uuid,text), public.reviewlab_commit_v2(uuid,text,bigint,uuid,jsonb) FROM PUBLIC,anon;
GRANT EXECUTE ON FUNCTION public.reviewlab_workspaces_v2(), public.reviewlab_status_v2(uuid,text), public.reviewlab_read_v2(uuid,text), public.reviewlab_commit_v2(uuid,text,bigint,uuid,jsonb) TO authenticated;
INSERT INTO reviewlab.schema_migrations VALUES(2,now());
COMMIT;
""")
    Path('supabase/migrations/202610250002_surveys.sql').write_text(''.join(out),encoding='utf-8')

if __name__=='__main__':main()
