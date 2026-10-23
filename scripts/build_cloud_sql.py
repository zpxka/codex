"""정적 Supabase 마이그레이션 생성기. 앱 실행 중에는 실행하지 않는다."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).parents[1]))
from reviewlab.cloud.dataset import SCHEMA, TABLES

HEADER = '''-- ReviewLab cloud schema v1. Supabase SQL Editor에서 관리자가 1회 적용한다.
-- 앱에는 publishable/anon key와 사용자 토큰만 사용한다. service_role은 사용하지 않는다.
BEGIN;
CREATE SCHEMA IF NOT EXISTS reviewlab;
REVOKE ALL ON SCHEMA reviewlab FROM PUBLIC, anon;
GRANT USAGE ON SCHEMA reviewlab TO authenticated;
CREATE TABLE reviewlab.schema_migrations(version integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now());
INSERT INTO reviewlab.schema_migrations VALUES(1,now());
CREATE TABLE reviewlab.workspaces(id uuid PRIMARY KEY DEFAULT gen_random_uuid(), name text NOT NULL CHECK(length(name) BETWEEN 1 AND 200), created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE reviewlab.workspace_members(workspace_id uuid NOT NULL REFERENCES reviewlab.workspaces(id), user_id uuid NOT NULL REFERENCES auth.users(id), role text NOT NULL CHECK(role IN ('owner','editor')), PRIMARY KEY(workspace_id,user_id));
CREATE TABLE reviewlab.workspace_states(workspace_id uuid NOT NULL REFERENCES reviewlab.workspaces(id), data_mode text NOT NULL CHECK(data_mode IN ('actual','example')), revision bigint NOT NULL DEFAULT 0 CHECK(revision>=0), changed_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(workspace_id,data_mode));
CREATE TABLE reviewlab.commit_receipts(workspace_id uuid NOT NULL, data_mode text NOT NULL, operation_id uuid NOT NULL, payload_hash text NOT NULL, revision bigint NOT NULL, actor uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(workspace_id,data_mode,operation_id), FOREIGN KEY(workspace_id,data_mode) REFERENCES reviewlab.workspace_states(workspace_id,data_mode));

CREATE FUNCTION reviewlab.allowed(wid uuid) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,reviewlab AS $$
 SELECT auth.uid() IS NOT NULL AND EXISTS(SELECT 1 FROM reviewlab.workspace_members WHERE workspace_id=wid AND user_id=auth.uid());
$$;
REVOKE ALL ON FUNCTION reviewlab.allowed(uuid) FROM PUBLIC,anon;
GRANT EXECUTE ON FUNCTION reviewlab.allowed(uuid) TO authenticated;
'''

def pgtype(c):
    if c['name']=='id' or c['name'].endswith('_id'):
        return 'uuid'
    return {'INTEGER':'bigint','REAL':'double precision','TEXT':'text'}[c['type']]


def columns(table):
    return ', '.join(c['name']+' '+pgtype(c) for c in SCHEMA[table])


def keys(table):
    if table in ('verification_events','review_checks'):
        return ['sequence']
    return [c['name'] for c in sorted(SCHEMA[table],key=lambda c:c['pk']) if c['pk']]


def main():
    out=[HEADER]
    import sqlite3
    from reviewlab.cloud.dataset import empty_schema
    db=sqlite3.connect(':memory:');empty_schema(db,'example')
    for t in TABLES:
        fields=[c['name']+' '+pgtype(c)+(' NOT NULL' if c['notnull'] or c['pk'] else '') for c in SCHEMA[t]]
        pk=['workspace_id','data_mode']+keys(t)
        constraints=['PRIMARY KEY('+','.join(pk)+')','FOREIGN KEY(workspace_id,data_mode) REFERENCES reviewlab.workspace_states(workspace_id,data_mode)']
        if t in ('verification_events','review_checks'):
            constraints.append('UNIQUE(workspace_id,data_mode,id)')
        for row in db.execute(f'PRAGMA foreign_key_list({t})'):
            _,_,parent,child,parentcol,*_=row
            constraints.append(f'FOREIGN KEY(workspace_id,data_mode,{child}) REFERENCES reviewlab.{parent}(workspace_id,data_mode,{parentcol}) DEFERRABLE INITIALLY DEFERRED')
        if t=='tags':constraints.extend(['UNIQUE(workspace_id,data_mode,name)', "CHECK(length(btrim(name)) BETWEEN 1 AND 100)"])
        if t=='sources':constraints.extend(["CHECK(length(btrim(title))>0)", "CHECK(source_type IN ('학술논문','뉴스기사','정부·공공기관 자료','플랫폼 정책','기타'))"])
        if t=='verification_events':constraints.append("CHECK(status IN ('미검증','원문 확인 중','검증 완료','사용 보류'))")
        if t=='review_checks':constraints.append("CHECK(status IN ('미검토','검토 중','검토 완료','분석 제외'))")
        if t=='reviews':constraints.append('UNIQUE(workspace_id,data_mode,project_id,identity_key)')
        if t=='review_projects':constraints.extend(['CHECK(rating_max>rating_min)', 'CHECK(data_kind=data_mode)'])
        out.append(f'CREATE TABLE reviewlab.{t}(workspace_id uuid NOT NULL, data_mode text NOT NULL,\n '+',\n '.join(fields+constraints)+'\n);\n')
    out.append('''
CREATE INDEX reviewlab_review_project ON reviewlab.reviews(workspace_id,data_mode,project_id);
CREATE INDEX reviewlab_source_checks ON reviewlab.verification_events(workspace_id,data_mode,source_id,sequence);
CREATE INDEX reviewlab_review_checks ON reviewlab.review_checks(workspace_id,data_mode,review_id,sequence);
''')
    for t in ('workspaces','workspace_members','workspace_states','commit_receipts',*TABLES):
        wid='id' if t=='workspaces' else 'workspace_id'
        out.append(f'ALTER TABLE reviewlab.{t} ENABLE ROW LEVEL SECURITY;\nCREATE POLICY member_read ON reviewlab.{t} FOR SELECT TO authenticated USING(reviewlab.allowed({wid}));\nGRANT SELECT ON reviewlab.{t} TO authenticated;\nREVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON reviewlab.{t} FROM authenticated,anon;\n')
    out.append('''
CREATE FUNCTION public.reviewlab_workspaces() RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,reviewlab AS $$
 SELECT jsonb_build_object('schema_version',1,'workspaces',coalesce(jsonb_agg(jsonb_build_object('id',w.id,'name',w.name) ORDER BY w.created_at,w.id),'[]'::jsonb)) FROM reviewlab.workspaces w WHERE reviewlab.allowed(w.id);
$$;
CREATE FUNCTION public.reviewlab_create_workspace(p_name text) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,reviewlab AS $$
DECLARE wid uuid;
BEGIN
 IF auth.uid() IS NULL THEN RAISE EXCEPTION 'RL_FORBIDDEN' USING ERRCODE='42501'; END IF;
 INSERT INTO reviewlab.workspaces(name) VALUES(p_name) RETURNING id INTO wid;
 INSERT INTO reviewlab.workspace_members VALUES(wid,auth.uid(),'owner');
 INSERT INTO reviewlab.workspace_states(workspace_id,data_mode) VALUES(wid,'actual'),(wid,'example');
 RETURN wid;
END; $$;
CREATE FUNCTION public.reviewlab_status(p_workspace uuid,p_mode text) RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,reviewlab AS $$
DECLARE result jsonb;
BEGIN
 IF p_mode IS NULL OR p_mode NOT IN ('actual','example') OR NOT reviewlab.allowed(p_workspace) THEN RAISE EXCEPTION 'RL_FORBIDDEN' USING ERRCODE='42501'; END IF;
 SELECT jsonb_build_object('schema_version',1,'revision',revision,'changed_at',changed_at) INTO result FROM reviewlab.workspace_states WHERE workspace_id=p_workspace AND data_mode=p_mode;
 RETURN result;
END; $$;
CREATE FUNCTION public.reviewlab_read(p_workspace uuid,p_mode text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,reviewlab AS $$
DECLARE rev bigint; result jsonb := '{}'::jsonb;
BEGIN
 IF p_mode IS NULL OR p_mode NOT IN ('actual','example') OR NOT reviewlab.allowed(p_workspace) THEN RAISE EXCEPTION 'RL_FORBIDDEN' USING ERRCODE='42501'; END IF;
 SELECT revision INTO rev FROM reviewlab.workspace_states WHERE workspace_id=p_workspace AND data_mode=p_mode FOR SHARE;
''')
    for t in TABLES:
        out.append(f" SELECT result || jsonb_build_object('{t}',coalesce(jsonb_agg(to_jsonb(r)-'workspace_id'-'data_mode'),'[]'::jsonb)) INTO result FROM reviewlab.{t} r WHERE workspace_id=p_workspace AND data_mode=p_mode;\n")
    out.append('''
 RETURN jsonb_build_object('schema_version',1,'workspace_id',p_workspace,'mode',p_mode,'revision',rev,'data',result);
END; $$;
CREATE FUNCTION public.reviewlab_commit(p_workspace uuid,p_mode text,p_expected_revision bigint,p_operation uuid,p_data jsonb) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,reviewlab AS $$
DECLARE rev bigint; receipt reviewlab.commit_receipts%ROWTYPE; digest text; item jsonb; n bigint := 0;
BEGIN
 IF p_mode IS NULL OR p_mode NOT IN ('actual','example') OR NOT reviewlab.allowed(p_workspace) THEN RAISE EXCEPTION 'RL_FORBIDDEN' USING ERRCODE='42501'; END IF;
 IF p_operation IS NULL OR p_expected_revision IS NULL OR jsonb_typeof(p_data) IS DISTINCT FROM 'object' OR octet_length(p_data::text)>20971520 THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 SELECT revision INTO rev FROM reviewlab.workspace_states WHERE workspace_id=p_workspace AND data_mode=p_mode FOR UPDATE;
 digest := encode(sha256(convert_to(p_data::text,'UTF8')),'hex');
 SELECT * INTO receipt FROM reviewlab.commit_receipts WHERE workspace_id=p_workspace AND data_mode=p_mode AND operation_id=p_operation;
 IF FOUND THEN
   IF receipt.payload_hash<>digest THEN RAISE EXCEPTION 'RL_OPERATION_MISMATCH' USING ERRCODE='22023'; END IF;
   RETURN jsonb_build_object('revision',receipt.revision,'operation_id',p_operation,'replayed',true);
 END IF;
 IF rev<>p_expected_revision THEN RAISE EXCEPTION 'RL_CONFLICT' USING ERRCODE='40001'; END IF;
''')
    array='ARRAY['+','.join("'"+t+"'" for t in sorted(TABLES))+']::text[]'
    out.append(f" IF ARRAY(SELECT jsonb_object_keys(p_data) ORDER BY 1)<>{array} THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;\n")
    for t in TABLES:
        fields=sorted(c['name'] for c in SCHEMA[t]);fa='ARRAY['+','.join("'"+k+"'" for k in fields)+']::text[]'
        out.append(f" IF jsonb_typeof(p_data->'{t}') IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;\n n := n+jsonb_array_length(p_data->'{t}');\n FOR item IN SELECT value FROM jsonb_array_elements(p_data->'{t}') LOOP\n IF jsonb_typeof(item) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;\n IF ARRAY(SELECT jsonb_object_keys(item) ORDER BY 1)<>{fa} THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;\n END LOOP;\n")
    out.append(" IF n>50000 THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;\n SET CONSTRAINTS ALL DEFERRED;\n")
    for t in TABLES:
        fields=[c['name'] for c in SCHEMA[t]];pk=keys(t);others=[k for k in fields if k not in pk]
        action='DO UPDATE SET '+','.join(k+'=EXCLUDED.'+k for k in others) if others else 'DO NOTHING'
        out.append(f" INSERT INTO reviewlab.{t}(workspace_id,data_mode,{','.join(fields)}) SELECT p_workspace,p_mode,{','.join('x.'+k for k in fields)} FROM jsonb_to_recordset(p_data->'{t}') AS x({columns(t)}) ON CONFLICT(workspace_id,data_mode,{','.join(pk)}) {action};\n")
    for t in reversed(TABLES):
        predicate=' AND '.join('x.'+k+'=r.'+k for k in keys(t))
        out.append(f" DELETE FROM reviewlab.{t} r WHERE workspace_id=p_workspace AND data_mode=p_mode AND NOT EXISTS(SELECT 1 FROM jsonb_to_recordset(p_data->'{t}') AS x({columns(t)}) WHERE {predicate});\n")
    out.append('''
 SET CONSTRAINTS ALL IMMEDIATE;
 UPDATE reviewlab.workspace_states SET revision=rev+1,changed_at=now() WHERE workspace_id=p_workspace AND data_mode=p_mode;
 INSERT INTO reviewlab.commit_receipts(workspace_id,data_mode,operation_id,payload_hash,revision,actor) VALUES(p_workspace,p_mode,p_operation,digest,rev+1,auth.uid());
 RETURN jsonb_build_object('revision',rev+1,'operation_id',p_operation,'replayed',false);
END; $$;
REVOKE ALL ON FUNCTION public.reviewlab_workspaces(), public.reviewlab_create_workspace(text), public.reviewlab_status(uuid,text), public.reviewlab_read(uuid,text), public.reviewlab_commit(uuid,text,bigint,uuid,jsonb) FROM PUBLIC,anon;
GRANT EXECUTE ON FUNCTION public.reviewlab_workspaces(), public.reviewlab_create_workspace(text), public.reviewlab_status(uuid,text), public.reviewlab_read(uuid,text), public.reviewlab_commit(uuid,text,bigint,uuid,jsonb) TO authenticated;
COMMENT ON FUNCTION public.reviewlab_commit(uuid,text,bigint,uuid,jsonb) IS 'Authenticated member-only atomic CAS commit; service_role not used. New tables require a versioned migration.';
COMMIT;
''')
    Path('supabase/migrations/202610240001_shared_storage.sql').write_text(''.join(out))

if __name__=='__main__':main()
