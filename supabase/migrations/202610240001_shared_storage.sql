-- ReviewLab cloud schema v1. Supabase SQL Editor에서 관리자가 1회 적용한다.
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
CREATE TABLE reviewlab.tags(workspace_id uuid NOT NULL, data_mode text NOT NULL,
 id uuid NOT NULL,
 name text NOT NULL,
 PRIMARY KEY(workspace_id,data_mode,id),
 FOREIGN KEY(workspace_id,data_mode) REFERENCES reviewlab.workspace_states(workspace_id,data_mode),
 UNIQUE(workspace_id,data_mode,name),
 CHECK(length(btrim(name)) BETWEEN 1 AND 100)
);
CREATE TABLE reviewlab.sources(workspace_id uuid NOT NULL, data_mode text NOT NULL,
 id uuid NOT NULL,
 title text NOT NULL,
 authors text NOT NULL,
 publication_date text NOT NULL,
 source_type text NOT NULL,
 original_url text NOT NULL,
 notes text NOT NULL,
 purpose text NOT NULL,
 created_at text NOT NULL,
 updated_at text NOT NULL,
 PRIMARY KEY(workspace_id,data_mode,id),
 FOREIGN KEY(workspace_id,data_mode) REFERENCES reviewlab.workspace_states(workspace_id,data_mode),
 CHECK(length(btrim(title))>0),
 CHECK(source_type IN ('학술논문','뉴스기사','정부·공공기관 자료','플랫폼 정책','기타'))
);
CREATE TABLE reviewlab.source_tags(workspace_id uuid NOT NULL, data_mode text NOT NULL,
 source_id uuid NOT NULL,
 tag_id uuid NOT NULL,
 PRIMARY KEY(workspace_id,data_mode,source_id,tag_id),
 FOREIGN KEY(workspace_id,data_mode) REFERENCES reviewlab.workspace_states(workspace_id,data_mode),
 FOREIGN KEY(workspace_id,data_mode,tag_id) REFERENCES reviewlab.tags(workspace_id,data_mode,id) DEFERRABLE INITIALLY DEFERRED,
 FOREIGN KEY(workspace_id,data_mode,source_id) REFERENCES reviewlab.sources(workspace_id,data_mode,id) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE reviewlab.verification_events(workspace_id uuid NOT NULL, data_mode text NOT NULL,
 sequence bigint NOT NULL,
 id uuid NOT NULL,
 source_id uuid NOT NULL,
 status text NOT NULL,
 checked_on text NOT NULL,
 checked_by text NOT NULL,
 evidence text NOT NULL,
 memo text NOT NULL,
 recorded_at text NOT NULL,
 PRIMARY KEY(workspace_id,data_mode,sequence),
 FOREIGN KEY(workspace_id,data_mode) REFERENCES reviewlab.workspace_states(workspace_id,data_mode),
 UNIQUE(workspace_id,data_mode,id),
 FOREIGN KEY(workspace_id,data_mode,source_id) REFERENCES reviewlab.sources(workspace_id,data_mode,id) DEFERRABLE INITIALLY DEFERRED,
 CHECK(status IN ('미검증','원문 확인 중','검증 완료','사용 보류'))
);
CREATE TABLE reviewlab.review_projects(workspace_id uuid NOT NULL, data_mode text NOT NULL,
 id uuid NOT NULL,
 name text NOT NULL,
 data_kind text NOT NULL,
 rating_min double precision NOT NULL,
 rating_max double precision NOT NULL,
 description text NOT NULL,
 created_at text NOT NULL,
 PRIMARY KEY(workspace_id,data_mode,id),
 FOREIGN KEY(workspace_id,data_mode) REFERENCES reviewlab.workspace_states(workspace_id,data_mode),
 CHECK(rating_max>rating_min),
 CHECK(data_kind=data_mode)
);
CREATE TABLE reviewlab.review_imports(workspace_id uuid NOT NULL, data_mode text NOT NULL,
 id uuid NOT NULL,
 project_id uuid NOT NULL,
 file_sha256 text NOT NULL,
 encoding text NOT NULL,
 mapping_json text NOT NULL,
 source_note text NOT NULL,
 total_rows bigint NOT NULL,
 imported_rows bigint NOT NULL,
 skipped_rows bigint NOT NULL,
 ignored_columns bigint NOT NULL,
 created_at text NOT NULL,
 PRIMARY KEY(workspace_id,data_mode,id),
 FOREIGN KEY(workspace_id,data_mode) REFERENCES reviewlab.workspace_states(workspace_id,data_mode),
 FOREIGN KEY(workspace_id,data_mode,project_id) REFERENCES reviewlab.review_projects(workspace_id,data_mode,id) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE reviewlab.reviews(workspace_id uuid NOT NULL, data_mode text NOT NULL,
 id uuid NOT NULL,
 project_id uuid NOT NULL,
 import_id uuid NOT NULL,
 source_row bigint NOT NULL,
 identity_key text NOT NULL,
 review_key text,
 platform text,
 industry text,
 rating double precision,
 review_date text,
 content text,
 event_mentioned text NOT NULL,
 ad_disclosed text NOT NULL,
 collected_on text,
 original_url text,
 raw_json text NOT NULL,
 created_at text NOT NULL,
 PRIMARY KEY(workspace_id,data_mode,id),
 FOREIGN KEY(workspace_id,data_mode) REFERENCES reviewlab.workspace_states(workspace_id,data_mode),
 FOREIGN KEY(workspace_id,data_mode,import_id) REFERENCES reviewlab.review_imports(workspace_id,data_mode,id) DEFERRABLE INITIALLY DEFERRED,
 FOREIGN KEY(workspace_id,data_mode,project_id) REFERENCES reviewlab.review_projects(workspace_id,data_mode,id) DEFERRABLE INITIALLY DEFERRED,
 UNIQUE(workspace_id,data_mode,project_id,identity_key)
);
CREATE TABLE reviewlab.review_notes(workspace_id uuid NOT NULL, data_mode text NOT NULL,
 id uuid NOT NULL,
 review_id uuid NOT NULL,
 memo text NOT NULL,
 recorded_at text NOT NULL,
 PRIMARY KEY(workspace_id,data_mode,id),
 FOREIGN KEY(workspace_id,data_mode) REFERENCES reviewlab.workspace_states(workspace_id,data_mode),
 FOREIGN KEY(workspace_id,data_mode,review_id) REFERENCES reviewlab.reviews(workspace_id,data_mode,id) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE reviewlab.review_checks(workspace_id uuid NOT NULL, data_mode text NOT NULL,
 sequence bigint NOT NULL,
 id uuid NOT NULL,
 review_id uuid NOT NULL,
 status text NOT NULL,
 reviewer text NOT NULL,
 checked_on text NOT NULL,
 evidence text NOT NULL,
 memo text NOT NULL,
 recorded_at text NOT NULL,
 PRIMARY KEY(workspace_id,data_mode,sequence),
 FOREIGN KEY(workspace_id,data_mode) REFERENCES reviewlab.workspace_states(workspace_id,data_mode),
 UNIQUE(workspace_id,data_mode,id),
 FOREIGN KEY(workspace_id,data_mode,review_id) REFERENCES reviewlab.reviews(workspace_id,data_mode,id) DEFERRABLE INITIALLY DEFERRED,
 CHECK(status IN ('미검토','검토 중','검토 완료','분석 제외'))
);
CREATE TABLE reviewlab.review_source_links(workspace_id uuid NOT NULL, data_mode text NOT NULL,
 project_id uuid NOT NULL,
 referenced_id uuid NOT NULL,
 source_id uuid,
 title text NOT NULL,
 original_url text NOT NULL,
 PRIMARY KEY(workspace_id,data_mode,project_id,referenced_id),
 FOREIGN KEY(workspace_id,data_mode) REFERENCES reviewlab.workspace_states(workspace_id,data_mode),
 FOREIGN KEY(workspace_id,data_mode,source_id) REFERENCES reviewlab.sources(workspace_id,data_mode,id) DEFERRABLE INITIALLY DEFERRED,
 FOREIGN KEY(workspace_id,data_mode,project_id) REFERENCES reviewlab.review_projects(workspace_id,data_mode,id) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE reviewlab.review_analysis_runs(workspace_id uuid NOT NULL, data_mode text NOT NULL,
 id uuid NOT NULL,
 project_id uuid NOT NULL,
 filters_json text NOT NULL,
 parameters_json text NOT NULL,
 input_ids_json text NOT NULL,
 results_json text NOT NULL,
 created_at text NOT NULL,
 tool_version text NOT NULL,
 PRIMARY KEY(workspace_id,data_mode,id),
 FOREIGN KEY(workspace_id,data_mode) REFERENCES reviewlab.workspace_states(workspace_id,data_mode),
 FOREIGN KEY(workspace_id,data_mode,project_id) REFERENCES reviewlab.review_projects(workspace_id,data_mode,id) DEFERRABLE INITIALLY DEFERRED
);

CREATE INDEX reviewlab_review_project ON reviewlab.reviews(workspace_id,data_mode,project_id);
CREATE INDEX reviewlab_source_checks ON reviewlab.verification_events(workspace_id,data_mode,source_id,sequence);
CREATE INDEX reviewlab_review_checks ON reviewlab.review_checks(workspace_id,data_mode,review_id,sequence);
ALTER TABLE reviewlab.workspaces ENABLE ROW LEVEL SECURITY;
CREATE POLICY member_read ON reviewlab.workspaces FOR SELECT TO authenticated USING(reviewlab.allowed(id));
GRANT SELECT ON reviewlab.workspaces TO authenticated;
REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON reviewlab.workspaces FROM authenticated,anon;
ALTER TABLE reviewlab.workspace_members ENABLE ROW LEVEL SECURITY;
CREATE POLICY member_read ON reviewlab.workspace_members FOR SELECT TO authenticated USING(reviewlab.allowed(workspace_id));
GRANT SELECT ON reviewlab.workspace_members TO authenticated;
REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON reviewlab.workspace_members FROM authenticated,anon;
ALTER TABLE reviewlab.workspace_states ENABLE ROW LEVEL SECURITY;
CREATE POLICY member_read ON reviewlab.workspace_states FOR SELECT TO authenticated USING(reviewlab.allowed(workspace_id));
GRANT SELECT ON reviewlab.workspace_states TO authenticated;
REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON reviewlab.workspace_states FROM authenticated,anon;
ALTER TABLE reviewlab.commit_receipts ENABLE ROW LEVEL SECURITY;
CREATE POLICY member_read ON reviewlab.commit_receipts FOR SELECT TO authenticated USING(reviewlab.allowed(workspace_id));
GRANT SELECT ON reviewlab.commit_receipts TO authenticated;
REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON reviewlab.commit_receipts FROM authenticated,anon;
ALTER TABLE reviewlab.tags ENABLE ROW LEVEL SECURITY;
CREATE POLICY member_read ON reviewlab.tags FOR SELECT TO authenticated USING(reviewlab.allowed(workspace_id));
GRANT SELECT ON reviewlab.tags TO authenticated;
REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON reviewlab.tags FROM authenticated,anon;
ALTER TABLE reviewlab.sources ENABLE ROW LEVEL SECURITY;
CREATE POLICY member_read ON reviewlab.sources FOR SELECT TO authenticated USING(reviewlab.allowed(workspace_id));
GRANT SELECT ON reviewlab.sources TO authenticated;
REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON reviewlab.sources FROM authenticated,anon;
ALTER TABLE reviewlab.source_tags ENABLE ROW LEVEL SECURITY;
CREATE POLICY member_read ON reviewlab.source_tags FOR SELECT TO authenticated USING(reviewlab.allowed(workspace_id));
GRANT SELECT ON reviewlab.source_tags TO authenticated;
REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON reviewlab.source_tags FROM authenticated,anon;
ALTER TABLE reviewlab.verification_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY member_read ON reviewlab.verification_events FOR SELECT TO authenticated USING(reviewlab.allowed(workspace_id));
GRANT SELECT ON reviewlab.verification_events TO authenticated;
REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON reviewlab.verification_events FROM authenticated,anon;
ALTER TABLE reviewlab.review_projects ENABLE ROW LEVEL SECURITY;
CREATE POLICY member_read ON reviewlab.review_projects FOR SELECT TO authenticated USING(reviewlab.allowed(workspace_id));
GRANT SELECT ON reviewlab.review_projects TO authenticated;
REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON reviewlab.review_projects FROM authenticated,anon;
ALTER TABLE reviewlab.review_imports ENABLE ROW LEVEL SECURITY;
CREATE POLICY member_read ON reviewlab.review_imports FOR SELECT TO authenticated USING(reviewlab.allowed(workspace_id));
GRANT SELECT ON reviewlab.review_imports TO authenticated;
REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON reviewlab.review_imports FROM authenticated,anon;
ALTER TABLE reviewlab.reviews ENABLE ROW LEVEL SECURITY;
CREATE POLICY member_read ON reviewlab.reviews FOR SELECT TO authenticated USING(reviewlab.allowed(workspace_id));
GRANT SELECT ON reviewlab.reviews TO authenticated;
REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON reviewlab.reviews FROM authenticated,anon;
ALTER TABLE reviewlab.review_notes ENABLE ROW LEVEL SECURITY;
CREATE POLICY member_read ON reviewlab.review_notes FOR SELECT TO authenticated USING(reviewlab.allowed(workspace_id));
GRANT SELECT ON reviewlab.review_notes TO authenticated;
REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON reviewlab.review_notes FROM authenticated,anon;
ALTER TABLE reviewlab.review_checks ENABLE ROW LEVEL SECURITY;
CREATE POLICY member_read ON reviewlab.review_checks FOR SELECT TO authenticated USING(reviewlab.allowed(workspace_id));
GRANT SELECT ON reviewlab.review_checks TO authenticated;
REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON reviewlab.review_checks FROM authenticated,anon;
ALTER TABLE reviewlab.review_source_links ENABLE ROW LEVEL SECURITY;
CREATE POLICY member_read ON reviewlab.review_source_links FOR SELECT TO authenticated USING(reviewlab.allowed(workspace_id));
GRANT SELECT ON reviewlab.review_source_links TO authenticated;
REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON reviewlab.review_source_links FROM authenticated,anon;
ALTER TABLE reviewlab.review_analysis_runs ENABLE ROW LEVEL SECURITY;
CREATE POLICY member_read ON reviewlab.review_analysis_runs FOR SELECT TO authenticated USING(reviewlab.allowed(workspace_id));
GRANT SELECT ON reviewlab.review_analysis_runs TO authenticated;
REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON reviewlab.review_analysis_runs FROM authenticated,anon;

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
 SELECT result || jsonb_build_object('tags',coalesce(jsonb_agg(to_jsonb(r)-'workspace_id'-'data_mode'),'[]'::jsonb)) INTO result FROM reviewlab.tags r WHERE workspace_id=p_workspace AND data_mode=p_mode;
 SELECT result || jsonb_build_object('sources',coalesce(jsonb_agg(to_jsonb(r)-'workspace_id'-'data_mode'),'[]'::jsonb)) INTO result FROM reviewlab.sources r WHERE workspace_id=p_workspace AND data_mode=p_mode;
 SELECT result || jsonb_build_object('source_tags',coalesce(jsonb_agg(to_jsonb(r)-'workspace_id'-'data_mode'),'[]'::jsonb)) INTO result FROM reviewlab.source_tags r WHERE workspace_id=p_workspace AND data_mode=p_mode;
 SELECT result || jsonb_build_object('verification_events',coalesce(jsonb_agg(to_jsonb(r)-'workspace_id'-'data_mode'),'[]'::jsonb)) INTO result FROM reviewlab.verification_events r WHERE workspace_id=p_workspace AND data_mode=p_mode;
 SELECT result || jsonb_build_object('review_projects',coalesce(jsonb_agg(to_jsonb(r)-'workspace_id'-'data_mode'),'[]'::jsonb)) INTO result FROM reviewlab.review_projects r WHERE workspace_id=p_workspace AND data_mode=p_mode;
 SELECT result || jsonb_build_object('review_imports',coalesce(jsonb_agg(to_jsonb(r)-'workspace_id'-'data_mode'),'[]'::jsonb)) INTO result FROM reviewlab.review_imports r WHERE workspace_id=p_workspace AND data_mode=p_mode;
 SELECT result || jsonb_build_object('reviews',coalesce(jsonb_agg(to_jsonb(r)-'workspace_id'-'data_mode'),'[]'::jsonb)) INTO result FROM reviewlab.reviews r WHERE workspace_id=p_workspace AND data_mode=p_mode;
 SELECT result || jsonb_build_object('review_notes',coalesce(jsonb_agg(to_jsonb(r)-'workspace_id'-'data_mode'),'[]'::jsonb)) INTO result FROM reviewlab.review_notes r WHERE workspace_id=p_workspace AND data_mode=p_mode;
 SELECT result || jsonb_build_object('review_checks',coalesce(jsonb_agg(to_jsonb(r)-'workspace_id'-'data_mode'),'[]'::jsonb)) INTO result FROM reviewlab.review_checks r WHERE workspace_id=p_workspace AND data_mode=p_mode;
 SELECT result || jsonb_build_object('review_source_links',coalesce(jsonb_agg(to_jsonb(r)-'workspace_id'-'data_mode'),'[]'::jsonb)) INTO result FROM reviewlab.review_source_links r WHERE workspace_id=p_workspace AND data_mode=p_mode;
 SELECT result || jsonb_build_object('review_analysis_runs',coalesce(jsonb_agg(to_jsonb(r)-'workspace_id'-'data_mode'),'[]'::jsonb)) INTO result FROM reviewlab.review_analysis_runs r WHERE workspace_id=p_workspace AND data_mode=p_mode;

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
 IF ARRAY(SELECT jsonb_object_keys(p_data) ORDER BY 1)<>ARRAY['review_analysis_runs','review_checks','review_imports','review_notes','review_projects','review_source_links','reviews','source_tags','sources','tags','verification_events']::text[] THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 IF jsonb_typeof(p_data->'tags') IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 n := n+jsonb_array_length(p_data->'tags');
 FOR item IN SELECT value FROM jsonb_array_elements(p_data->'tags') LOOP
 IF jsonb_typeof(item) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 IF ARRAY(SELECT jsonb_object_keys(item) ORDER BY 1)<>ARRAY['id','name']::text[] THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 END LOOP;
 IF jsonb_typeof(p_data->'sources') IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 n := n+jsonb_array_length(p_data->'sources');
 FOR item IN SELECT value FROM jsonb_array_elements(p_data->'sources') LOOP
 IF jsonb_typeof(item) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 IF ARRAY(SELECT jsonb_object_keys(item) ORDER BY 1)<>ARRAY['authors','created_at','id','notes','original_url','publication_date','purpose','source_type','title','updated_at']::text[] THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 END LOOP;
 IF jsonb_typeof(p_data->'source_tags') IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 n := n+jsonb_array_length(p_data->'source_tags');
 FOR item IN SELECT value FROM jsonb_array_elements(p_data->'source_tags') LOOP
 IF jsonb_typeof(item) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 IF ARRAY(SELECT jsonb_object_keys(item) ORDER BY 1)<>ARRAY['source_id','tag_id']::text[] THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 END LOOP;
 IF jsonb_typeof(p_data->'verification_events') IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 n := n+jsonb_array_length(p_data->'verification_events');
 FOR item IN SELECT value FROM jsonb_array_elements(p_data->'verification_events') LOOP
 IF jsonb_typeof(item) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 IF ARRAY(SELECT jsonb_object_keys(item) ORDER BY 1)<>ARRAY['checked_by','checked_on','evidence','id','memo','recorded_at','sequence','source_id','status']::text[] THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 END LOOP;
 IF jsonb_typeof(p_data->'review_projects') IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 n := n+jsonb_array_length(p_data->'review_projects');
 FOR item IN SELECT value FROM jsonb_array_elements(p_data->'review_projects') LOOP
 IF jsonb_typeof(item) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 IF ARRAY(SELECT jsonb_object_keys(item) ORDER BY 1)<>ARRAY['created_at','data_kind','description','id','name','rating_max','rating_min']::text[] THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 END LOOP;
 IF jsonb_typeof(p_data->'review_imports') IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 n := n+jsonb_array_length(p_data->'review_imports');
 FOR item IN SELECT value FROM jsonb_array_elements(p_data->'review_imports') LOOP
 IF jsonb_typeof(item) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 IF ARRAY(SELECT jsonb_object_keys(item) ORDER BY 1)<>ARRAY['created_at','encoding','file_sha256','id','ignored_columns','imported_rows','mapping_json','project_id','skipped_rows','source_note','total_rows']::text[] THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 END LOOP;
 IF jsonb_typeof(p_data->'reviews') IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 n := n+jsonb_array_length(p_data->'reviews');
 FOR item IN SELECT value FROM jsonb_array_elements(p_data->'reviews') LOOP
 IF jsonb_typeof(item) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 IF ARRAY(SELECT jsonb_object_keys(item) ORDER BY 1)<>ARRAY['ad_disclosed','collected_on','content','created_at','event_mentioned','id','identity_key','import_id','industry','original_url','platform','project_id','rating','raw_json','review_date','review_key','source_row']::text[] THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 END LOOP;
 IF jsonb_typeof(p_data->'review_notes') IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 n := n+jsonb_array_length(p_data->'review_notes');
 FOR item IN SELECT value FROM jsonb_array_elements(p_data->'review_notes') LOOP
 IF jsonb_typeof(item) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 IF ARRAY(SELECT jsonb_object_keys(item) ORDER BY 1)<>ARRAY['id','memo','recorded_at','review_id']::text[] THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 END LOOP;
 IF jsonb_typeof(p_data->'review_checks') IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 n := n+jsonb_array_length(p_data->'review_checks');
 FOR item IN SELECT value FROM jsonb_array_elements(p_data->'review_checks') LOOP
 IF jsonb_typeof(item) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 IF ARRAY(SELECT jsonb_object_keys(item) ORDER BY 1)<>ARRAY['checked_on','evidence','id','memo','recorded_at','review_id','reviewer','sequence','status']::text[] THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 END LOOP;
 IF jsonb_typeof(p_data->'review_source_links') IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 n := n+jsonb_array_length(p_data->'review_source_links');
 FOR item IN SELECT value FROM jsonb_array_elements(p_data->'review_source_links') LOOP
 IF jsonb_typeof(item) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 IF ARRAY(SELECT jsonb_object_keys(item) ORDER BY 1)<>ARRAY['original_url','project_id','referenced_id','source_id','title']::text[] THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 END LOOP;
 IF jsonb_typeof(p_data->'review_analysis_runs') IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 n := n+jsonb_array_length(p_data->'review_analysis_runs');
 FOR item IN SELECT value FROM jsonb_array_elements(p_data->'review_analysis_runs') LOOP
 IF jsonb_typeof(item) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 IF ARRAY(SELECT jsonb_object_keys(item) ORDER BY 1)<>ARRAY['created_at','filters_json','id','input_ids_json','parameters_json','project_id','results_json','tool_version']::text[] THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 END LOOP;
 IF n>50000 THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 SET CONSTRAINTS ALL DEFERRED;
 INSERT INTO reviewlab.tags(workspace_id,data_mode,id,name) SELECT p_workspace,p_mode,x.id,x.name FROM jsonb_to_recordset(p_data->'tags') AS x(id uuid, name text) ON CONFLICT(workspace_id,data_mode,id) DO UPDATE SET name=EXCLUDED.name;
 INSERT INTO reviewlab.sources(workspace_id,data_mode,id,title,authors,publication_date,source_type,original_url,notes,purpose,created_at,updated_at) SELECT p_workspace,p_mode,x.id,x.title,x.authors,x.publication_date,x.source_type,x.original_url,x.notes,x.purpose,x.created_at,x.updated_at FROM jsonb_to_recordset(p_data->'sources') AS x(id uuid, title text, authors text, publication_date text, source_type text, original_url text, notes text, purpose text, created_at text, updated_at text) ON CONFLICT(workspace_id,data_mode,id) DO UPDATE SET title=EXCLUDED.title,authors=EXCLUDED.authors,publication_date=EXCLUDED.publication_date,source_type=EXCLUDED.source_type,original_url=EXCLUDED.original_url,notes=EXCLUDED.notes,purpose=EXCLUDED.purpose,created_at=EXCLUDED.created_at,updated_at=EXCLUDED.updated_at;
 INSERT INTO reviewlab.source_tags(workspace_id,data_mode,source_id,tag_id) SELECT p_workspace,p_mode,x.source_id,x.tag_id FROM jsonb_to_recordset(p_data->'source_tags') AS x(source_id uuid, tag_id uuid) ON CONFLICT(workspace_id,data_mode,source_id,tag_id) DO NOTHING;
 INSERT INTO reviewlab.verification_events(workspace_id,data_mode,sequence,id,source_id,status,checked_on,checked_by,evidence,memo,recorded_at) SELECT p_workspace,p_mode,x.sequence,x.id,x.source_id,x.status,x.checked_on,x.checked_by,x.evidence,x.memo,x.recorded_at FROM jsonb_to_recordset(p_data->'verification_events') AS x(sequence bigint, id uuid, source_id uuid, status text, checked_on text, checked_by text, evidence text, memo text, recorded_at text) ON CONFLICT(workspace_id,data_mode,sequence) DO UPDATE SET id=EXCLUDED.id,source_id=EXCLUDED.source_id,status=EXCLUDED.status,checked_on=EXCLUDED.checked_on,checked_by=EXCLUDED.checked_by,evidence=EXCLUDED.evidence,memo=EXCLUDED.memo,recorded_at=EXCLUDED.recorded_at;
 INSERT INTO reviewlab.review_projects(workspace_id,data_mode,id,name,data_kind,rating_min,rating_max,description,created_at) SELECT p_workspace,p_mode,x.id,x.name,x.data_kind,x.rating_min,x.rating_max,x.description,x.created_at FROM jsonb_to_recordset(p_data->'review_projects') AS x(id uuid, name text, data_kind text, rating_min double precision, rating_max double precision, description text, created_at text) ON CONFLICT(workspace_id,data_mode,id) DO UPDATE SET name=EXCLUDED.name,data_kind=EXCLUDED.data_kind,rating_min=EXCLUDED.rating_min,rating_max=EXCLUDED.rating_max,description=EXCLUDED.description,created_at=EXCLUDED.created_at;
 INSERT INTO reviewlab.review_imports(workspace_id,data_mode,id,project_id,file_sha256,encoding,mapping_json,source_note,total_rows,imported_rows,skipped_rows,ignored_columns,created_at) SELECT p_workspace,p_mode,x.id,x.project_id,x.file_sha256,x.encoding,x.mapping_json,x.source_note,x.total_rows,x.imported_rows,x.skipped_rows,x.ignored_columns,x.created_at FROM jsonb_to_recordset(p_data->'review_imports') AS x(id uuid, project_id uuid, file_sha256 text, encoding text, mapping_json text, source_note text, total_rows bigint, imported_rows bigint, skipped_rows bigint, ignored_columns bigint, created_at text) ON CONFLICT(workspace_id,data_mode,id) DO UPDATE SET project_id=EXCLUDED.project_id,file_sha256=EXCLUDED.file_sha256,encoding=EXCLUDED.encoding,mapping_json=EXCLUDED.mapping_json,source_note=EXCLUDED.source_note,total_rows=EXCLUDED.total_rows,imported_rows=EXCLUDED.imported_rows,skipped_rows=EXCLUDED.skipped_rows,ignored_columns=EXCLUDED.ignored_columns,created_at=EXCLUDED.created_at;
 INSERT INTO reviewlab.reviews(workspace_id,data_mode,id,project_id,import_id,source_row,identity_key,review_key,platform,industry,rating,review_date,content,event_mentioned,ad_disclosed,collected_on,original_url,raw_json,created_at) SELECT p_workspace,p_mode,x.id,x.project_id,x.import_id,x.source_row,x.identity_key,x.review_key,x.platform,x.industry,x.rating,x.review_date,x.content,x.event_mentioned,x.ad_disclosed,x.collected_on,x.original_url,x.raw_json,x.created_at FROM jsonb_to_recordset(p_data->'reviews') AS x(id uuid, project_id uuid, import_id uuid, source_row bigint, identity_key text, review_key text, platform text, industry text, rating double precision, review_date text, content text, event_mentioned text, ad_disclosed text, collected_on text, original_url text, raw_json text, created_at text) ON CONFLICT(workspace_id,data_mode,id) DO UPDATE SET project_id=EXCLUDED.project_id,import_id=EXCLUDED.import_id,source_row=EXCLUDED.source_row,identity_key=EXCLUDED.identity_key,review_key=EXCLUDED.review_key,platform=EXCLUDED.platform,industry=EXCLUDED.industry,rating=EXCLUDED.rating,review_date=EXCLUDED.review_date,content=EXCLUDED.content,event_mentioned=EXCLUDED.event_mentioned,ad_disclosed=EXCLUDED.ad_disclosed,collected_on=EXCLUDED.collected_on,original_url=EXCLUDED.original_url,raw_json=EXCLUDED.raw_json,created_at=EXCLUDED.created_at;
 INSERT INTO reviewlab.review_notes(workspace_id,data_mode,id,review_id,memo,recorded_at) SELECT p_workspace,p_mode,x.id,x.review_id,x.memo,x.recorded_at FROM jsonb_to_recordset(p_data->'review_notes') AS x(id uuid, review_id uuid, memo text, recorded_at text) ON CONFLICT(workspace_id,data_mode,id) DO UPDATE SET review_id=EXCLUDED.review_id,memo=EXCLUDED.memo,recorded_at=EXCLUDED.recorded_at;
 INSERT INTO reviewlab.review_checks(workspace_id,data_mode,sequence,id,review_id,status,reviewer,checked_on,evidence,memo,recorded_at) SELECT p_workspace,p_mode,x.sequence,x.id,x.review_id,x.status,x.reviewer,x.checked_on,x.evidence,x.memo,x.recorded_at FROM jsonb_to_recordset(p_data->'review_checks') AS x(sequence bigint, id uuid, review_id uuid, status text, reviewer text, checked_on text, evidence text, memo text, recorded_at text) ON CONFLICT(workspace_id,data_mode,sequence) DO UPDATE SET id=EXCLUDED.id,review_id=EXCLUDED.review_id,status=EXCLUDED.status,reviewer=EXCLUDED.reviewer,checked_on=EXCLUDED.checked_on,evidence=EXCLUDED.evidence,memo=EXCLUDED.memo,recorded_at=EXCLUDED.recorded_at;
 INSERT INTO reviewlab.review_source_links(workspace_id,data_mode,project_id,referenced_id,source_id,title,original_url) SELECT p_workspace,p_mode,x.project_id,x.referenced_id,x.source_id,x.title,x.original_url FROM jsonb_to_recordset(p_data->'review_source_links') AS x(project_id uuid, referenced_id uuid, source_id uuid, title text, original_url text) ON CONFLICT(workspace_id,data_mode,project_id,referenced_id) DO UPDATE SET source_id=EXCLUDED.source_id,title=EXCLUDED.title,original_url=EXCLUDED.original_url;
 INSERT INTO reviewlab.review_analysis_runs(workspace_id,data_mode,id,project_id,filters_json,parameters_json,input_ids_json,results_json,created_at,tool_version) SELECT p_workspace,p_mode,x.id,x.project_id,x.filters_json,x.parameters_json,x.input_ids_json,x.results_json,x.created_at,x.tool_version FROM jsonb_to_recordset(p_data->'review_analysis_runs') AS x(id uuid, project_id uuid, filters_json text, parameters_json text, input_ids_json text, results_json text, created_at text, tool_version text) ON CONFLICT(workspace_id,data_mode,id) DO UPDATE SET project_id=EXCLUDED.project_id,filters_json=EXCLUDED.filters_json,parameters_json=EXCLUDED.parameters_json,input_ids_json=EXCLUDED.input_ids_json,results_json=EXCLUDED.results_json,created_at=EXCLUDED.created_at,tool_version=EXCLUDED.tool_version;
 DELETE FROM reviewlab.review_analysis_runs r WHERE workspace_id=p_workspace AND data_mode=p_mode AND NOT EXISTS(SELECT 1 FROM jsonb_to_recordset(p_data->'review_analysis_runs') AS x(id uuid, project_id uuid, filters_json text, parameters_json text, input_ids_json text, results_json text, created_at text, tool_version text) WHERE x.id=r.id);
 DELETE FROM reviewlab.review_source_links r WHERE workspace_id=p_workspace AND data_mode=p_mode AND NOT EXISTS(SELECT 1 FROM jsonb_to_recordset(p_data->'review_source_links') AS x(project_id uuid, referenced_id uuid, source_id uuid, title text, original_url text) WHERE x.project_id=r.project_id AND x.referenced_id=r.referenced_id);
 DELETE FROM reviewlab.review_checks r WHERE workspace_id=p_workspace AND data_mode=p_mode AND NOT EXISTS(SELECT 1 FROM jsonb_to_recordset(p_data->'review_checks') AS x(sequence bigint, id uuid, review_id uuid, status text, reviewer text, checked_on text, evidence text, memo text, recorded_at text) WHERE x.sequence=r.sequence);
 DELETE FROM reviewlab.review_notes r WHERE workspace_id=p_workspace AND data_mode=p_mode AND NOT EXISTS(SELECT 1 FROM jsonb_to_recordset(p_data->'review_notes') AS x(id uuid, review_id uuid, memo text, recorded_at text) WHERE x.id=r.id);
 DELETE FROM reviewlab.reviews r WHERE workspace_id=p_workspace AND data_mode=p_mode AND NOT EXISTS(SELECT 1 FROM jsonb_to_recordset(p_data->'reviews') AS x(id uuid, project_id uuid, import_id uuid, source_row bigint, identity_key text, review_key text, platform text, industry text, rating double precision, review_date text, content text, event_mentioned text, ad_disclosed text, collected_on text, original_url text, raw_json text, created_at text) WHERE x.id=r.id);
 DELETE FROM reviewlab.review_imports r WHERE workspace_id=p_workspace AND data_mode=p_mode AND NOT EXISTS(SELECT 1 FROM jsonb_to_recordset(p_data->'review_imports') AS x(id uuid, project_id uuid, file_sha256 text, encoding text, mapping_json text, source_note text, total_rows bigint, imported_rows bigint, skipped_rows bigint, ignored_columns bigint, created_at text) WHERE x.id=r.id);
 DELETE FROM reviewlab.review_projects r WHERE workspace_id=p_workspace AND data_mode=p_mode AND NOT EXISTS(SELECT 1 FROM jsonb_to_recordset(p_data->'review_projects') AS x(id uuid, name text, data_kind text, rating_min double precision, rating_max double precision, description text, created_at text) WHERE x.id=r.id);
 DELETE FROM reviewlab.verification_events r WHERE workspace_id=p_workspace AND data_mode=p_mode AND NOT EXISTS(SELECT 1 FROM jsonb_to_recordset(p_data->'verification_events') AS x(sequence bigint, id uuid, source_id uuid, status text, checked_on text, checked_by text, evidence text, memo text, recorded_at text) WHERE x.sequence=r.sequence);
 DELETE FROM reviewlab.source_tags r WHERE workspace_id=p_workspace AND data_mode=p_mode AND NOT EXISTS(SELECT 1 FROM jsonb_to_recordset(p_data->'source_tags') AS x(source_id uuid, tag_id uuid) WHERE x.source_id=r.source_id AND x.tag_id=r.tag_id);
 DELETE FROM reviewlab.sources r WHERE workspace_id=p_workspace AND data_mode=p_mode AND NOT EXISTS(SELECT 1 FROM jsonb_to_recordset(p_data->'sources') AS x(id uuid, title text, authors text, publication_date text, source_type text, original_url text, notes text, purpose text, created_at text, updated_at text) WHERE x.id=r.id);
 DELETE FROM reviewlab.tags r WHERE workspace_id=p_workspace AND data_mode=p_mode AND NOT EXISTS(SELECT 1 FROM jsonb_to_recordset(p_data->'tags') AS x(id uuid, name text) WHERE x.id=r.id);

 SET CONSTRAINTS ALL IMMEDIATE;
 UPDATE reviewlab.workspace_states SET revision=rev+1,changed_at=now() WHERE workspace_id=p_workspace AND data_mode=p_mode;
 INSERT INTO reviewlab.commit_receipts(workspace_id,data_mode,operation_id,payload_hash,revision,actor) VALUES(p_workspace,p_mode,p_operation,digest,rev+1,auth.uid());
 RETURN jsonb_build_object('revision',rev+1,'operation_id',p_operation,'replayed',false);
END; $$;
REVOKE ALL ON FUNCTION public.reviewlab_workspaces(), public.reviewlab_create_workspace(text), public.reviewlab_status(uuid,text), public.reviewlab_read(uuid,text), public.reviewlab_commit(uuid,text,bigint,uuid,jsonb) FROM PUBLIC,anon;
GRANT EXECUTE ON FUNCTION public.reviewlab_workspaces(), public.reviewlab_create_workspace(text), public.reviewlab_status(uuid,text), public.reviewlab_read(uuid,text), public.reviewlab_commit(uuid,text,bigint,uuid,jsonb) TO authenticated;
COMMENT ON FUNCTION public.reviewlab_commit(uuid,text,bigint,uuid,jsonb) IS 'Authenticated member-only atomic CAS commit; service_role not used. New tables require a versioned migration.';
COMMIT;
