"""자료 관리 규칙. UI 없이도 테스트·재사용할 수 있다."""
import sqlite3

from reviewlab.models import tag_name, validate_event, validate_source


def source_key(record):
    return (record["title"].casefold(), record["authors"].casefold(), record["publication_date"])


def duplicate_of(candidate, existing):
    return candidate["original_url"] == existing["original_url"] or source_key(candidate) == source_key(existing)


class Research:
    def __init__(self, library):
        self.library = library

    def save(self, data, tag_ids=(), source_id=None, allow_duplicate=False):
        record = validate_source(data)
        tag_ids = list(dict.fromkeys(tag_ids))
        known = {t["id"] for t in self.library.tags()}
        if any(t not in known for t in tag_ids):
            raise ValueError("선택한 태그가 없습니다. 새로고침 후 다시 선택하세요.")
        duplicates = [s for s in self.library.list_sources()
                      if s["id"] != source_id and duplicate_of(record, s)]
        if duplicates and not allow_duplicate:
            raise ValueError("같은 URL 또는 같은 제목·저자·발행일의 중복 후보가 있습니다. 목록을 확인하고 별도 자료라면 중복 허용을 선택하세요.")
        return self.library.save(record, tag_ids, source_id)

    def search(self, query="", source_type="전체", tag=None, status="전체", sort="등록일 최신순"):
        rows = self.library.list_sources()
        query = query.casefold()
        rows = [r for r in rows if (not query or query in r["title"].casefold() or query in r["authors"].casefold())
                and (source_type == "전체" or r["source_type"] == source_type)
                and (status == "전체" or r["status"] == status)
                and (tag is None or tag in [t["name"] for t in r["tags"]])]
        key = "publication_date" if sort.startswith("발행일") else "created_at"
        # 날짜 미상은 정렬 방향과 무관하게 마지막에 둔다. 연/월만 아는 날짜는 시작일 기준이다.
        known = [r for r in rows if r[key]]
        unknown = [r for r in rows if not r[key]]
        return sorted(known, key=lambda r: (r[key], r["id"]), reverse=sort.endswith("최신순")) + unknown

    def add_tag(self, name):
        try:
            self.library.add_tag(tag_name(name))
        except sqlite3.IntegrityError:
            raise ValueError("같은 이름의 태그가 이미 있습니다.") from None

    def rename_tag(self, tag_id, name):
        try:
            self.library.rename_tag(tag_id, tag_name(name))
        except sqlite3.IntegrityError:
            raise ValueError("같은 이름의 태그가 이미 있습니다.") from None

    def verify(self, source_id, event, confirmed=False):
        event = validate_event(event)
        if event["status"] == "검증 완료" and not confirmed:
            raise ValueError("원문을 직접 확인했다는 확인란을 선택해야 검증 완료로 저장할 수 있습니다.")
        if source_id not in {s["id"] for s in self.library.list_sources()}:
            raise ValueError("자료가 없습니다.")
        self.library.add_event(source_id, event)

    def delete(self, source_id, confirmed=False):
        if not confirmed:
            raise ValueError("삭제 확인이 필요합니다.")
        backup = self.library.snapshot()
        self.library.delete(source_id)
        return backup
