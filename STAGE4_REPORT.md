# ReviewLab 4단계 개발 결과 — 2026-10-28(KST)

## 구현 결과

기존 참고문헌·리뷰·설문과 선택적 Supabase 구조를 유지하면서 **원문·논문 검토** 메뉴를 추가했다. 운영 자료·백업 및 Supabase SQL/Storage/RLS는 변경하지 않았다. 코드 검증과 실제 운영 배포는 구분한다.

| 영역 | 구현된 기능 | 한계 |
| --- | --- | --- |
| 4-A 원문 | 기존 참고문헌 연결, PDF/DOCX/UTF-8 TXT 추출, 페이지/문단/표 위치, 한글 검색, 해시 중복 검사, 불변 버전, 원본 다운로드 | PDF 200페이지/파일 10MB/추출 JSON 1MB. OCR·암호 해제 없음. 부분 추출도 분석 차단 |
| 4-B 분석 | 연구 목적/질문/방법·대상/변수/결과/한계/관련성 수동 기록, 문헌별 비교표, 공통·차이·표본 조건 기록, 실제 근거·범위·확인 이력 | 단어 포함 검색. 결론·해석은 연구자가 입력하며 자동 의미 분석·학술적 진위 확정 없음 |
| AI 모듈 | 기본 꺼짐, 선택 구절 미리보기와 매 호출 승인, 인용 정확성/위치/범위 검사, 비용·호출 한도, 실패 처리 | 유료 API 실접속 미검증. AI는 인용 탐색만 수행. 별도 AI 사용 이력 메뉴 없음 |
| 4-C 초고 | 파일/직접 입력, 문장 추가·삭제·수정 비교, 인용 누락·일반화·인과·표현·구조 검토 제안, 초고와 문헌 근거 연결, 연구자 판단 추가 기록 | 완전한 맞춤법 사전·논리 판정 없음. 수정·주장·논문 본문 자동 작성 없음 |
| 저장·복구 | SQLite/클라우드 메타데이터, 별도 불변 파일 저장, 전체 ZIP 검증·추가 복원, 기존 CAS/요청 ID 보호 | 파일/DB 분산 원자성 없음. 실패한 업로드의 고아 파일은 자동 삭제하지 않음. 자동 파일 백업 미구현 |

## 구조와 데이터 보호

SQLite **v4**는 `document_files`, `document_notes`만 추가한다. 원문/초고는 kind와 불변 ID, 원본/추출 해시로 구분한다. 분석·근거·범위·연구자 확인 상태는 원문과 별도 행에 저장하고 기존 판단을 덮어쓰지 않는다. 참고문헌 삭제 시 서지 스냅샷과 원문/분석을 유지한다. 기존 테이블은 초기화하지 않으며 SQLite 이전 전 사본과 실패 롤백을 사용한다.

Supabase **v3**는 두 테이블, v3 RPC와 RLS를 추가한다. 이전 v2 클라이언트 저장을 차단해 새 자료의 손상을 방지한다. 실제/예시 및 workspace 구분, 모드 전체 CAS, 미확인 요청 자동 재전송 금지를 유지한다. 기존 20MB/50,000개 레코드 한도는 메타데이터·인용에 적용되며, 원본/추출 JSON은 별도 파일 저장소에 둔다.

공용 파일은 `reviewlab-originals/<workspace>/<actual|example>/<sha256>` 경로의 private Storage를 사용한다. 사용자 JWT, 구성원 SELECT/INSERT, 해시 확인, 10MB 다운로드 제한을 적용한다. 공개 URL 및 앱의 덮어쓰기/삭제 기능은 없다. 로컬 파일은 모드별 DB 옆 documents 폴더에 둔다.

## 필요한 설정 및 승인 순서

새 SQLite SQL: `reviewlab/db/migrations/004_documents.sql` — 앱이 사전 백업 후 적용.

새 Supabase SQL: `supabase/migrations/202610270003_documents.sql`, 별도 `supabase/storage_documents.sql` — **운영에는 미적용**.

1. 현재 데이터와 pending 상태를 확인하고 actual/example 백업을 보관한다. 두 기기의 앱을 종료한다.
2. 테스트 프로젝트에서 기존 v1 → 설문 v2 → 원문 v3를 순서대로 검증한다. 기존 프로젝트에서 이미 적용한 v1/v2를 다시 실행하지 않는다.
3. 관리자가 v3 SQL, private bucket·RLS 정책, 백업/복구 방법을 검토하고 운영 적용을 별도로 승인한다. 다른 기존 Storage 정책이 접근을 넓히지 않는지도 확인한다.
4. 두 컴퓨터를 함께 새 코드로 업데이트하고 예시 모드의 업로드→다른 기기 조회→ZIP 복원을 확인한 뒤 실제 사용한다. 기존 참고문헌을 다시 이전하지 않는다.

승인과 테스트 프로젝트 검증이 끝나기 전에는 운영 전환을 진행하지 않는다. 정확한 절차는 [DOCUMENTS_GUIDE.md](DOCUMENTS_GUIDE.md)에 있다.

## AI 설정과 비용

`OPENAI_API_KEY`를 환경변수 또는 secrets.toml 최상위에 설정한다. 키가 있어도 기본 전송은 꺼져 있다. 화면에서 제공사 최신 토큰 요금과 일간 예상 비용 한도를 입력하고, 매 호출 전 선택 범위·개인정보·전송 비용을 확인한다. OpenAI 호출 시 비용이 발생하며 Storage 업로드/다운로드도 별도 요금이 발생할 수 있다. 실제 요금이나 유료 연결 성공을 확인한 것은 아니다. 두 기기의 합산 과금 한도는 제공사에서 설정해야 한다.

## 실제 테스트 결과

**266 통과 / 0 실패 / 0 미실행**, Linux/Python 3.12.14/pytest 8.4.2/임시 PostgreSQL 16. 기존 242개와 새 24개 검사를 포함한다. 기본 테스트의 외부 HTTP는 차단되고 Auth/Storage HTTP/AI는 대역을 사용한다. SQL/RLS는 임시 PostgreSQL에서 실제 실행했다.

전체 명령과 검증 범위, 수정한 오류는 [TEST_RESULTS.md](TEST_RESULTS.md) 상단에 기록했다. 실행 산출물은 `test-results/stage4.xml`, `test-results/stage4.json`이다(로컬 산출물이며 Git 제외). 실제 Windows·Supabase Storage/Auth·OpenAI 연결과 운영 적용은 미검증이다.

## Windows 업데이트와 복구

각 컴퓨터에서 앱을 종료하고 기존 데이터/백업과 secrets.toml을 보존한다. 최신 코드를 새 폴더에 압축 해제하고 필요한 로컬 설정을 안전하게 옮긴 뒤 `Start_ReviewLab.cmd`를 실행한다. 사용자 지정 `REVIEWLAB_DATA_DIR`가 있다면 같은 경로를 유지한다. 기본 데이터 경로는 코드 ZIP과 별개다. PowerShell 수동 실행 명령은 안내서에 있다. 공용 모드는 관리자의 SQL/Storage 승인·적용을 먼저 완료해야 한다.

DB 사본만으로 원문 파일을 복원할 수 없다. **원문·논문 검토 → 파일 포함 백업·복원**의 ZIP은 기존 19개 테이블과 참조 파일을 함께 담고, 원본 재추출 결과까지 검사한다. 추가 복원이며 동일 ID 충돌을 덮어쓰지 않는다. SQLite 단독 파일로 원문 연결 자료를 공용 이전하는 것은 차단한다.

오류가 나면 현재 DB/파일/pending/백업을 보존하고 미확인 요청을 자동 재전송하지 않는다. SQL 실패는 트랜잭션 롤백 상태를 확인한다. v3 이후 구버전 클라이언트 쓰기를 재활성화하는 롤백은 제공하지 않으며 v3 호환 수정본으로 복구한다. 구버전 로컬 검토가 필요하면 이전 DB 사본을 별도 데이터 경로에서 열고 현재 DB를 교체하지 않는다.

## 변경 파일

- `CLOUD_TEST_REPORT.md`
- `.env.example`
- `.streamlit/secrets.toml.example`
- `AGENTS.md`
- `ARCHITECTURE.md`
- `DOCUMENTS_GUIDE.md`
- `PROJECT_PLAN.md`
- `README.md`
- `STAGE4_REPORT.md`
- `TEST_RESULTS.md`
- `pyproject.toml`
- `requirements.lock`
- `requirements.txt`
- `reviewlab/analysis/documents.py`
- `reviewlab/cloud/client.py`
- `reviewlab/cloud/dataset.py`
- `reviewlab/cloud/library.py`
- `reviewlab/cloud/migration.py`
- `reviewlab/db/migrations/004_documents.sql`
- `reviewlab/repositories/library.py`
- `reviewlab/services/document_ai.py`
- `reviewlab/services/document_transfer.py`
- `reviewlab/services/documents.py`
- `reviewlab/ui/app.py`
- `reviewlab/ui/cloud.py`
- `reviewlab/ui/documents.py`
- `scripts/build_document_sql.py`
- `scripts/test_cloud_postgres.py`
- `scripts/test_supabase_live.py`
- `supabase/README.md`
- `supabase/TESTING.md`
- `supabase/migrations/202610270003_documents.sql`
- `supabase/storage_documents.sql`
- `tests/cloud_helpers.py`
- `tests/test_cloud_postgres.py`
- `tests/test_cloud_test_system.py`
- `tests/test_cloud_ui.py`
- `tests/test_document_cloud.py`
- `tests/test_documents.py`
- `tests/test_documents_ui.py`
- `tests/test_reviews.py`
- `tests/test_surveys.py`
