"""v1/v2 파일 보존. 원문 v3와 별도 Storage 정책 제안 생성."""
from pathlib import Path
import re
from unittest.mock import patch
from scripts.build_cloud_sql import render
from scripts.build_survey_sql import main as survey_sql
from reviewlab.services.documents import TABLES


def main():
    with patch.object(Path,'write_text') as capture:survey_sql()
    previous=capture.call_args.args[0];full=render()
    out=["-- v2 이후 승인한 관리자가 1회 적용\nBEGIN;\nDO $$ BEGIN IF (SELECT max(version) FROM reviewlab.schema_migrations)<>2 THEN RAISE EXCEPTION 'Expected v2'; END IF; END; $$;\n"]
    for t in TABLES:
        definition=re.search(r'CREATE TABLE reviewlab\.'+t+r'\(.*?;\n',full,re.S).group()
        if t=='document_files':definition=definition.replace('REFERENCES reviewlab.sources(workspace_id,data_mode,id) DEFERRABLE','REFERENCES reviewlab.sources(workspace_id,data_mode,id) ON DELETE SET NULL (source_id) DEFERRABLE')
        out.append(definition)
        out.append(f'ALTER TABLE reviewlab.{t} ENABLE ROW LEVEL SECURITY; CREATE POLICY member_read ON reviewlab.{t} FOR SELECT TO authenticated USING(reviewlab.allowed(workspace_id)); GRANT SELECT ON reviewlab.{t} TO authenticated; REVOKE ALL ON reviewlab.{t} FROM anon; REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON reviewlab.{t} FROM authenticated;\n')
    for name in ('workspaces','status','read','commit'):
        definition=re.search(r'CREATE FUNCTION public\.reviewlab_'+name+r'_v2\(.*?\$\$;',previous,re.S).group()
        out.append(definition.replace(name+'_v2(',name+'_v3(').replace("'schema_version',2","'schema_version',3")+'\n')
        if name!='commit':out.append(definition.replace('CREATE FUNCTION','CREATE OR REPLACE FUNCTION',1).replace("'schema_version',2","'schema_version',3")+'\n')
    out.append("""
CREATE OR REPLACE FUNCTION public.reviewlab_commit_v2(p_workspace uuid,p_mode text,p_expected_revision bigint,p_operation uuid,p_data jsonb) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,reviewlab AS $$ BEGIN RAISE EXCEPTION 'RL_SCHEMA_UPGRADE_REQUIRED' USING ERRCODE='22023'; END; $$;
REVOKE ALL ON FUNCTION public.reviewlab_workspaces_v3(),public.reviewlab_read_v3(uuid,text),public.reviewlab_status_v3(uuid,text),public.reviewlab_commit_v3(uuid,text,bigint,uuid,jsonb) FROM PUBLIC,anon;
GRANT EXECUTE ON FUNCTION public.reviewlab_workspaces_v3(),public.reviewlab_read_v3(uuid,text),public.reviewlab_status_v3(uuid,text),public.reviewlab_commit_v3(uuid,text,bigint,uuid,jsonb) TO authenticated;
INSERT INTO reviewlab.schema_migrations VALUES(3,now()); COMMIT;
""")
    Path('supabase/migrations/202610270003_documents.sql').write_text(''.join(out),encoding='utf-8')

if __name__=='__main__':main()
