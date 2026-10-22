"""한국어 화면: 데이터 규칙과 SQL은 서비스·저장소 계층에 위임한다."""
import hashlib
import sqlite3
from datetime import date

import pandas as pd
import streamlit as st

from reviewlab.config import MODES, database_path
from reviewlab.models import FIELDS, STATUSES, TYPES
from reviewlab.repositories.library import Library
from reviewlab.services.research import Research
from reviewlab.services.transfer import download_filename, export_backup, export_list, inspect_restore, restore

PAGES = ("메인 대시보드", "자료 등록", "자료 목록", "검증 관리", "데이터 백업 및 복원", "설정 및 데이터 관리", "리뷰 분석")
SORTS = ("등록일 최신순", "등록일 오래된순", "발행일 최신순", "발행일 오래된순")
LABELS = {"title": "제목", "authors": "저자·기관", "publication_date": "발행일",
          "source_type": "자료 유형", "original_url": "원문 URL", "notes": "핵심 내용 메모",
          "purpose": "연구 활용 목적", "created_at": "등록일(UTC)", "updated_at": "수정일(UTC)"}


def done(message):
    st.session_state["notice"] = message
    st.rerun()


def table(rows):
    return pd.DataFrame([{
        "제목": r["title"], "저자·기관": r["authors"] or "미상",
        "자료 유형": r["source_type"], "발행일": r["publication_date"] or "미상",
        "검증 상태": r["status"], "태그": ", ".join(t["name"] for t in r["tags"]),
        "등록일(UTC)": r["created_at"],
    } for r in rows])


def source_selector(rows, key):
    names = {r["id"]: f'{r["title"]} · {r["authors"] or "저자 미상"} · {r["id"][:8]}' for r in rows}
    selected = st.selectbox("자료 선택", list(names), format_func=names.get, key=key)
    return next(r for r in rows if r["id"] == selected)


def source_form(service, mode, source=None):
    source = source or {}
    sid = source.get("id")
    prefix = f'{mode}-{sid or "new"}-{st.session_state.get("source_epoch", 0) if not sid else 0}'
    tags = service.library.tags()
    tag_map = {t["id"]: t["name"] for t in tags}
    st.caption("* 필수 항목 · 저자와 발행일은 모르면 비워 두세요. 입력한 내용은 그대로 저장합니다.")
    with st.form(f"source-{prefix}"):
        data = {}
        data["title"] = st.text_input("자료 제목 *", source.get("title", ""), max_chars=1000)
        data["authors"] = st.text_input("저자 또는 작성 기관 (선택)", source.get("authors", ""))
        data["publication_date"] = st.text_input("발행일 (선택)", source.get("publication_date", ""),
                                                  help="YYYY, YYYY-MM, YYYY-MM-DD. 연/월만 알면 해당 정밀도로 입력하세요.")
        data["source_type"] = st.selectbox("자료 유형 *", TYPES, index=TYPES.index(source.get("source_type", TYPES[0])))
        data["original_url"] = st.text_input("원문 URL *", source.get("original_url", ""), max_chars=4000)
        data["notes"] = st.text_area("자료의 핵심 내용 메모 (선택)", source.get("notes", ""))
        data["purpose"] = st.text_area("연구에서 활용하려는 목적 (선택)", source.get("purpose", ""))
        selected_tags = st.multiselect("연구 주제 태그 (복수 선택)", list(tag_map),
                                      default=[t["id"] for t in source.get("tags", [])], format_func=tag_map.get)
        allow = st.checkbox("중복 후보를 확인했으며 별도 자료로 저장합니다", value=False)
        if sid and source["status"] == "검증 완료":
            st.warning("자료 내용을 수정해도 기존 검증 이력은 보존됩니다. 수정 후 검증 관리에서 상태와 근거를 다시 확인하세요.")
        submitted = st.form_submit_button("수정 저장" if sid else "자료 저장", type="primary")
    if submitted:
        try:
            service.save(data, selected_tags, sid, allow)
        except ValueError as error:
            st.error(str(error))
        else:
            if not sid:
                st.session_state["source_epoch"] = st.session_state.get("source_epoch", 0) + 1
            done("자료를 수정했습니다." if sid else "자료를 등록했습니다. 검증 상태는 미검증입니다.")


def dashboard(service):
    rows = service.search()
    columns = st.columns(3)
    columns[0].metric("전체 등록 자료", len(rows))
    columns[1].metric("검증 완료", sum(r["status"] == "검증 완료" for r in rows))
    columns[2].metric("미검증", sum(r["status"] == "미검증" for r in rows))
    st.caption("모든 수치는 현재 선택한 데이터 모드 기준입니다. 원문 확인 중·사용 보류는 미검증 수에 포함하지 않습니다.")
    if not rows:
        st.info("아직 등록된 자료가 없습니다. 왼쪽 메뉴의 자료 등록에서 시작하세요.")
    left, right = st.columns(2)
    with left:
        st.subheader("자료 유형별 현황")
        frame = pd.DataFrame({"자료 유형": TYPES, "자료 수": [sum(r["source_type"] == t for r in rows) for t in TYPES]}).set_index("자료 유형")
        st.dataframe(frame, width="stretch")
        if rows:
            st.bar_chart(frame)
    with right:
        st.subheader("검증 상태별 현황")
        st.dataframe(pd.DataFrame({"상태": STATUSES, "자료 수": [sum(r["status"] == s for r in rows) for s in STATUSES]}), hide_index=True, width="stretch")
    st.subheader("연구 주제 태그별 현황")
    tag_rows = [{"태그": tag["name"], "자료 수": sum(any(t["id"] == tag["id"] for t in r["tags"]) for r in rows)}
                for tag in service.library.tags()]
    st.dataframe(pd.DataFrame(tag_rows), hide_index=True, width="stretch")
    st.caption("한 자료에 여러 태그가 있으므로 태그별 개수의 합은 전체 자료 수보다 클 수 있습니다.")


def listing(service, mode):
    query = st.text_input("제목 및 저자 검색", key=f"search-{mode}")
    a, b, c = st.columns(3)
    kind = a.selectbox("자료 유형 필터", ("전체",) + TYPES)
    tag = b.selectbox("태그 필터", [None] + [t["name"] for t in service.library.tags()],
                      format_func=lambda value: "전체 태그" if value is None else value)
    status = c.selectbox("검증 상태 필터", ("전체",) + STATUSES)
    sort = st.selectbox("정렬", SORTS)
    st.caption("발행일 미상은 마지막에 표시합니다. 연·월까지만 아는 날짜는 해당 기간 시작일 기준으로 정렬합니다.")
    rows = service.search(query, kind, tag, status, sort)
    st.write(f"검색 결과: {len(rows)}건")
    if not rows:
        st.info("조건에 맞는 자료가 없습니다.")
        return
    st.dataframe(table(rows), hide_index=True, width="stretch")
    source = source_selector(rows, f"list-selected-{mode}")
    detail, edit, delete = st.tabs(["상세 정보", "수정", "삭제"])
    with detail:
        for key in FIELDS + ("created_at", "updated_at"):
            st.text(f'{LABELS[key]}: {source[key] or "미상 / 미입력"}')
        st.text(f'검증 상태: {source["status"]}')
        st.link_button("원문 열기 (직접 확인)", source["original_url"])
        st.caption("링크는 사용자가 클릭할 때 열립니다. 프로그램은 원문을 수집하거나 진위를 판정하지 않습니다.")
        show_events(source)
    with edit:
        source_form(service, mode, source)
    with delete:
        st.warning("자료와 연결된 검증 이력이 삭제됩니다. 삭제 전 DB 백업을 자동으로 보관합니다.")
        confirm = st.checkbox("선택한 자료와 검증 이력을 삭제하겠습니다", key=f'delete-{mode}-{source["id"]}')
        if st.button("자료 삭제", disabled=not confirm):
            service.delete(source["id"], confirmed=confirm)
            done("자료를 삭제했습니다. 삭제 전 DB 백업은 데이터 폴더의 backups에 보관했습니다.")


def show_events(source):
    st.subheader("검증 이력")
    if not source["events"]:
        st.caption("아직 검증 이력이 없습니다.")
    for event in reversed(source["events"]):
        with st.expander(f'{event["status"]} · {event["checked_on"] or "날짜 미입력"} · {event["checked_by"] or "담당자 미입력"}'):
            st.text(f'확인한 원문·공식 자료: {event["evidence"]}')
            st.text(f'메모: {event["memo"]}')
            st.caption(f'기록 시각(UTC): {event["recorded_at"]}')


def verification(service, mode):
    rows = service.search()
    if not rows:
        st.info("먼저 자료를 등록하세요.")
        return
    source = source_selector(rows, f"verify-selected-{mode}")
    st.write(f'현재 상태: {source["status"]}')
    st.link_button("등록된 원문 열기", source["original_url"])
    st.info("검증 완료는 담당자가 원문과 등록 내용을 직접 대조했다는 기록입니다. 자료의 학술적 타당성이나 주장의 진실성을 보증하지 않습니다.")
    with st.form(f'verify-{mode}-{source["id"]}-{st.session_state.get("verify_epoch", 0)}'):
        status = st.selectbox("새 검증 상태", STATUSES, index=STATUSES.index(source["status"]))
        day = st.date_input("검증 날짜", value=date.today(), max_value=date.today())
        person = st.text_input("검증 담당자 (별칭 권장)")
        evidence = st.text_area("확인한 원문 또는 공식 자료", help="직접 확인한 URL, 문서명, 페이지 등을 입력하세요.")
        memo = st.text_area("검증 관련 메모")
        confirm = st.checkbox("원문을 직접 확인하고 등록 정보와 대조했습니다")
        st.caption("검증 완료일 때 날짜·담당자·확인 자료·메모·직접 확인 표시가 모두 필요합니다.")
        submitted = st.form_submit_button("검증 기록 저장", type="primary")
    if submitted:
        try:
            service.verify(source["id"], {"status": status, "checked_on": day.isoformat(),
                                          "checked_by": person, "evidence": evidence, "memo": memo}, confirm)
        except ValueError as error:
            st.error(str(error))
        else:
            st.session_state["verify_epoch"] = st.session_state.get("verify_epoch", 0) + 1
            done("검증 기록을 저장했습니다. 이전 이력도 보존됩니다.")
    show_events(source)


def settings(service, mode):
    st.subheader("태그 관리")
    with st.form(f"add-tag-{mode}", clear_on_submit=True):
        name = st.text_input("새 태그 이름")
        add = st.form_submit_button("태그 추가")
    if add:
        try:
            service.add_tag(name)
        except ValueError as error:
            st.error(str(error))
        else:
            done("태그를 추가했습니다.")
    tags = service.library.tags()
    if tags:
        names = {t["id"]: t["name"] for t in tags}
        tid = st.selectbox("수정할 태그", list(names), format_func=names.get, key=f"tag-select-{mode}")
        with st.form(f"rename-{mode}-{tid}"):
            replacement = st.text_input("변경할 태그 이름", names[tid])
            rename = st.form_submit_button("태그 이름 변경")
        st.caption("태그 이름을 바꾸면 연결된 모든 자료에 반영됩니다.")
        if rename:
            try:
                service.rename_tag(tid, replacement)
            except ValueError as error:
                st.error(str(error))
            else:
                done("태그 이름을 변경했습니다.")
    st.divider()
    st.subheader("자동 백업")
    st.write("등록·수정·삭제·검증·태그 변경·CSV 복원 후 자동 백업합니다. 앱을 열 때에도 오늘의 백업을 확인합니다.")
    st.caption("UTC 날짜별 최신 사본 1개를 유지하며 최근 30개를 보관합니다. 앱이 꺼져 있을 때는 실행되지 않습니다. 삭제·복원 직전 백업은 별도로 남습니다.")
    backups = service.library.automatic_backups()
    if backups:
        latest = backups[0]
        from datetime import datetime, timezone
        modified = datetime.fromtimestamp(latest.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
        st.text(f"최근 백업 시각(UTC): {modified}")
        st.code(str(latest.parent))
    else:
        st.warning("자동 백업 사본이 없습니다. 아래 복원용 CSV도 안전한 곳에 보관하세요.")
    st.info("자동 백업은 이 컴퓨터에 저장됩니다. 노트북 분실·디스크 고장에 대비하려면 복원용 CSV를 별도 저장장치나 팀에서 승인한 저장소에도 보관하세요.")
    backup_and_restore(service, mode)
    st.subheader("로컬 저장 위치")
    st.code(str(service.library.path))
    st.caption("SQLite 백업은 같은 폴더의 backups에 보관됩니다. 삭제한 자료도 과거 백업에 남을 수 있으므로 보존 기간에 맞게 관리하세요. 실행 중 DB 파일을 직접 교체하지 마세요.")


def backup_and_restore(service, mode):
    st.subheader("CSV 내보내기·백업")
    st.table(pd.DataFrame([
        {"파일 구분": "일반 자료 CSV (전체 목록)", "용도": "Excel 조회·팀 공유", "내용": "자료 정보·태그·최신 검증 상태와 담당자·메모", "앱 복원": "불가"},
        {"파일 구분": "ReviewLab 전용 백업 CSV", "용도": "자료 복원·보관", "내용": "자료 ID·원문 입력·태그·시각·전체 검증 이력", "앱 복원": "가능"},
    ]))
    st.caption("UTF-8 BOM으로 저장합니다. 목록용 CSV는 스프레드시트 수식으로 해석될 수 있는 셀 앞에 작은따옴표를 붙입니다. 원본 데이터는 바뀌지 않습니다.")
    st.caption("다운로드 파일명에는 데이터 모드와 백업 날짜·시각(한국 시간, KST)이 포함됩니다. 현재 모드의 전체 자료를 내보냅니다.")
    st.download_button("전체 목록 CSV 다운로드", export_list(service.library),
                       file_name=download_filename(mode, "list"), mime="text/csv")
    st.download_button("복원용 CSV 백업 다운로드", export_backup(service.library),
                       file_name=download_filename(mode, "backup"), mime="text/csv")
    st.info("복원용 CSV는 자료 ID·태그·등록일·전체 검증 이력을 보존합니다. 목록용 CSV와 임의의 CSV는 검증 이력을 복원할 수 없습니다. 일반 참고문헌 CSV의 신규 등록은 자료 등록 → CSV 일괄 등록에서 진행하세요. 백업 파일은 내용을 수정하지 않고 안전하게 보관하세요.")
    st.subheader("CSV 복원")
    st.warning("추가 복원만 지원합니다. 같은 ID, 같은 URL 또는 같은 제목·저자·발행일인 자료는 건너뜁니다. 서로 다른 ID의 중복 후보를 보존하려면 아래 옵션을 선택하세요. 같은 ID는 항상 건너뜁니다. 기존 자료는 덮어쓰지 않으므로 수정된 백업도 기존 자료에 반영되지 않습니다. 복원 전 DB 백업이 자동 생성됩니다.")
    uploaded = st.file_uploader("ReviewLab 복원용 CSV (최대 10MB)", type=["csv"], key=f"upload-{mode}")
    if uploaded:
        content = uploaded.getvalue()
        allow_duplicates = st.checkbox("서로 다른 ID의 중복 후보도 복원합니다 (의도적으로 중복 등록한 자료용)",
                                       key=f"allow-restore-{mode}-{hashlib.sha256(content).hexdigest()}")
        result = inspect_restore(service.library, content, allow_duplicates)
        st.subheader("복원 미리보기")
        counts = st.columns(4)
        for column, label, value in zip(counts, ("전체 자료", "추가 예정", "중복 건너뛰기", "파일 오류" if result["scope"] == "file" else "오류 자료"),
                                        (result["total"], result["added"], result["skipped"], result["error_count"])):
            column.metric(label, "확인 불가" if value is None else value)
        if not result["can_restore"]:
            st.error("오류가 있어 복원 전체를 차단했습니다. 정상 레코드도 일부만 복원하지 않습니다. 기존 자료는 변경하지 않았습니다.")
            for error in result["errors"][:50]:
                prefix = f'자료 레코드 {error["record"]}: ' if error["record"] is not None else ""
                st.text(prefix + error["message"])
            if len(result["errors"]) > 50:
                st.caption("오류 설명은 처음 50건만 표시합니다. 오류 건수는 전체 기준입니다.")
            st.button("CSV 복원 실행", disabled=True)
        else:
            st.caption("아직 DB에 반영하지 않았습니다. 실행 시 파일과 중복 여부를 다시 검사합니다.")
            if result["skipped"]:
                st.warning(f'중복 후보 {result["skipped"]}건을 건너뜁니다. 기존 자료는 덮어쓰지 않습니다.')
            checked = st.checkbox("추가 복원 정책을 확인했으며, 신뢰하는 백업의 기존 검증 이력을 승계합니다",
                                  key=f'restore-{mode}-{allow_duplicates}-{hashlib.sha256(content).hexdigest()}')
            st.caption("검증 완료 상태는 백업에 기록된 과거 판단을 승계합니다. 이번 복원이 원문을 새로 검증하지는 않습니다. 실제/예시 간 복원은 차단합니다.")
            if st.button("CSV 복원 실행", disabled=not checked, type="primary"):
                try:
                    result = restore(service.library, content, checked, allow_duplicates)
                except ValueError as error:
                    st.error(str(error))
                else:
                    done(f'복원 완료: {result["added"]}건 추가, {result["skipped"]}건 건너뜀. 기존 자료는 유지했습니다.')


def main():
    st.set_page_config(page_title="ReviewLab · 연구자료 관리", page_icon="📚", layout="wide")
    st.sidebar.title("ReviewLab")
    label = st.sidebar.selectbox("데이터 모드", list(MODES), key="data_mode")
    mode = MODES[label]
    page = st.sidebar.radio("메뉴", PAGES, key="page")
    st.sidebar.caption("1·2단계 · 연구자료와 리뷰 분석\n\n한 연구 프로젝트를 로컬에서 관리합니다.")
    st.title(page)
    if mode == "example":
        st.warning("예시·연습 모드입니다. 여기에 넣은 자료는 실제 연구에 사용하지 마세요. 실제 자료와 별도 저장됩니다.")
    else:
        st.caption("실제 연구자료 · 조사한 출처만 직접 등록하세요.")
    if "notice" in st.session_state:
        st.success(st.session_state.pop("notice"))
    try:
        service = Research(Library(database_path(mode), mode))
        service.library.automatic_backup(only_if_needed=True)
        if service.library.backup_warning:
            st.warning(service.library.backup_warning)
        if page == "메인 대시보드":
            dashboard(service)
        elif page == "자료 등록":
            method = st.radio("등록 방식", ("직접 등록", "CSV 일괄 등록"), horizontal=True, key="source-registration-method")
            if method == "CSV 일괄 등록":
                from reviewlab.ui.source_import import main as source_import_main
                source_import_main(service.library)
            else:
                source_form(service, mode)
        elif page == "자료 목록":
            listing(service, mode)
        elif page == "검증 관리":
            verification(service, mode)
        elif page == "데이터 백업 및 복원":
            backup_and_restore(service, mode)
        elif page == "리뷰 분석":
            from reviewlab.ui.reviews import main as reviews_main
            reviews_main(service.library)
        else:
            settings(service, mode)
    except ValueError as error:
        st.error(str(error))
    except (sqlite3.Error, OSError):
        st.error("데이터 파일에 접근하거나 저장하지 못했습니다. 저장 경로의 쓰기 권한·디스크 공간을 확인하고 다른 실행 중인 앱을 종료한 뒤 다시 시도하세요. 파일을 임의로 삭제하지 마세요.")
