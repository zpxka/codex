-- 별도 승인 후 적용. 기존 bucket이 있으면 자동 수정하지 않음.
BEGIN;
INSERT INTO storage.buckets(id,name,public,file_size_limit,allowed_mime_types)
VALUES('reviewlab-originals','reviewlab-originals',false,10485760,ARRAY['application/octet-stream']);
CREATE FUNCTION reviewlab.file_allowed(path text) RETURNS boolean LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,reviewlab AS $$
BEGIN
 IF path !~ '^[0-9a-f-]{36}/(actual|example)/[0-9a-f]{64}$' THEN RETURN false; END IF;
 RETURN reviewlab.allowed(split_part(path,'/',1)::uuid);
EXCEPTION WHEN invalid_text_representation THEN RETURN false;
END; $$;
REVOKE ALL ON FUNCTION reviewlab.file_allowed(text) FROM PUBLIC,anon;
GRANT EXECUTE ON FUNCTION reviewlab.file_allowed(text) TO authenticated;
CREATE POLICY reviewlab_files_read ON storage.objects FOR SELECT TO authenticated USING(bucket_id='reviewlab-originals' AND reviewlab.file_allowed(name));
CREATE POLICY reviewlab_files_insert ON storage.objects FOR INSERT TO authenticated WITH CHECK(bucket_id='reviewlab-originals' AND reviewlab.file_allowed(name));
-- UPDATE/DELETE 정책 없음: 파일 덮어쓰기/앱 자동 삭제를 허용하지 않음.
COMMIT;
