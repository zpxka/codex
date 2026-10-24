# 3단계 설문 시스템 개발 결과

검증일: 2026-10-25(KST). 운영 Supabase와 사용자 연구자료에는 접속하지 않았습니다. 운영 SQL도 적용하지 않았습니다.

## 구현

- 기존 한국어 메뉴에 **설문조사 분석**과 6개 하위 화면을 추가했습니다. 프로젝트 생성/메타데이터 수정·목록, 원본 응답/분석 적용 응답 읽기 전용 조회를 제공합니다.
- UTF-8/BOM/CP949 CSV, 열·유형 제안/사용자 확인, 필수 구조·중복 열·빈 행·잘못된 값 검사, 식별정보 의심 열 기본 제외, 원본 파일 SHA·수집 시각·설정·제외 건수 기록, 동일 파일 재업로드 차단을 제공합니다.
- 7종 변수와 범주·척도·역문항·결측·다중 구분자 설정을 지원합니다. 원문을 바꾸지 않고 분석 시 변환합니다.
- 변수별 유효/결측/전체 N, 빈도·분모·비율, 평균/중앙값/표본 SD/최소/최대, 다중 선택의 응답자 기준 비율, 범주 교차표·행 비율·집단별 리커트 평균·리커트 Pearson 관계 탐색과 범주 집단 필터를 제공합니다.
- 한국어 막대/가로 막대/도넛/리커트 분포/집단 평균/히트맵 PNG 및 BOM CSV, 분석 기록·입력 ID·문항 정의·필터 스냅샷, 설문 전체 추가 복원을 구현했습니다. 전체 SQLite·공용 데이터셋에도 설문을 포함했습니다.

## 변경 파일과 구조

| 구분 | 주요 파일 |
| --- | --- |
| 새 저장 스키마 | `reviewlab/db/migrations/003_surveys.sql`, `supabase/migrations/202610250002_surveys.sql` |
| 새 설문 코드 | `reviewlab/services/surveys.py`, `survey_transfer.py`, `reviewlab/analysis/surveys.py`, `reviewlab/ui/surveys.py` |
| 기존 확장 | `repositories/library.py`, `cloud/dataset.py`, `cloud/library.py`, `cloud/client.py`, `cloud/migration.py`, `ui/app.py`, `ui/cloud.py` |
| SQL·실행기 | `scripts/build_cloud_sql.py`(v1 파일 재생성 방지), `build_survey_sql.py`, `test_cloud_postgres.py`, `test_supabase_live.py` |
| 새 테스트 | `tests/test_surveys.py`, `test_surveys_ui.py`, `test_surveys_postgres.py` |
| 기존 검사 갱신 | 클라우드 대역/스키마 버전 기대값·v1 마이그레이션 fixture·PostgreSQL RPC 이름. 검사 조건을 없애거나 오류를 skip으로 바꾸지 않았습니다. |
| 안내 | README, ARCHITECTURE, PROJECT_PLAN, AGENTS, TEST_RESULTS, `SURVEY_GUIDE.md`, `supabase/SURVEY_UPGRADE.md`, Supabase README/TESTING |

추가 런타임 패키지는 없습니다. UI/분석/서비스를 분리하고 기존 SQLite 저장소 API와 pandas·Matplotlib 한국어 폰트를 재사용했습니다. 설문 그래프는 정적 PNG를 화면과 다운로드에 공통 사용합니다.

## DB·호환성

SQLite 스키마 3: `survey_projects`, `survey_questions`, `survey_imports`, `survey_responses`, `survey_answers`, `survey_analysis_runs`. 로컬 v1/v2 자료는 사전 사본 생성 후 트랜잭션으로 추가 스키마를 적용합니다. v1부터 올라오면 같은 업그레이드의 사전 백업은 한 번만 생성합니다. 기존 참고문헌·리뷰·이력 테이블은 삭제/초기화하지 않습니다.

Supabase 스키마 2: 별도 새 마이그레이션을 v1 다음 적용합니다. 정규 테이블·RLS·작업공간/모드 FK·프로젝트 간 참조 검사·중복 UNIQUE와 v2 전용 읽기/저장 RPC를 추가합니다. 모드 전체 CAS·receipt 멱등성은 유지하고 v1 저장은 명시적으로 차단합니다. 구버전 클라이언트가 설문 테이블 없는 payload로 저장하는 위험을 막기 위한 호환성 제한입니다. **두 컴퓨터 모두 업데이트해야 하며 v1 앱으로 공용 쓰기를 재개할 수 없습니다.**

실제 운영 적용은 사용자가 백업·검토 후 별도 승인하고 관리자가 수행해야 합니다. 이 작업에서는 SQL 파일만 작성하고 임시 PostgreSQL에서 적용했습니다. 적용 순서와 실패/복구·Windows 절차는 [Supabase v2 업데이트](supabase/SURVEY_UPGRADE.md)에 있습니다.

## 실행한 검증

환경: Linux / Python 3.12.14 / pytest 8.4.2 / 임시 Docker PostgreSQL 16. 기존 212개 검사에 설문 검사 30개를 추가했습니다.

| 실행 | 결과 |
| --- | --- |
| 전체 pytest (Docker 없이) | **234 통과, 8 미실행**, 실패 0. PostgreSQL 기존 6개+설문 2개는 컨테이너 미설정으로 skip |
| 전체 pytest + 소유 확인한 임시 PostgreSQL, 실제 v1→v2 SQL 적용 | **242 통과, 0 실패, 0 미실행** |
| 설문 서비스 대상 검사 | **25 통과** |
| 설문 Streamlit 화면 검사 | **3 통과** |
| 설문 실제 PostgreSQL 검사 | 전체 실행에 포함하여 **2 통과** |
| Python 컴파일·diff 공백 검사 | 통과 |
| 실제 Supabase·Windows 네이티브/두 물리 컴퓨터 | **미검증**. 접속 정보/Windows 환경 없음 |

검사 범위: 한글 인코딩 3종, 같은 파일/빈 응답/동일 패턴, PII 기본 제외, 날짜·척도·NaN·다중 범주 오류와 전체 중단, 역문항/원본 불변, 관측 N/결측/전체 분모·표본 SD·다중 선택 비율·행 비율/집단 평균/상관 정답, 집단 필터, 5종 PNG 생성/디코딩/한글 메타데이터, 분석 이력, CSV 실제 파일 왕복·모드 거부·같은 ID 보호·손상 백업·불변 지문, SQLite v2→v3 실패 롤백/백업, 공용 세션 충돌/새로고침/이전, 설문 6테이블 RLS·비구성원/직접 쓰기 거부·실제/예시 분리·멱등성·v1 쓰기 차단, 기존 1·2단계 전체 회귀입니다.

v1→v2 SQL 보존 검사에서는 별도 임시 DB에 **허구 참고문헌 15건·태그·검증 이력·revision·요청 receipt**를 넣고 SQL을 적용한 뒤 이전 11개 테이블 내용·revision·receipt가 같은지 비교했습니다. 이는 사용자 운영 15건을 직접 검사했다는 뜻이 아닙니다.

발견/수정: CSV 복원에서 병합된 기존 답변이 손상 파일의 답변 누락을 가리던 문제를 발견하여 파일 독립 검증을 먼저 수행합니다. PostgreSQL 준비 판정도 `pg_isready`만으로는 초기 DB 생성 전 성공할 수 있어 실제 테스트 DB의 SELECT 1 성공을 기다리도록 수정했습니다. 새 SQLite 버전 때문에 기존 테스트의 미래 버전/가상 v1 구성도 갱신했고, 기존 검증 동작을 유지했습니다.

## 제약과 미구현

- 실제 Supabase Auth/API/RLS 배포 및 두 기기 동작, Windows 파일 경로/CSV/한국어 PNG 실기는 검증하지 않았습니다. GitHub Actions 전체 OS 검사는 구성돼 있으며 원격 결과를 성공으로 보고하지 않습니다.
- 원본 응답 편집·삭제, 프로젝트 삭제, XLSX 직접 입력, 임의 날짜/텍스트 리커트 자동 코딩, 통계적 유의성·회귀·신뢰도 계수·인과 판단은 제공하지 않습니다. 4·5단계 기능도 구현하지 않았습니다.
- 개인정보 감지는 보조 규칙이며 익명성을 보장하지 않습니다. 자유서술·타임스탬프·조합 식별 정보를 연구자가 검토해야 합니다. 제외 설정 변경은 이미 저장한 원본을 삭제하지 않습니다.
- 동일 패턴은 서로 다른 응답자일 수 있어 자동 삭제하지 않습니다. 다른 파일 간 동일 응답자 식별/중복 제거는 자동 수행하지 않습니다. 기본 결측 제외는 문항별, 교차표는 두 변수 모두 유효한 응답 기준입니다.
- 모드 전체 20MB/50,000레코드에 모든 답변·이력도 합산됩니다. 5,000응답·100열이라는 파일 제한보다 공용 레코드 제한이 먼저 걸릴 수 있습니다.
- 새 스키마 적용 후 구버전 공용 쓰기 롤백은 지원하지 않습니다. 설문을 보존하며 v2 호환 코드로 복구해야 합니다. 원본 DB/백업을 삭제하거나 운영 테이블 DROP으로 되돌리지 마세요.

사용법: [SURVEY_GUIDE.md](SURVEY_GUIDE.md). 업데이트/백업/롤백: [supabase/SURVEY_UPGRADE.md](supabase/SURVEY_UPGRADE.md).
