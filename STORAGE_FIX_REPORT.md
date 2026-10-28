# 원문 Storage 오류 수정 — 2026-10-28

## 확인된 원인과 검증 범위

기존 오류 `Storage 접근을 확인하세요. 저장하지 않았습니다.`는 실제 파일 POST 전 `Files.put()`의 HEAD 존재 검사에서만 발생한다. 따라서 화면에서 파일을 선택한 것과 Storage 업로드 성공은 별개다. 해당 문구는 HEAD가 200/404 이외의 응답을 받았음을 뜻하지만, 이전 코드가 HTTP 상태를 버렸으므로 운영 서버의 실제 상태 코드는 확정할 수 없다.

코드 결함은 HEAD의 오류 본문이 없고 HTTP 404만 파일 미존재로 인정하여, HTTP 400 + `NoSuchKey` 또는 `not_found`로 표현되는 미존재 응답을 정상적인 신규 파일 등록으로 처리하지 못한 점이다. 이 응답 형태와 예시 모드의 405바이트 TXT를 HTTP 대역으로 재현하고 수정했다. 실제 운영 장애가 400이었는지, 권한/인증 오류였는지는 **미검증**이다.

## 수정 사항

- HEAD 대신 `GET /storage/v1/object/info/authenticated/reviewlab-originals/<workspace UUID>/example/<sha256>`로 존재 여부 확인. actual은 actual 경로 사용.
- HTTP 400/404이면서 허용한 파일 미존재 코드일 때만 신규 업로드. 알 수 없는 400/404, 인증·권한·버킷·서버 오류는 중단. 응답 메시지 문자열만으로 업로드 허용하지 않음.
- 기존 파일은 GET 다운로드와 SHA-256 일치 확인 후 재사용. 업로드 POST는 `application/octet-stream`, 사용자 Bearer JWT와 publishable/anon key, `x-upsert:false` 유지.
- 처리 순서: 로컬 TXT UTF-8 검사·텍스트 추출 → 원본 존재 검사/저장/다운로드 해시 확인 → 추출 JSON 존재 검사/저장/다운로드 해시 확인 → 문서 메타데이터 DB 저장. 파일 선택 자체에는 Storage 저장 요청 없음.
- 오류 화면과 Python 경고 로그에 고정 처리 단계(`original`, `extracted_json`, `file`), 작업(`info`, `upload`, `download`), HTTP 상태, 허용 목록 기반 오류 분류만 기록. URL/작업공간 ID/파일명/해시/헤더/응답 본문/비밀번호/JWT/API 키/원문/원시 예외는 기록하지 않음.
- 네트워크 오류는 HTTP `unconfirmed`, 분류 `network`. 자동 재전송하지 않음. 파일 저장 중 실패하면 문서 메타데이터를 등록하지 않으며 이미 저장된 불변 파일을 자동 삭제하지 않음.

## 진단 메시지 읽기

예: `Storage 실패: 단계=original, 요청=info, HTTP=403, 분류=access_denied`.

| 분류 | 확인할 내용 |
| --- | --- |
| authentication / HTTP 401 | 로그아웃 후 재로그인, 사용자 세션 유효성 |
| access_denied / HTTP 403 | 현재 사용자의 작업공간 구성원 권한, SELECT/INSERT 정책과 workspace/mode/hash 경로 일치 |
| bucket_missing | 연결한 프로젝트와 private 버킷 이름 확인 |
| mime_type / size_limit | MIME application/octet-stream 및 10MB 한도 |
| unknown / server_error | 표시한 단계·상태·분류를 확인. 임의로 RLS 해제하거나 Public으로 전환하지 않음 |
| network / unconfirmed | 연결·저장 결과 확인. 자동 재전송하지 않음 |

운영 RLS/정책 정의는 직접 읽거나 변경하지 않았다. 관리자 확인이 필요하면 정책 대상 authenticated, 경로의 workspace 구성원 검사, actual/example 경로 허용 여부를 확인한다. 이번 코드 수정에는 **추가 SQL/Storage 정책 마이그레이션이 없다**. 기존 v1/v2/v3 SQL과 Storage SQL을 다시 실행하지 않는다.

## 실제 테스트

Linux/Python 3.12.14, 임시 PostgreSQL 16: **277 통과 / 0 실패 / 0 미실행**. 명령:

```text
.venv/bin/python scripts/test_cloud_postgres.py -ra --junitxml=test-results/storage-fix.xml --cloud-report=test-results/storage-fix.json
```

기존 266개 + 새 11개 검사. 신규 검사는 405바이트/예시 모드 업로드, 400/404 파일 미존재, 버킷·JWT·권한·알 수 없는 상태·서버 오류 차단, 헤더/경로/원본→JSON 순서, 기존 객체 재사용, 진단 비밀값 비노출, 잘못된 UTF-8의 네트워크 전 차단, JSON 저장 실패 시 기존 데이터 보존을 검증한다. 기존 1~4단계·CAS·복원·검증 이력 회귀 검사를 포함한다.

**실제 Windows 및 Supabase Storage/Auth 접속은 미검증**이다. HTTP 대역과 임시 SQL 정책 검증을 운영 연결 성공으로 보고하지 않는다. 운영 연구자료·DB·원문·기존 백업은 변경하지 않았다.

## Windows 업데이트

1. 현재 앱의 터미널에서 Ctrl+C로 종료한다.
2. GitHub main 최신 ZIP을 새 폴더에 모두 압축 해제한다. 기존 데이터/백업 폴더를 지우지 않는다.
3. 기존 `.streamlit/secrets.toml`을 새 폴더의 같은 위치에 안전하게 복사한다. 사용자 지정 REVIEWLAB_DATA_DIR가 있으면 같은 경로를 유지한다. 새 작업공간을 만들거나 기존 자료를 다시 이전하지 않는다.
4. 새 폴더에서 `Start_ReviewLab.cmd`를 실행하고 기존 계정·공용 작업공간·예시 모드를 선택한다.
5. 같은 테스트 TXT를 원문 업로드한다. 오류가 계속되면 표시된 **단계·요청·HTTP·분류**만 전달한다. 토큰/키나 원문을 보낼 필요가 없다. 결과 미확인 경고가 있으면 상태부터 확인한다.

Git 사용자는 앱을 종료한 후 해당 저장소에서 `git pull --ff-only origin main`으로 갱신할 수 있다. 이번 수정에는 새 패키지나 DB 스키마 변경이 없다.
