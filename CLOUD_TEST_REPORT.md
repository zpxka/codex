> 2026-10-28 4단계 확장 검증: 전체 266개 통과(임시 PostgreSQL 포함). 원문 파일·근거·새 RLS/구버전 차단·전체 파일 ZIP 복구 검증을 추가했습니다. 실제 Supabase Storage/Auth 및 Windows는 미검증이며, 최신 세부 결과는 [TEST_RESULTS.md](TEST_RESULTS.md), 운영 승인 사항은 [STAGE4_REPORT.md](STAGE4_REPORT.md)를 따릅니다. 아래는 이전 클라우드 테스트 시스템 구축 당시의 결과 기록입니다.

> 이 문서는 3단계 추가 전 자동 테스트 구축 기록입니다. 설문을 포함한 최신 결과는 [TEST_RESULTS.md](TEST_RESULTS.md)와 [SURVEY_DEVELOPMENT_REPORT.md](SURVEY_DEVELOPMENT_REPORT.md)를 참고하세요.

# ReviewLab 클라우드 안정성 자동 테스트 결과

작성일: **2026-10-25(KST)**. 이 문서는 개발 환경에서 실제 실행한 결과입니다. 사용자는 운영 공용 작업공간에 참고문헌 15건 이전을 완료했다고 알려 주었으나, 이 작업은 그 운영 프로젝트에 접속하지 않았고 해당 자료를 검사·수정·삭제하지 않았습니다.

## 1. 실행 결과와 환경

| 실행 | 환경 | 통과 | 실패 | 미실행 | 확인 범위 |
| --- | --- | ---: | ---: | ---: | --- |
| 기본 전체 pytest | Linux 6.18.55 / Python 3.12.14 / pytest 8.4.2 | 206 | 0 | 6 | 임시 SQLite, HTTP/Auth/서버 대역, Streamlit AppTest, CSV/분석/PNG |
| PostgreSQL 포함 전체 pytest | 같은 Linux / Docker 28.4.0 / `postgres:16-alpine` | 212 | 0 | 0 | 위 전체 + 실제 PostgreSQL SQL/RLS/CAS/트랜잭션. Supabase Auth는 로컬 대역 |
| 실제 Supabase 실행기(전용 접속 정보 없음) | 전용 테스트 환경변수를 제거한 별도 프로세스 | 0 | 0 | 16 | 설정 확인 1개와 실제 검사 15개 모두 미실행으로 출력 |
| Windows 전체 회귀 CI | `windows-latest`, Python 3.12로 구성 | 미확인 | 미확인 | 미실행 | workflow 작성 완료. 실제 Windows/원격 CI 결과 미확인 |
| GitHub Actions 원격 실행 결과 | 저장소 API 조회 | — | — | 미확인 | API가 Forbidden을 반환해 실행 결과 확인 불가 |

기본 실행의 미실행 6개는 PostgreSQL 컨테이너가 없어서 건너뛴 항목입니다. 같은 항목을 두 번째 실행에서 실제로 실행해 모두 통과했습니다. 두 행의 테스트 수는 중복 집계하면 안 됩니다. 기존 183개 검사를 유지하고 29개 시나리오/매개변수 검사를 추가했습니다. 최종 기본 검사 8.26초, PostgreSQL 전체 검사 13.68초입니다.

## 2. 필수 항목별 검사

| 항목 | 실행한 검사와 관련 파일 | 결과와 한계 |
| --- | --- | --- |
| 저장·조회·동기화 | `test_cloud_storage.py`, `test_cloud_ui.py`, `test_cloud_postgres.py`: 참고문헌 등록/수정/삭제/검색, 별도 세션 새로고침, 실제/예시 분리, SQLite/서버 지문 비교 | 통과. 두 물리 컴퓨터의 실제 운영 접속은 검사하지 않음 |
| 동시 수정 | `test_cloud_storage.py`, `test_cloud_regression.py`, `test_cloud_postgres.py`: 같은 버전 두 작성자, 먼저 저장한 값 보존, 오래된 저장 차단, 새로고침 후 명시적 재편집 | 통과. PostgreSQL 병렬 쓰기에서 정확히 한 작성자만 성공 |
| 장애·중복 방지 | `test_cloud_client.py`, `test_cloud_storage.py`, `test_cloud_regression.py`: ConnectionError/ReadTimeout, 커밋 전 단절/커밋 후 응답 유실, 동일 ID receipt, 다른 payload 거부, 과거 요청이 이후 변경을 되돌리지 않음 | 통과. 지연은 예외/대역 주입이며 실제 회선을 끊거나 Supabase 장애를 유발하지 않음 |
| 화면 성공 표시 | `test_cloud_ui.py`: 저장 전 연결 실패/커밋 후 응답 유실 각각 오류 표시, 성공 안내 없음, 새로고침 후 commit 호출 수 증가 없음 | 통과. Streamlit AppTest이며 실제 브라우저 수동 조작 아님 |
| 백업·복원 | `test_transfer.py`, `test_reviews.py`, `test_cloud_storage.py`, `test_cloud_regression.py`: 실제 CSV 파일 저장→읽기→복원, 자료·태그 연결·검증 이력·리뷰·메모·검토·분석 실행 비교, 전체 SQLite/서버 11개 테이블 지문 비교 | 통과. 자료 CSV는 태그 UUID를 이름으로 재연결하므로 자료/이력 내용·태그 이름과 연결로 비교. 기존 대상 태그도 보존 |
| 손상/기존 자료 보호 | 기존 CSV 손상/필수값/이력 ID 충돌/모드 오류/부분 삽입 롤백 검사 + 변조된 pending SQLite 사본 전송 차단 | 통과. 정상 백업 바이트 보존 및 기존 데이터 지문 불변 검사 포함 |
| 인증/접근 권한 | Auth HTTP 대역 + 실제 PostgreSQL `anon` 거부, 다른 작업공간 조회/쓰기 거부, 권한 회수, 11개 업무 테이블 모두 자료가 있는 상태에서 구성원/비구성원 RLS와 직접 쓰기 금지 확인 | 통과. 실제 Supabase 로그인/JWT/API gateway 및 publishable key 단독 접근 차단은 실접속 미검증 |
| 기존 1·2단계 | `test_research.py`, `test_automatic_backup.py`, `test_source_import.py`, `test_source_import_ui.py`, `test_reviews.py`, `test_reviews_ui.py`, `test_ui.py` 등 | 기존 회귀 모두 통과. UTF-8 BOM/CP949, 한글, 중복, 태그, 통계 알려진 정답, Plotly/PNG 생성·디코딩 포함 |
| Windows 파일 잠금 | `test_cloud_preflight.py`: 한글/공백 경로, 파일 핸들 종료 추적, 열린 파일 교체 금지 모사, 사전 검사 실패 분류·비밀값 비노출 | Linux 모사 통과. Windows 네이티브 결과는 미검증 |
| 실행기 안전성 | `test_cloud_test_system.py`: 운영 설정 fallback 금지, 부분 설정·서비스 키·다른 ref 차단, 비밀값 없는 보고서, 미설정 skip, 실행 소유 ID/컨테이너만 정리, 준비/정리 실패 분류 | 통과. 실제 Supabase 시나리오도 명시적 MemoryServer 대역으로 실행기 흐름을 검증했으며 실접속 성공으로 집계하지 않음 |

파일 경로는 모두 `tests/` 아래입니다. 동기화 검사는 별도 CloudLibrary 세션의 명시적 reload를 사용하며, 실제 두 기기에서 10초 알림/브라우저 동작까지 증명하지는 않습니다.

## 3. 자동 실행과 산출물

- `.github/workflows/cloud-regression.yml`: 모든 PR, main push, 수동 실행. Windows/Linux 전체 pytest와 Linux 임시 PostgreSQL 전체 pytest를 별도 작업으로 실행합니다. 이전 Windows 전용 workflow를 통합했습니다.
- `.github/workflows/supabase-live-tests.yml`: 별도 `supabase-test` Environment의 Secrets/Variables로만 수동 실접속합니다. PR에는 비밀값을 제공하지 않습니다. 전용 프로젝트 설정이 없으면 skipped이며 부분 설정은 접속 전에 차단합니다.
- `tests/conftest.py`: 임시 데이터 경로, 기본 외부 HTTP 차단, 앱 공용 설정 격리, `--cloud-report` JSON 집계.
- `scripts/test_cloud_postgres.py`: 컨테이너 실행 라벨/소유 확인, UTF-8 명시, pytest 인자 전달, 준비/정리 상태 JSON, 자신이 만든 임시 컨테이너 정리.
- `scripts/test_supabase_live.py`: 전용 ref 확인·테스트 계정 2개·실행별 새 작업공간, 실제 RPC/세션/충돌/모드/이력/백업/복구 검사, 고정 분류만 출력.
- `scripts/summarize_tests.py`: 상태 건수, 테스트 ID/파일 위치, 비정상 종료 코드를 GitHub Summary로 표시.

로컬 산출물은 `test-results/regression.json`, `regression.xml`, `postgres.json`, `postgres.xml`, `postgres-environment.json`, `supabase-live.json`, `supabase-live.xml`입니다. Git에서 제외되며 GitHub Actions에서는 14일간 Artifacts로 보관합니다. 산출물의 skipped를 passed로 바꾸지 않습니다. 정적 Python 컴파일, workflow YAML 파싱, `git diff --check`도 통과했습니다. YAML 파싱이 GitHub 엔진에서의 실제 실행을 대신하지는 않습니다.

재현 명령(Windows는 `.venv/bin/python`을 `.\.venv\Scripts\python.exe`로 변경):

```bash
.venv/bin/python -m pytest -q -ra --junitxml=test-results/regression.xml --cloud-report=test-results/regression.json
.venv/bin/python scripts/test_cloud_postgres.py -ra --junitxml=test-results/postgres.xml --cloud-report=test-results/postgres.json
```

실접속은 [설정 안내](supabase/TESTING.md)에 따라 별도 테스트 프로젝트를 준비한 후에만 실행합니다.

## 4. 발견한 문제와 수정

운영 앱의 새 결함을 재현한 것은 아닙니다. 자동 검증 환경의 다음 빈틈을 보완했습니다.

1. 기존 Windows workflow는 일부 클라우드 파일 push에만 반응했습니다. 전체 PR/main 및 양쪽 OS 전체 회귀 검사로 확대했습니다.
2. 기존 pytest의 UI 공용 설정 격리에 더해 임시 데이터 경로와 외부 HTTP 차단을 추가했습니다. 기본 검사에서 실제 DB/설정을 자동 선택하지 않습니다.
3. 기존 PostgreSQL 테스트는 환경변수의 컨테이너 이름만 신뢰했습니다. 실행별 소유 라벨 검증과 생성 성공 여부 추적을 추가해 다른 컨테이너를 쓰거나 지우지 않도록 했습니다.
4. PostgreSQL 실행기의 텍스트 인코딩을 UTF-8로 명시하고, 준비 실패/테스트 실패/정리 실패 및 실패 단계를 분리했습니다.
5. 테스트 작성 중 타임아웃 대역의 로그인 토큰 누락, CSV 복원 대상 기본 태그를 간과한 비교, 화면 저장 전에 전체 통신을 막던 장애 주입을 발견했습니다. 실제 저장 실패 경로·추가 복원 정책에 맞게 fixture를 수정했고 검사 조건을 약화하거나 skip으로 회피하지 않았습니다.

최종 재실행 결과는 위 표와 같으며 실패 0건입니다.

## 5. 기존 기능과 데이터에 미치는 영향

앱 실행 코드, SQLite/Supabase 업무 스키마, 운영 연결 설정, 저장 요청/수동 복구 정책을 변경하지 않았습니다. 1·2단계 기능과 일반 참고문헌 CSV, 기존 백업 호환성은 기존 테스트를 포함해 통과했습니다. 새 런타임 의존성도 없습니다. 실제 연구자료 15건의 존재/내용은 운영 접속을 하지 않았으므로 이 보고서에서 직접 검증했다고 주장하지 않습니다.

실접속 실행기는 생성 응답을 받은 이번 실행 소유 작업공간의 자료만 정리합니다. 정리도 CAS 검사를 거치며 미확인 저장을 자동 재전송하지 않습니다. 공간/구성원/receipt 메타데이터는 삭제 API가 없어 남고, 실행 강제 중단·생성 응답 유실 시 가상 자료가 남을 수 있습니다. 정리 실패는 실패로 보고하며 관리자가 별도 테스트 프로젝트에서 확인합니다. 운영 공간 목록을 탐색해 일괄 삭제하지 않습니다.

## 6. 실제 Supabase에서 추가 검증할 항목

현재 전용 테스트 프로젝트 정보가 없으므로 다음은 모두 미검증입니다.

- 테스트 계정 2개의 실제 이메일 확인·로그인·토큰·프로젝트 Auth 설정.
- 실제 REST/RPC를 통한 두 세션 자료·전체 이력 저장/조회, 충돌·명시적 재편집·동일 ID 재확인.
- publishable/anon key만 있는 요청 거부와 다른 테스트 계정의 작업공간 읽기/쓰기 거부.
- 실제 프로젝트의 RLS/권한 구성. `reviewlab` 스키마가 Data API 비공개면 직접 HTTP RLS 검사는 `schema_not_exposed`로 미실행 표시하며 RPC 권한 검사는 별도로 수행합니다. 검사를 위해 권한을 완화하지 않습니다.
- 실접속 백업 파일 왕복 및 테스트 실행 소유 자료 정리 성공 여부.
- Windows 실제 실행, 실제 노트북/데스크탑 알림·새로고침, 장시간 인증/네트워크 환경, 서비스 중단/응답 지연의 실제 운용 영향.

## 7. 3·4·5단계 개발 전 점검 사항

1. Windows CI 결과와 별도 Supabase 테스트 프로젝트 결과를 먼저 확인하세요. 현재 문서의 구성 완료와 미실행을 구분해야 합니다.
2. GitHub `supabase-test` Environment의 승인자/브랜치 제한, 테스트 전용 계정·프로젝트 분리를 설정해야 합니다. 지정 ref만으로 운영/테스트 여부를 독립 판별할 수는 없습니다.
3. 새 설문/원고/AI 기록/제출 테이블에 스키마 버전·백업 manifest·RLS·범위 FK·이력 보존·CSV/SQLite 왕복 검사를 함께 추가해야 합니다.
4. 현 모드 전체 CAS는 별개 자료 편집도 충돌할 수 있습니다. 20MB/50,000레코드 한도·성능·행 단위 변경 RPC 전환 여부를 실제 규모 도입 전에 검토해야 합니다.
5. 실제 설문·원고를 fixture에 넣지 않고 작은 가상 정답 데이터, 개인정보 최소화, 원본/연구자 판단/분석 결과 분리 원칙을 유지해야 합니다.

사용 절차는 [supabase/TESTING.md](supabase/TESTING.md)를 참고하세요.
