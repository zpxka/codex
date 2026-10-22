"""UTF-8 BOM CSV: 목록용과 손실 없는 복원용을 명확하게 구분한다."""
import csv
import io
import json
from uuid import uuid4
from datetime import datetime, timedelta, timezone

from reviewlab.models import FIELDS, tag_name, timestamp, valid_id, validate_event, validate_source
from reviewlab.services.research import duplicate_of

MAX_BYTES = 10 * 1024 * 1024
MAX_RECORDS = 10000
BACKUP_HEADER = ["format", "mode", "record_type", "payload"]
FORMAT = "reviewlab-backup-v1"


def download_filename(mode, kind):
    if mode not in ("actual", "example") or kind not in ("list", "backup"):
        raise ValueError("CSV 파일 구분이 올바르지 않습니다.")
    stamp = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d_%H%M%S")
    return f"reviewlab-{mode}-{kind}-{stamp}-KST.csv"


def write_csv(header, rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(header)
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8-sig")


def safe_cell(value):
    # 표시용 CSV만 이스케이프한다. 원 DB와 복원용 JSON 문자열은 변형하지 않는다.
    value = str(value)
    if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")):
        return "'" + value
    return value


def export_list(library):
    header = ["자료 ID", "데이터 구분", "제목", "저자·기관", "발행일", "자료 유형", "원문 URL", "핵심 메모",
              "활용 목적", "등록일(UTC)", "수정일(UTC)", "태그", "검증 상태", "검증 날짜", "검증 담당자",
              "확인한 원문·공식 자료", "검증 메모"]
    rows = []
    for source in library.list_sources():
        event = source["events"][-1] if source["events"] else {}
        values = [source["id"], library.mode, *[source[k] for k in FIELDS],
                  source["created_at"], source["updated_at"],
                  " | ".join(t["name"] for t in source["tags"]), source["status"],
                  *[event.get(k, "") for k in ("checked_on", "checked_by", "evidence", "memo")]]
        rows.append([safe_cell(v) for v in values])
    return write_csv(header, rows)


def export_backup(library):
    rows = [[FORMAT, library.mode, "metadata", json.dumps(
        {"tags": [t["name"] for t in library.tags()]}, ensure_ascii=False)]]
    for source in library.list_sources():
        payload = {k: source[k] for k in ("id",) + FIELDS + ("created_at", "updated_at", "events")}
        payload["tags"] = [t["name"] for t in source["tags"]]
        rows.append([FORMAT, library.mode, "source", json.dumps(payload, ensure_ascii=False)])
    return write_csv(BACKUP_HEADER, rows)


def _read_backup(content, mode):
    if len(content) > MAX_BYTES:
        raise ValueError("복원 파일은 10MB 이하여야 합니다.")
    csv.field_size_limit(MAX_BYTES)
    try:
        reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig"), newline=""), strict=True)
        if reader.fieldnames != BACKUP_HEADER:
            raise ValueError("ReviewLab 복원용 CSV를 선택하세요. 목록용 CSV는 복원할 수 없습니다.")
        raw = []
        for row in reader:
            if len(raw) >= MAX_RECORDS + 1:
                raise ValueError("한 번에 최대 10,000개 자료만 복원할 수 있습니다.")
            if set(row) != set(BACKUP_HEADER) or any(v is None for v in row.values()):
                raise ValueError("CSV 열 개수가 올바르지 않습니다.")
            if row["format"] != FORMAT or row["mode"] != mode:
                raise ValueError("백업 버전 또는 실제/예시 모드가 다릅니다. 해당 모드에서 복원하세요.")
            raw.append(row)
    except (UnicodeDecodeError, csv.Error):
        raise ValueError("UTF-8 형식의 손상되지 않은 ReviewLab 백업 CSV를 선택하세요.") from None
    if not raw or raw[0]["record_type"] != "metadata":
        raise ValueError("백업 메타데이터가 없습니다.")
    metadata = _payload(raw[0]["payload"])
    if set(metadata) != {"tags"}:
        raise ValueError("백업 메타데이터 필드가 올바르지 않습니다.")
    names = metadata["tags"]
    if not isinstance(names, list) or len(names) > MAX_RECORDS:
        raise ValueError("태그 목록이 올바르지 않습니다.")
    for name in names:
        tag_name(name)
    if len(set(names)) != len(names):
        raise ValueError("백업 태그 이름이 중복됩니다.")
    return names, raw[1:]


def _payload(value):
    try:
        result = json.loads(value)
    except (json.JSONDecodeError, RecursionError):
        raise ValueError("백업 JSON 데이터가 손상되었습니다.") from None
    if not isinstance(result, dict):
        raise ValueError("백업 데이터 구조가 올바르지 않습니다.")
    return result


def _validate_record(row, names, ids, event_ids):
    payload = _payload(row["payload"])
    expected = {"id", *FIELDS, "created_at", "updated_at", "events", "tags"}
    if row["record_type"] != "source" or set(payload) != expected:
        raise ValueError("알 수 없는 백업 레코드 또는 필드입니다.")
    validate_source(payload)
    source_id = valid_id(payload["id"])
    if source_id in ids:
        raise ValueError("백업 안에 같은 자료 ID가 반복됩니다.")
    timestamp(payload["created_at"])
    timestamp(payload["updated_at"])
    if not isinstance(payload["tags"], list) or any(not isinstance(t, str) or t not in names for t in payload["tags"]):
        raise ValueError("자료의 태그 연결이 올바르지 않습니다.")
    if len(set(payload["tags"])) != len(payload["tags"]):
        raise ValueError("자료에 같은 태그가 반복됩니다.")
    if not isinstance(payload["events"], list) or len(payload["events"]) > MAX_RECORDS:
        raise ValueError("검증 이력 형식이 올바르지 않습니다.")
    local_event_ids = set()
    for event in payload["events"]:
        validate_event(event)
        if set(event) != {"id", "status", "checked_on", "checked_by", "evidence", "memo", "recorded_at"}:
            raise ValueError("검증 이력 필드가 올바르지 않습니다.")
        event_id = valid_id(event["id"])
        if event_id in event_ids or event_id in local_event_ids:
            raise ValueError("검증 이력 ID가 중복됩니다.")
        local_event_ids.add(event_id)
        timestamp(event["recorded_at"])
    ids.add(source_id)
    event_ids.update(local_event_ids)
    return payload


def _inspect_records(content, mode):
    names, raw = _read_backup(content, mode)
    ids, event_ids, sources, errors = set(), set(), [], []
    for index, row in enumerate(raw, 1):
        try:
            sources.append(_validate_record(row, names, ids, event_ids))
        except ValueError as error:
            errors.append({"record": index, "message": str(error)})
    return names, sources, errors, len(raw)


def parse_backup(content, mode):
    names, sources, errors, _ = _inspect_records(content, mode)
    if errors:
        raise ValueError(f"자료 레코드 오류 {len(errors)}건: {errors[0]['message']} 전체 복원을 취소했습니다.")
    return names, sources


def partition(sources, existing, allow_duplicates=False):
    accepted, skipped = [], []
    existing_ids = {s["id"] for s in existing}
    for source in sources:
        if source["id"] in existing_ids or (not allow_duplicates and any(duplicate_of(source, old) for old in existing + accepted)):
            skipped.append(source)
        else:
            accepted.append(source)
    return accepted, skipped


def inspect_restore(library, content, allow_duplicates=False):
    """DB를 쓰지 않고 파일/레코드 오류와 추가·중복 건수를 구분한다."""
    try:
        _, sources, errors, total = _inspect_records(content, library.mode)
    except ValueError as error:
        return {"total": None, "added": None, "skipped": None, "error_count": 1,
                "errors": [{"record": None, "message": str(error)}], "scope": "file", "can_restore": False}
    existing = library.list_sources()
    accepted, skipped = partition(sources, existing, allow_duplicates)
    existing_events = {e["id"] for source in existing for e in source["events"]}
    conflicts = [source for source in accepted if any(e["id"] in existing_events for e in source["events"])]
    for source in conflicts:
        errors.append({"record": None, "message": "기존 자료와 검증 이력 ID가 충돌하는 추가 후보가 있습니다."})
    return {"total": total, "added": len(accepted) - len(conflicts), "skipped": len(skipped),
            "error_count": len(errors), "errors": errors, "scope": "record", "can_restore": not errors}


def preview_restore(library, content, allow_duplicates=False):
    _, sources = parse_backup(content, library.mode)
    accepted, skipped = partition(sources, library.list_sources(), allow_duplicates)
    return {"total": len(sources), "added": len(accepted), "skipped": len(skipped)}


def restore(library, content, confirmed=False, allow_duplicates=False):
    if not confirmed:
        raise ValueError("복원 정책과 검증 이력 승계에 동의해야 합니다.")
    names, sources = parse_backup(content, library.mode)  # 모든 레코드를 먼저 검사
    backup = library.snapshot()
    with library.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = [dict(r) for r in db.execute("SELECT * FROM sources")]
        accepted, skipped = partition(sources, existing, allow_duplicates)
        existing_event_ids = {r[0] for r in db.execute("SELECT id FROM verification_events")}
        if any(e["id"] in existing_event_ids for s in accepted for e in s["events"]):
            raise ValueError("기존 검증 이력 ID와 충돌합니다. 복원이 취소되었습니다.")
        tag_map = {r["name"]: r["id"] for r in db.execute("SELECT * FROM tags")}
        for name in names:
            if name not in tag_map:
                tag_map[name] = str(uuid4())
                db.execute("INSERT INTO tags VALUES(?,?)", (tag_map[name], name))
        for source in accepted:
            library.insert_source(db, source)
            db.executemany("INSERT INTO source_tags VALUES(?,?)", [(source["id"], tag_map[n]) for n in source["tags"]])
            for event in source["events"]:
                library.insert_event(db, source["id"], event)
    return {"total": len(sources), "added": len(accepted), "skipped": len(skipped), "backup": str(backup)}
