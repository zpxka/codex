# 클라우드 자동 회귀 테스트 사용법

이 문서는 개발용 테스트 안내입니다. 앱을 사용하는 데 테스트 계정이나 CI 설정은 필요하지 않습니다. 운영 연구자료 15건이 있는 Supabase 프로젝트·SQLite·백업을 테스트 대상으로 지정하지 마세요.

## 1. 기본 테스트 — 계정·인터넷 연결 불필요

프로젝트 폴더에서 개발 패키지를 한 번 설치합니다. 최초 패키지 설치에는 인터넷이 필요합니다.

Windows PowerShell:

```powershell
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q -ra --junitxml=test-results/regression.xml --cloud-report=test-results/regression.json
```

Linux:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q -ra --junitxml=test-results/regression.xml --cloud-report=test-results/regression.json
```

테스트는 임시 디렉터리의 허구 자료만 사용합니다. 기본 pytest에서는 외부 HTTP 호출이 차단되고 사용자 데이터 경로·공용 저장소 설정이 격리됩니다. Auth·네트워크 장애는 대역으로 검증하며 실제 Supabase 연결 검증을 대신하지 않습니다. Docker 없이 실행하면 PostgreSQL 테스트 6개는 **SKIPPED(미실행)**입니다.

`test-results/regression.json`에는 OS·Python·상태·테스트 ID/파일 위치만 저장하고, XML에는 기본 회귀 실패 위치가 남습니다. 실제 자료/환경변수/비밀값은 포함하지 않습니다. 이 폴더는 Git에서 제외됩니다. `--showlocals`나 HTTP 디버그 로그를 추가하지 마세요.

## 2. PostgreSQL SQL·RLS — 임시 Docker 전용

Docker가 설치되고 실행 중인 개발 환경에서 다음을 실행합니다. Windows에서는 Docker Desktop의 Linux 컨테이너 모드가 필요하며, Windows에서 이 명령의 실기 실행 결과는 아직 확인하지 않았습니다. CI의 PostgreSQL 검증은 Linux에서 수행합니다.

```powershell
.\.venv\Scripts\python.exe scripts/test_cloud_postgres.py -ra --junitxml=test-results/postgres.xml --cloud-report=test-results/postgres.json
```

Linux는 Python 경로를 `.venv/bin/python`으로 바꿉니다. 이 실행기는 새 `postgres:16-alpine` 컨테이너를 만들고 마이그레이션과 **전체 회귀 테스트**를 실행한 뒤 자신이 만든 컨테이너만 정리합니다. 호스트 포트를 열지 않고 임의 테스트 비밀번호를 로그에 출력하지 않습니다. 테스트가 실행 전에 컨테이너 이름·실행별 소유 라벨을 검사합니다. `REVIEWLAB_TEST_PG_CONTAINER`를 수동으로 지정하지 마세요.

Auth의 `auth.uid()`와 사용자 테이블은 로컬 대역입니다. 실제 SQL 트랜잭션, 모든 업무 테이블의 RLS, 구성원/비구성원, CAS 충돌, 요청 ID 멱등성을 검사하지만 실제 Supabase Auth/JWT·게이트웨이 설정까지 검증하지는 않습니다.

종료 코드 1은 pytest 실패, 2는 Docker 준비·SQL 적용·수집 등의 환경/설정 오류일 수 있습니다. `postgres-environment.json`의 phase와 실패한 CI 단계로 구분하세요. SQL 적용 단계 실패는 마이그레이션 코드 문제일 수도 있으므로 단순 외부 장애로 단정하지 않습니다. 정리에 실패하면 해당 실행이 만든 테스트 컨테이너만 수동 정리합니다.

## 3. GitHub Actions 자동 실행

**Actions → ReviewLab cloud regression**에서 확인합니다. Pull Request와 main push, 수동 실행에 다음 작업을 수행합니다.

- Ubuntu와 Windows Python 3.12에서 전체 pytest 실행(실제 접속 정보 불필요).
- 별도 Linux 작업에서 임시 PostgreSQL 16 + 전체 pytest 실행.
- 실패한 테스트의 `tests/...py::test_...` 위치와 통과/실패/미실행 건수를 Summary에 표시.
- `regression-ubuntu-latest`, `regression-windows-latest`, `postgres-regression` 산출물에 JSON/JUnit XML 저장(14일).

이전 Windows 집중 workflow는 전체 OS 매트릭스에 통합했습니다. 설정 파일이 있다는 사실만으로 GitHub에서 통과했다고 판단하지 마세요. 이 작업 환경에서는 GitHub Actions API 접근이 Forbidden으로 차단되어 원격 실행 결과를 확인하지 못했습니다. 저장소에서 Actions가 비활성화되어 있다면 소유자가 활성화해야 합니다. 필요하면 Branch protection에서 세 작업을 필수 검사로 지정하세요.

## 4. 실제 Supabase — 별도 프로젝트, 수동 실행만

**현재 운영 프로젝트와 다른 새 Supabase 프로젝트**를 만듭니다. 프로젝트 참조 ID를 지정해도 코드가 그 프로젝트가 운영용인지 독립적으로 증명할 수는 없습니다. 설정 담당자가 별도 프로젝트임을 확인해야 합니다. 운영 키를 복사하지 마세요.

1. 새 프로젝트에 기존 `supabase/migrations/202610240001_shared_storage.sql` 다음 `202610250002_surveys.sql` 순서로 한 번씩 적용합니다. 운영 DB에 SQL을 다시 적용하지 않습니다.
2. Auth에 이메일 확인·비밀번호 설정을 마친 **서로 다른 테스트 계정 A, B**를 준비합니다. 실제 팀원 계정을 사용하지 않습니다. 공개 회원가입은 끕니다.
3. GitHub 저장소 **Settings → Environments → New environment**에서 `supabase-test`를 만들고 가능하면 required reviewer 및 main 브랜치 제한을 설정합니다. PR 코드에는 비밀값을 전달하지 않습니다. 수동 실행 시에도 신뢰하는 main 브랜치를 선택합니다.
4. 해당 Environment의 **Variables**에 아래를 설정합니다.

| 변수 | 값 |
| --- | --- |
| `REVIEWLAB_LIVE_TEST_CONFIRM` | `isolated-test-project-only` |
| `REVIEWLAB_TEST_PROJECT_REF` | 별도 테스트 프로젝트 URL의 20자리 참조 ID |

5. 같은 Environment의 **Secrets**에 다음 이름을 등록합니다. 값은 코드·문서·채팅에 쓰지 않습니다.

| Secret | 내용 |
| --- | --- |
| `REVIEWLAB_TEST_SUPABASE_URL` | 별도 테스트 프로젝트의 HTTPS URL |
| `REVIEWLAB_TEST_SUPABASE_KEY` | 그 프로젝트의 publishable/anon key |
| `REVIEWLAB_TEST_EMAIL_A` / `REVIEWLAB_TEST_PASSWORD_A` | 테스트 계정 A |
| `REVIEWLAB_TEST_EMAIL_B` / `REVIEWLAB_TEST_PASSWORD_B` | 테스트 계정 B(서로 다른 사용자) |

서비스 키, DB 비밀번호, 운영 키는 사용하지 않습니다. URL이 지정된 참조 ID의 `https://<ref>.supabase.co`와 정확히 일치해야 합니다. custom domain은 안전장치 때문에 지원하지 않습니다.

6. **Actions → Separate Supabase test project (manual) → Run workflow**를 실행합니다. 이 workflow는 PR/push에 자동 실행되지 않습니다. 모든 설정이 없으면 모든 실제 테스트가 skipped로 표시됩니다. 일부 설정만 있거나 확인값·프로젝트 ID가 다르면 연결 전에 실패합니다.
7. Summary와 `supabase-live-sanitized-report`를 확인합니다. `skipped`는 성공이 아닙니다. 이 보고서는 고정 단계 ID와 분류만 기록하고 원시 HTTP 응답·예외·토큰·로그인 정보는 기록하지 않습니다.

로컬에서도 위 **테스트 전용 이름의 환경변수**를 보안 도구로 주입한 뒤 다음을 실행할 수 있습니다. 셸 기록에 비밀번호를 직접 입력하는 방식은 피하세요. 운영 `.streamlit/secrets.toml`이나 앱 환경변수로 대체 연결하지 않습니다.

```powershell
.\.venv\Scripts\python.exe scripts/test_supabase_live.py
```

실접속 테스트는 A로 만든 실행별 `REVIEWLAB-AUTOTEST-...` 작업공간에서 두 세션 등록·검색·동시 수정 차단·새로고침·재편집·백업·리뷰/검토/분석 이력·응답 유실 후 명시적 동일 ID 재확인·삭제를 검사합니다. B의 별도 작업공간과 서로 접근할 수 없는지, publishable key만으로 요청할 수 없는지도 검사합니다. 실제 지연/회선 단절은 기본 테스트의 대역 주입으로 검증하며 Supabase 서비스 자체에 장애를 일으키지 않습니다.

`reviewlab` 스키마가 Data API에 공개되지 않았다면 직접 테이블 RLS HTTP 검사는 `schema_not_exposed`로 skipped입니다. 권한 검증을 위해 스키마를 공개하거나 권한을 완화할 필요는 없습니다. 실제 RPC 비인가 접근 차단은 별도로 검사하고, 직접 RLS는 임시 PostgreSQL에서 검사합니다.

완료/실패 시 이 실행의 생성 응답으로 받은 작업공간 ID만 대상으로 자료를 비웁니다. CAS로 확인된 최신 버전만 사용하며 실패한 정리를 자동 재시도하지 않습니다. 작업공간·구성원·revision·요청 receipt 메타데이터는 삭제 RPC가 없어 유지됩니다. 실행이 중단되거나 생성 응답 자체가 유실되면 테스트 잔여물이 남을 수 있습니다. 필요 시 관리자가 **별도 테스트 프로젝트에서만** `REVIEWLAB-AUTOTEST-...` 공간을 확인해 정리하세요. 프로그램은 이름을 검색해 기존 공간을 일괄 삭제하지 않습니다.

실접속 종료 코드 1은 검증 결과 불일치(`behavior_mismatch`), 2는 외부 통신·인증·스키마·설정·예상 밖 테스트 오류입니다. 2를 모두 외부 장애로 해석하지 말고 기록된 분류와 단계로 조사하세요. 설정 누락 skip만 있으면 종료 0이지만 실접속 성공은 아닙니다. 클라우드 장애·시간초과는 자동으로 성공이나 skip으로 바꾸지 않습니다.

## 5. 3·4·5단계 확장 시

새 테이블마다 실제/예시·작업공간 범위, 원본/주석/결과, RLS, 복합 FK, 클라이언트 검증 manifest, 스키마 버전, SQLite/CSV 왕복, 동시 수정·응답 유실 테스트를 함께 추가하세요. 고정 정답이 있는 분석 fixture와 작은 가상 자료를 사용하고 실제 설문·원고·AI 대화는 넣지 않습니다. 현재 전체 모드 CAS의 충돌 범위와 20MB/50,000레코드 한도는 대규모 자료 도입 전에 검토해야 합니다.
