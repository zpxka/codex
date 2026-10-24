-- ReviewLab Supabase v2: v1 이후 1회 적용. 기존 데이터 삭제/초기화 없음.
BEGIN;
DO $$ BEGIN IF (SELECT max(version) FROM reviewlab.schema_migrations) IS DISTINCT FROM 1 THEN RAISE EXCEPTION 'Expected schema v1'; END IF; END; $$;
CREATE TABLE reviewlab.survey_projects(workspace_id uuid NOT NULL, data_mode text NOT NULL,
 id uuid NOT NULL,
 name text NOT NULL,
 purpose text NOT NULL,
 period_start text NOT NULL,
 period_end text NOT NULL,
 sample text NOT NULL,
 method text NOT NULL,
 memo text NOT NULL,
 data_kind text NOT NULL,
 created_at text NOT NULL,
 PRIMARY KEY(workspace_id,data_mode,id),
 FOREIGN KEY(workspace_id,data_mode) REFERENCES reviewlab.workspace_states(workspace_id,data_mode)
);
ALTER TABLE reviewlab.survey_projects ENABLE ROW LEVEL SECURITY;
CREATE POLICY member_read ON reviewlab.survey_projects FOR SELECT TO authenticated USING(reviewlab.allowed(workspace_id));
GRANT SELECT ON reviewlab.survey_projects TO authenticated;
REVOKE ALL ON reviewlab.survey_projects FROM anon;
REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON reviewlab.survey_projects FROM authenticated;
CREATE TABLE reviewlab.survey_questions(workspace_id uuid NOT NULL, data_mode text NOT NULL,
 id uuid NOT NULL,
 project_id uuid NOT NULL,
 name text NOT NULL,
 variable text NOT NULL,
 config_json text NOT NULL,
 PRIMARY KEY(workspace_id,data_mode,id),
 FOREIGN KEY(workspace_id,data_mode) REFERENCES reviewlab.workspace_states(workspace_id,data_mode),
 FOREIGN KEY(workspace_id,data_mode,project_id) REFERENCES reviewlab.survey_projects(workspace_id,data_mode,id) DEFERRABLE INITIALLY DEFERRED
);
ALTER TABLE reviewlab.survey_questions ENABLE ROW LEVEL SECURITY;
CREATE POLICY member_read ON reviewlab.survey_questions FOR SELECT TO authenticated USING(reviewlab.allowed(workspace_id));
GRANT SELECT ON reviewlab.survey_questions TO authenticated;
REVOKE ALL ON reviewlab.survey_questions FROM anon;
REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON reviewlab.survey_questions FROM authenticated;
CREATE TABLE reviewlab.survey_imports(workspace_id uuid NOT NULL, data_mode text NOT NULL,
 id uuid NOT NULL,
 project_id uuid NOT NULL,
 file_sha256 text NOT NULL,
 settings_json text NOT NULL,
 created_at text NOT NULL,
 PRIMARY KEY(workspace_id,data_mode,id),
 FOREIGN KEY(workspace_id,data_mode) REFERENCES reviewlab.workspace_states(workspace_id,data_mode),
 FOREIGN KEY(workspace_id,data_mode,project_id) REFERENCES reviewlab.survey_projects(workspace_id,data_mode,id) DEFERRABLE INITIALLY DEFERRED
);
ALTER TABLE reviewlab.survey_imports ENABLE ROW LEVEL SECURITY;
CREATE POLICY member_read ON reviewlab.survey_imports FOR SELECT TO authenticated USING(reviewlab.allowed(workspace_id));
GRANT SELECT ON reviewlab.survey_imports TO authenticated;
REVOKE ALL ON reviewlab.survey_imports FROM anon;
REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON reviewlab.survey_imports FROM authenticated;
CREATE TABLE reviewlab.survey_responses(workspace_id uuid NOT NULL, data_mode text NOT NULL,
 id uuid NOT NULL,
 project_id uuid NOT NULL,
 import_id uuid NOT NULL,
 source_row bigint NOT NULL,
 created_at text NOT NULL,
 PRIMARY KEY(workspace_id,data_mode,id),
 FOREIGN KEY(workspace_id,data_mode) REFERENCES reviewlab.workspace_states(workspace_id,data_mode),
 FOREIGN KEY(workspace_id,data_mode,import_id) REFERENCES reviewlab.survey_imports(workspace_id,data_mode,id) DEFERRABLE INITIALLY DEFERRED,
 FOREIGN KEY(workspace_id,data_mode,project_id) REFERENCES reviewlab.survey_projects(workspace_id,data_mode,id) DEFERRABLE INITIALLY DEFERRED
);
ALTER TABLE reviewlab.survey_responses ENABLE ROW LEVEL SECURITY;
CREATE POLICY member_read ON reviewlab.survey_responses FOR SELECT TO authenticated USING(reviewlab.allowed(workspace_id));
GRANT SELECT ON reviewlab.survey_responses TO authenticated;
REVOKE ALL ON reviewlab.survey_responses FROM anon;
REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON reviewlab.survey_responses FROM authenticated;
CREATE TABLE reviewlab.survey_answers(workspace_id uuid NOT NULL, data_mode text NOT NULL,
 id uuid NOT NULL,
 response_id uuid NOT NULL,
 question_id uuid NOT NULL,
 raw_value text NOT NULL,
 PRIMARY KEY(workspace_id,data_mode,id),
 FOREIGN KEY(workspace_id,data_mode) REFERENCES reviewlab.workspace_states(workspace_id,data_mode),
 FOREIGN KEY(workspace_id,data_mode,question_id) REFERENCES reviewlab.survey_questions(workspace_id,data_mode,id) DEFERRABLE INITIALLY DEFERRED,
 FOREIGN KEY(workspace_id,data_mode,response_id) REFERENCES reviewlab.survey_responses(workspace_id,data_mode,id) DEFERRABLE INITIALLY DEFERRED
);
ALTER TABLE reviewlab.survey_answers ENABLE ROW LEVEL SECURITY;
CREATE POLICY member_read ON reviewlab.survey_answers FOR SELECT TO authenticated USING(reviewlab.allowed(workspace_id));
GRANT SELECT ON reviewlab.survey_answers TO authenticated;
REVOKE ALL ON reviewlab.survey_answers FROM anon;
REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON reviewlab.survey_answers FROM authenticated;
CREATE TABLE reviewlab.survey_analysis_runs(workspace_id uuid NOT NULL, data_mode text NOT NULL,
 id uuid NOT NULL,
 project_id uuid NOT NULL,
 config_json text NOT NULL,
 results_json text NOT NULL,
 created_at text NOT NULL,
 PRIMARY KEY(workspace_id,data_mode,id),
 FOREIGN KEY(workspace_id,data_mode) REFERENCES reviewlab.workspace_states(workspace_id,data_mode),
 FOREIGN KEY(workspace_id,data_mode,project_id) REFERENCES reviewlab.survey_projects(workspace_id,data_mode,id) DEFERRABLE INITIALLY DEFERRED
);
ALTER TABLE reviewlab.survey_analysis_runs ENABLE ROW LEVEL SECURITY;
CREATE POLICY member_read ON reviewlab.survey_analysis_runs FOR SELECT TO authenticated USING(reviewlab.allowed(workspace_id));
GRANT SELECT ON reviewlab.survey_analysis_runs TO authenticated;
REVOKE ALL ON reviewlab.survey_analysis_runs FROM anon;
REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON reviewlab.survey_analysis_runs FROM authenticated;
ALTER TABLE reviewlab.survey_projects ADD CHECK(data_kind=data_mode);
ALTER TABLE reviewlab.survey_questions ADD UNIQUE(workspace_id,data_mode,project_id,variable);
ALTER TABLE reviewlab.survey_imports ADD UNIQUE(workspace_id,data_mode,project_id,file_sha256);
ALTER TABLE reviewlab.survey_answers ADD UNIQUE(workspace_id,data_mode,response_id,question_id);
ALTER TABLE reviewlab.survey_responses ADD CHECK(source_row>=2);
CREATE FUNCTION public.reviewlab_workspaces_v2() RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,reviewlab AS $$
 SELECT jsonb_build_object('schema_version',2,'workspaces',coalesce(jsonb_agg(jsonb_build_object('id',w.id,'name',w.name) ORDER BY w.created_at,w.id),'[]'::jsonb)) FROM reviewlab.workspaces w WHERE reviewlab.allowed(w.id);
$$;
CREATE OR REPLACE FUNCTION public.reviewlab_workspaces() RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,reviewlab AS $$
 SELECT jsonb_build_object('schema_version',2,'workspaces',coalesce(jsonb_agg(jsonb_build_object('id',w.id,'name',w.name) ORDER BY w.created_at,w.id),'[]'::jsonb)) FROM reviewlab.workspaces w WHERE reviewlab.allowed(w.id);
$$;
CREATE FUNCTION public.reviewlab_status_v2(p_workspace uuid,p_mode text) RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,reviewlab AS $$
DECLARE result jsonb;
BEGIN
 IF p_mode IS NULL OR p_mode NOT IN ('actual','example') OR NOT reviewlab.allowed(p_workspace) THEN RAISE EXCEPTION 'RL_FORBIDDEN' USING ERRCODE='42501'; END IF;
 SELECT jsonb_build_object('schema_version',2,'revision',revision,'changed_at',changed_at) INTO result FROM reviewlab.workspace_states WHERE workspace_id=p_workspace AND data_mode=p_mode;
 RETURN result;
END; $$;
CREATE OR REPLACE FUNCTION public.reviewlab_status(p_workspace uuid,p_mode text) RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,reviewlab AS $$
DECLARE result jsonb;
BEGIN
 IF p_mode IS NULL OR p_mode NOT IN ('actual','example') OR NOT reviewlab.allowed(p_workspace) THEN RAISE EXCEPTION 'RL_FORBIDDEN' USING ERRCODE='42501'; END IF;
 SELECT jsonb_build_object('schema_version',2,'revision',revision,'changed_at',changed_at) INTO result FROM reviewlab.workspace_states WHERE workspace_id=p_workspace AND data_mode=p_mode;
 RETURN result;
END; $$;
CREATE FUNCTION public.reviewlab_read_v2(p_workspace uuid,p_mode text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,reviewlab AS $$
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
 SELECT result || jsonb_build_object('survey_projects',coalesce(jsonb_agg(to_jsonb(r)-'workspace_id'-'data_mode'),'[]'::jsonb)) INTO result FROM reviewlab.survey_projects r WHERE workspace_id=p_workspace AND data_mode=p_mode;
 SELECT result || jsonb_build_object('survey_questions',coalesce(jsonb_agg(to_jsonb(r)-'workspace_id'-'data_mode'),'[]'::jsonb)) INTO result FROM reviewlab.survey_questions r WHERE workspace_id=p_workspace AND data_mode=p_mode;
 SELECT result || jsonb_build_object('survey_imports',coalesce(jsonb_agg(to_jsonb(r)-'workspace_id'-'data_mode'),'[]'::jsonb)) INTO result FROM reviewlab.survey_imports r WHERE workspace_id=p_workspace AND data_mode=p_mode;
 SELECT result || jsonb_build_object('survey_responses',coalesce(jsonb_agg(to_jsonb(r)-'workspace_id'-'data_mode'),'[]'::jsonb)) INTO result FROM reviewlab.survey_responses r WHERE workspace_id=p_workspace AND data_mode=p_mode;
 SELECT result || jsonb_build_object('survey_answers',coalesce(jsonb_agg(to_jsonb(r)-'workspace_id'-'data_mode'),'[]'::jsonb)) INTO result FROM reviewlab.survey_answers r WHERE workspace_id=p_workspace AND data_mode=p_mode;
 SELECT result || jsonb_build_object('survey_analysis_runs',coalesce(jsonb_agg(to_jsonb(r)-'workspace_id'-'data_mode'),'[]'::jsonb)) INTO result FROM reviewlab.survey_analysis_runs r WHERE workspace_id=p_workspace AND data_mode=p_mode;

 RETURN jsonb_build_object('schema_version',2,'workspace_id',p_workspace,'mode',p_mode,'revision',rev,'data',result);
END; $$;
CREATE OR REPLACE FUNCTION public.reviewlab_read(p_workspace uuid,p_mode text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,reviewlab AS $$
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
 SELECT result || jsonb_build_object('survey_projects',coalesce(jsonb_agg(to_jsonb(r)-'workspace_id'-'data_mode'),'[]'::jsonb)) INTO result FROM reviewlab.survey_projects r WHERE workspace_id=p_workspace AND data_mode=p_mode;
 SELECT result || jsonb_build_object('survey_questions',coalesce(jsonb_agg(to_jsonb(r)-'workspace_id'-'data_mode'),'[]'::jsonb)) INTO result FROM reviewlab.survey_questions r WHERE workspace_id=p_workspace AND data_mode=p_mode;
 SELECT result || jsonb_build_object('survey_imports',coalesce(jsonb_agg(to_jsonb(r)-'workspace_id'-'data_mode'),'[]'::jsonb)) INTO result FROM reviewlab.survey_imports r WHERE workspace_id=p_workspace AND data_mode=p_mode;
 SELECT result || jsonb_build_object('survey_responses',coalesce(jsonb_agg(to_jsonb(r)-'workspace_id'-'data_mode'),'[]'::jsonb)) INTO result FROM reviewlab.survey_responses r WHERE workspace_id=p_workspace AND data_mode=p_mode;
 SELECT result || jsonb_build_object('survey_answers',coalesce(jsonb_agg(to_jsonb(r)-'workspace_id'-'data_mode'),'[]'::jsonb)) INTO result FROM reviewlab.survey_answers r WHERE workspace_id=p_workspace AND data_mode=p_mode;
 SELECT result || jsonb_build_object('survey_analysis_runs',coalesce(jsonb_agg(to_jsonb(r)-'workspace_id'-'data_mode'),'[]'::jsonb)) INTO result FROM reviewlab.survey_analysis_runs r WHERE workspace_id=p_workspace AND data_mode=p_mode;

 RETURN jsonb_build_object('schema_version',2,'workspace_id',p_workspace,'mode',p_mode,'revision',rev,'data',result);
END; $$;
CREATE FUNCTION public.reviewlab_commit_v2(p_workspace uuid,p_mode text,p_expected_revision bigint,p_operation uuid,p_data jsonb) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,reviewlab AS $$
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
 IF ARRAY(SELECT jsonb_object_keys(p_data) ORDER BY 1)<>ARRAY['review_analysis_runs','review_checks','review_imports','review_notes','review_projects','review_source_links','reviews','source_tags','sources','survey_analysis_runs','survey_answers','survey_imports','survey_projects','survey_questions','survey_responses','tags','verification_events']::text[] THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
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
 IF jsonb_typeof(p_data->'survey_projects') IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 n := n+jsonb_array_length(p_data->'survey_projects');
 FOR item IN SELECT value FROM jsonb_array_elements(p_data->'survey_projects') LOOP
 IF jsonb_typeof(item) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 IF ARRAY(SELECT jsonb_object_keys(item) ORDER BY 1)<>ARRAY['created_at','data_kind','id','memo','method','name','period_end','period_start','purpose','sample']::text[] THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 END LOOP;
 IF jsonb_typeof(p_data->'survey_questions') IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 n := n+jsonb_array_length(p_data->'survey_questions');
 FOR item IN SELECT value FROM jsonb_array_elements(p_data->'survey_questions') LOOP
 IF jsonb_typeof(item) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 IF ARRAY(SELECT jsonb_object_keys(item) ORDER BY 1)<>ARRAY['config_json','id','name','project_id','variable']::text[] THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 END LOOP;
 IF jsonb_typeof(p_data->'survey_imports') IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 n := n+jsonb_array_length(p_data->'survey_imports');
 FOR item IN SELECT value FROM jsonb_array_elements(p_data->'survey_imports') LOOP
 IF jsonb_typeof(item) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 IF ARRAY(SELECT jsonb_object_keys(item) ORDER BY 1)<>ARRAY['created_at','file_sha256','id','project_id','settings_json']::text[] THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 END LOOP;
 IF jsonb_typeof(p_data->'survey_responses') IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 n := n+jsonb_array_length(p_data->'survey_responses');
 FOR item IN SELECT value FROM jsonb_array_elements(p_data->'survey_responses') LOOP
 IF jsonb_typeof(item) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 IF ARRAY(SELECT jsonb_object_keys(item) ORDER BY 1)<>ARRAY['created_at','id','import_id','project_id','source_row']::text[] THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 END LOOP;
 IF jsonb_typeof(p_data->'survey_answers') IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 n := n+jsonb_array_length(p_data->'survey_answers');
 FOR item IN SELECT value FROM jsonb_array_elements(p_data->'survey_answers') LOOP
 IF jsonb_typeof(item) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 IF ARRAY(SELECT jsonb_object_keys(item) ORDER BY 1)<>ARRAY['id','question_id','raw_value','response_id']::text[] THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 END LOOP;
 IF jsonb_typeof(p_data->'survey_analysis_runs') IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 n := n+jsonb_array_length(p_data->'survey_analysis_runs');
 FOR item IN SELECT value FROM jsonb_array_elements(p_data->'survey_analysis_runs') LOOP
 IF jsonb_typeof(item) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
 IF ARRAY(SELECT jsonb_object_keys(item) ORDER BY 1)<>ARRAY['config_json','created_at','id','project_id','results_json']::text[] THEN RAISE EXCEPTION 'RL_INVALID_DATA' USING ERRCODE='22023'; END IF;
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
 INSERT INTO reviewlab.survey_projects(workspace_id,data_mode,id,name,purpose,period_start,period_end,sample,method,memo,data_kind,created_at) SELECT p_workspace,p_mode,x.id,x.name,x.purpose,x.period_start,x.period_end,x.sample,x.method,x.memo,x.data_kind,x.created_at FROM jsonb_to_recordset(p_data->'survey_projects') AS x(id uuid, name text, purpose text, period_start text, period_end text, sample text, method text, memo text, data_kind text, created_at text) ON CONFLICT(workspace_id,data_mode,id) DO UPDATE SET name=EXCLUDED.name,purpose=EXCLUDED.purpose,period_start=EXCLUDED.period_start,period_end=EXCLUDED.period_end,sample=EXCLUDED.sample,method=EXCLUDED.method,memo=EXCLUDED.memo,data_kind=EXCLUDED.data_kind,created_at=EXCLUDED.created_at;
 INSERT INTO reviewlab.survey_questions(workspace_id,data_mode,id,project_id,name,variable,config_json) SELECT p_workspace,p_mode,x.id,x.project_id,x.name,x.variable,x.config_json FROM jsonb_to_recordset(p_data->'survey_questions') AS x(id uuid, project_id uuid, name text, variable text, config_json text) ON CONFLICT(workspace_id,data_mode,id) DO UPDATE SET project_id=EXCLUDED.project_id,name=EXCLUDED.name,variable=EXCLUDED.variable,config_json=EXCLUDED.config_json;
 INSERT INTO reviewlab.survey_imports(workspace_id,data_mode,id,project_id,file_sha256,settings_json,created_at) SELECT p_workspace,p_mode,x.id,x.project_id,x.file_sha256,x.settings_json,x.created_at FROM jsonb_to_recordset(p_data->'survey_imports') AS x(id uuid, project_id uuid, file_sha256 text, settings_json text, created_at text) ON CONFLICT(workspace_id,data_mode,id) DO UPDATE SET project_id=EXCLUDED.project_id,file_sha256=EXCLUDED.file_sha256,settings_json=EXCLUDED.settings_json,created_at=EXCLUDED.created_at;
 INSERT INTO reviewlab.survey_responses(workspace_id,data_mode,id,project_id,import_id,source_row,created_at) SELECT p_workspace,p_mode,x.id,x.project_id,x.import_id,x.source_row,x.created_at FROM jsonb_to_recordset(p_data->'survey_responses') AS x(id uuid, project_id uuid, import_id uuid, source_row bigint, created_at text) ON CONFLICT(workspace_id,data_mode,id) DO UPDATE SET project_id=EXCLUDED.project_id,import_id=EXCLUDED.import_id,source_row=EXCLUDED.source_row,created_at=EXCLUDED.created_at;
 INSERT INTO reviewlab.survey_answers(workspace_id,data_mode,id,response_id,question_id,raw_value) SELECT p_workspace,p_mode,x.id,x.response_id,x.question_id,x.raw_value FROM jsonb_to_recordset(p_data->'survey_answers') AS x(id uuid, response_id uuid, question_id uuid, raw_value text) ON CONFLICT(workspace_id,data_mode,id) DO UPDATE SET response_id=EXCLUDED.response_id,question_id=EXCLUDED.question_id,raw_value=EXCLUDED.raw_value;
 INSERT INTO reviewlab.survey_analysis_runs(workspace_id,data_mode,id,project_id,config_json,results_json,created_at) SELECT p_workspace,p_mode,x.id,x.project_id,x.config_json,x.results_json,x.created_at FROM jsonb_to_recordset(p_data->'survey_analysis_runs') AS x(id uuid, project_id uuid, config_json text, results_json text, created_at text) ON CONFLICT(workspace_id,data_mode,id) DO UPDATE SET project_id=EXCLUDED.project_id,config_json=EXCLUDED.config_json,results_json=EXCLUDED.results_json,created_at=EXCLUDED.created_at;
 DELETE FROM reviewlab.survey_analysis_runs r WHERE workspace_id=p_workspace AND data_mode=p_mode AND NOT EXISTS(SELECT 1 FROM jsonb_to_recordset(p_data->'survey_analysis_runs') AS x(id uuid, project_id uuid, config_json text, results_json text, created_at text) WHERE x.id=r.id);
 DELETE FROM reviewlab.survey_answers r WHERE workspace_id=p_workspace AND data_mode=p_mode AND NOT EXISTS(SELECT 1 FROM jsonb_to_recordset(p_data->'survey_answers') AS x(id uuid, response_id uuid, question_id uuid, raw_value text) WHERE x.id=r.id);
 DELETE FROM reviewlab.survey_responses r WHERE workspace_id=p_workspace AND data_mode=p_mode AND NOT EXISTS(SELECT 1 FROM jsonb_to_recordset(p_data->'survey_responses') AS x(id uuid, project_id uuid, import_id uuid, source_row bigint, created_at text) WHERE x.id=r.id);
 DELETE FROM reviewlab.survey_imports r WHERE workspace_id=p_workspace AND data_mode=p_mode AND NOT EXISTS(SELECT 1 FROM jsonb_to_recordset(p_data->'survey_imports') AS x(id uuid, project_id uuid, file_sha256 text, settings_json text, created_at text) WHERE x.id=r.id);
 DELETE FROM reviewlab.survey_questions r WHERE workspace_id=p_workspace AND data_mode=p_mode AND NOT EXISTS(SELECT 1 FROM jsonb_to_recordset(p_data->'survey_questions') AS x(id uuid, project_id uuid, name text, variable text, config_json text) WHERE x.id=r.id);
 DELETE FROM reviewlab.survey_projects r WHERE workspace_id=p_workspace AND data_mode=p_mode AND NOT EXISTS(SELECT 1 FROM jsonb_to_recordset(p_data->'survey_projects') AS x(id uuid, name text, purpose text, period_start text, period_end text, sample text, method text, memo text, data_kind text, created_at text) WHERE x.id=r.id);
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

 IF EXISTS(SELECT 1 FROM reviewlab.survey_answers a JOIN reviewlab.survey_responses r ON r.workspace_id=a.workspace_id AND r.data_mode=a.data_mode AND r.id=a.response_id JOIN reviewlab.survey_questions q ON q.workspace_id=a.workspace_id AND q.data_mode=a.data_mode AND q.id=a.question_id WHERE a.workspace_id=p_workspace AND a.data_mode=p_mode AND q.project_id<>r.project_id) OR EXISTS(SELECT 1 FROM reviewlab.survey_responses r JOIN reviewlab.survey_imports i ON i.workspace_id=r.workspace_id AND i.data_mode=r.data_mode AND i.id=r.import_id WHERE r.workspace_id=p_workspace AND r.data_mode=p_mode AND r.project_id<>i.project_id) THEN RAISE EXCEPTION 'RL_SURVEY_PROJECT_MISMATCH' USING ERRCODE='22023'; END IF;
 SET CONSTRAINTS ALL IMMEDIATE;
 UPDATE reviewlab.workspace_states SET revision=rev+1,changed_at=now() WHERE workspace_id=p_workspace AND data_mode=p_mode;
 INSERT INTO reviewlab.commit_receipts(workspace_id,data_mode,operation_id,payload_hash,revision,actor) VALUES(p_workspace,p_mode,p_operation,digest,rev+1,auth.uid());
 RETURN jsonb_build_object('revision',rev+1,'operation_id',p_operation,'replayed',false);
END; $$;

CREATE OR REPLACE FUNCTION public.reviewlab_commit(p_workspace uuid,p_mode text,p_expected_revision bigint,p_operation uuid,p_data jsonb) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,reviewlab AS $$
BEGIN
 IF NOT reviewlab.allowed(p_workspace) THEN RAISE EXCEPTION 'RL_FORBIDDEN' USING ERRCODE='42501'; END IF;
 RAISE EXCEPTION 'RL_SCHEMA_UPGRADE_REQUIRED' USING ERRCODE='22023';
END; $$;
REVOKE ALL ON FUNCTION public.reviewlab_workspaces_v2(), public.reviewlab_status_v2(uuid,text), public.reviewlab_read_v2(uuid,text), public.reviewlab_commit_v2(uuid,text,bigint,uuid,jsonb) FROM PUBLIC,anon;
GRANT EXECUTE ON FUNCTION public.reviewlab_workspaces_v2(), public.reviewlab_status_v2(uuid,text), public.reviewlab_read_v2(uuid,text), public.reviewlab_commit_v2(uuid,text,bigint,uuid,jsonb) TO authenticated;
INSERT INTO reviewlab.schema_migrations VALUES(2,now());
COMMIT;
