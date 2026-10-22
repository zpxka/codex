"""기존 Library의 트랜잭션·자동 백업을 공유하는 리뷰 저장소."""
import json
from uuid import uuid4
from reviewlab.models import now


class Reviews:
    def __init__(self, library):
        self.library = library

    def projects(self):
        with self.library.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM review_projects ORDER BY created_at,id')]

    def project(self, project_id):
        return next((p for p in self.projects() if p['id'] == project_id), None)

    @staticmethod
    def insert(db, table, record):
        # table과 columns는 코드가 정한 allowlist/검증된 백업 필드만 전달한다.
        columns = list(record)
        db.execute(f'INSERT INTO {table} ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})',
                   tuple(record[k] for k in columns))

    def create_project(self, name, minimum, maximum, description):
        pid = str(uuid4())
        with self.library.connect() as db:
            self.insert(db, 'review_projects', dict(id=pid, name=name, data_kind=self.library.mode,
                rating_min=minimum, rating_max=maximum, description=description, created_at=now()))
        return pid

    def rows(self, project_id):
        with self.library.connect() as db:
            rows = [dict(r) for r in db.execute('SELECT * FROM reviews WHERE project_id=? ORDER BY created_at,source_row,id', (project_id,))]
            notes, checks = {}, {}
            for r in db.execute('SELECT n.* FROM review_notes n JOIN reviews r ON r.id=n.review_id WHERE r.project_id=? ORDER BY n.recorded_at,n.id', (project_id,)):
                notes.setdefault(r['review_id'], []).append(dict(r))
            for r in db.execute('SELECT c.* FROM review_checks c JOIN reviews r ON r.id=c.review_id WHERE r.project_id=? ORDER BY c.sequence', (project_id,)):
                event = dict(r)
                event.pop('sequence')
                checks.setdefault(r['review_id'], []).append(event)
            for r in rows:
                r['raw'] = json.loads(r['raw_json'])
                r['notes'] = notes.get(r['id'], [])
                r['checks'] = checks.get(r['id'], [])
                r['status'] = r['checks'][-1]['status'] if r['checks'] else '미검토'
            return rows

    def append_note(self, review_id, memo):
        with self.library.connect() as db:
            self.insert(db, 'review_notes', dict(id=str(uuid4()), review_id=review_id, memo=memo, recorded_at=now()))

    def append_check(self, review_id, record):
        with self.library.connect() as db:
            self.insert(db, 'review_checks', dict(record, id=str(uuid4()), review_id=review_id, recorded_at=now()))

    def links(self, project_id):
        with self.library.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM review_source_links WHERE project_id=? ORDER BY referenced_id', (project_id,))]

    def link_sources(self, project_id, source_ids):
        with self.library.connect() as db:
            known = {r['id']: dict(r) for r in db.execute('SELECT id,title,original_url FROM sources')}
            if any(sid not in known for sid in source_ids):
                raise ValueError('선택한 참고자료가 없습니다. 목록을 갱신하세요.')
            for sid in source_ids:
                db.execute('INSERT INTO review_source_links VALUES(?,?,?,?,?) '
                           'ON CONFLICT(project_id,referenced_id) DO UPDATE SET source_id=excluded.source_id',
                           (project_id, sid, sid, known[sid]['title'], known[sid]['original_url']))
