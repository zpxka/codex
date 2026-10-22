"""참고문헌 일괄 추가의 쓰기 잠금과 트랜잭션. 기존 레코드는 변경하지 않는다."""
from contextlib import contextmanager
from uuid import uuid4

from reviewlab.models import now


class SourceBatch:
    def __init__(self, library, db):
        self.library, self.db = library, db
        self.existing = [dict(row) for row in db.execute('SELECT * FROM sources')]
        self.tags = {row['name']: row['id'] for row in db.execute('SELECT * FROM tags')}

    def append(self, source, tags):
        sid = str(uuid4())
        recorded_at = now()
        self.library.insert_source(self.db, dict(source, id=sid, created_at=recorded_at, updated_at=recorded_at))
        for name in tags:
            if name not in self.tags:
                self.tags[name] = str(uuid4())
                self.db.execute('INSERT INTO tags VALUES(?,?)', (self.tags[name], name))
            self.db.execute('INSERT INTO source_tags VALUES(?,?)', (sid, self.tags[name]))
        # 검증 이벤트를 만들지 않는다. 기존 규칙에 따라 새 자료는 미검증이다.
        return sid


@contextmanager
def source_batch(library):
    with library.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        yield SourceBatch(library, db)
    # Library.connect가 성공한 커밋 직후 일별 자동 백업을 한 번 갱신한다.
