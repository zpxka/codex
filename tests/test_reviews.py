"""2단계: 실제 자료 대신 전부 허구 fixture와 임시 DB 사용."""
import csv
import io
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from uuid import uuid4
import pytest
from PIL import Image
from reviewlab.repositories.library import Library
from reviewlab.services.reviews import ReviewService, LABELS
from reviewlab.services.transfer import write_csv, export_backup as source_backup
from reviewlab.services import review_transfer as transfer
from reviewlab.analysis.reviews import select, statistics, patterns, context
from reviewlab.analysis.charts import png, plot, TITLES


@pytest.fixture
def review_service(library):
    return ReviewService(library)


@pytest.fixture
def project(review_service):
    return review_service.create_project('허구 테스트 프로젝트',0,5,'실제 근거 아님')


def data(rows=None,encoding='utf-8-sig'):
    rows=rows or [
        ['a','가상 A','음식',5,'2025-01-01','리뷰 이벤트 참여 좋아요','예','아니오','2025-02-01','https://example.com/a','원본 메모'],
        ['b','가상 A','음식',3,'2025-01-01','좋아요','아니오','','','',''],
        ['c','가상 B','생활','','','리뷰 이벤트 참여 좋아요','','','','',''],
        ['d','가상 B','생활',1,'2025-01-02','','아니오','','','',''],
    ]
    return write_csv(list(LABELS.values())[:-1],rows).decode('utf-8-sig').encode(encoding)


def upload(service,pid,content=None):
    return service.upload(pid,content or data(),'허구 테스트 수집 기록',True)


def test_upload_original_privacy_duplicate_and_restart(review_service,project,library):
    raw=data().decode('utf-8-sig').replace('연구자 메모\r\n','연구자 메모,작성자 실명\r\n').splitlines()
    raw=[raw[0]]+[line+',저장하지않을이름' for line in raw[1:]]
    blob='\r\n'.join(raw).encode('utf-8-sig')
    preview=review_service.preview(project,blob)
    assert (preview['added'],preview['ignored_columns'])==(4,1)
    assert review_service.repo.rows(project)==[]
    upload(review_service,project,blob)
    rows=review_service.repo.rows(project)
    assert rows[0]['raw']['research_memo']=='원본 메모'
    assert all('저장하지않을이름' not in r['raw_json'] for r in rows)
    assert rows[2]['rating'] is None and rows[2]['event_mentioned']=='미확인'
    assert upload(review_service,project,blob)['skipped']==4
    result=subprocess.run([sys.executable,'-c',
        'from reviewlab.repositories.library import Library; from reviewlab.repositories.reviews import Reviews; import sys,json; '
        'print(json.dumps(Reviews(Library(sys.argv[1],"actual")).rows(sys.argv[2]),ensure_ascii=False))',str(library.path),project],capture_output=True,text=True,check=True)
    assert json.loads(result.stdout)==rows


@pytest.mark.parametrize('blob', [b'not,csv\n1,2',b'\xff',b'content,content\na,b',b'content,rating\na',b'content\n'])
def test_invalid_files_no_mutation(review_service,project,blob):
    upload(review_service,project)
    before=transfer.export_backup(review_service.repo,project)
    with pytest.raises(ValueError):
        upload(review_service,project,blob)
    assert transfer.export_backup(review_service.repo,project)==before


@pytest.mark.parametrize('key,value', [('rating','NaN'),('rating','6'),('review_date','2025-02-30'),('review_date','2999-01-01'),
    ('event_mentioned','아마'),('original_url','javascript:alert(1)'),('original_url','https://user:secret@example.com')])
def test_bad_rows_reject_entire_file(review_service,project,key,value):
    blob=write_csv(['content',key],[['정상 허구 리뷰',''],['잘못된 허구 리뷰',value]])
    report=review_service.preview(project,blob)
    assert report['errors']
    with pytest.raises(ValueError):
        upload(review_service,project,blob)
    assert not review_service.repo.rows(project)


def test_cp949_mapping_and_mode(tmp_path,review_service,project):
    blob=write_csv(['후기','점수'],[['한글 테스트',4]]).decode('utf-8-sig').encode('cp949')
    review_service.upload(project,blob,'허구',True,'cp949',{'content':'후기','rating':'점수'})
    assert review_service.repo.rows(project)[0]['content']=='한글 테스트'
    from reviewlab.ui.reviews import sample_csv
    assert review_service.preview(project,sample_csv())['errors']
    other=ReviewService(Library(tmp_path/'example.db','example'))
    pid=other.create_project('가상 프로젝트')
    assert upload(other,pid,sample_csv())['added']==12
    with pytest.raises(ValueError):
        transfer.restore(review_service.library,transfer.export_backup(other.repo,pid),True)
    assert len(review_service.repo.rows(project))==1


def test_notes_checks_filters_statistics_and_patterns(review_service,project):
    upload(review_service,project)
    rows=review_service.repo.rows(project)
    review_service.note(rows[0]['id'],'연구자 추가 의견')
    with pytest.raises(ValueError):
        review_service.check(rows[0]['id'],'검토 완료','담당','2025-02-01','')
    review_service.check(rows[3]['id'],'분석 제외','담당','2025-02-01','범위 밖')
    rows=review_service.repo.rows(project)
    assert rows[0]['raw']['research_memo']=='원본 메모'
    assert len(select(rows,{}))==3
    assert len(select(rows,dict(exclude=False,platforms=['가상 B'])))==2
    assert len(select(rows,dict(ratings=['미확인'])))==1
    assert len(select(rows,dict(keyword='추가 의견')))==1
    assert len(select(rows,dict(start='2025-01-02',unknown_dates=True)))==1
    assert len(select(rows,dict(events=['예'])))==1
    tables,summary=statistics(rows)
    assert summary==dict(total=4,rating_n=3,text_n=3,date_n=3,event_yes=1,event_known=3,event_percent_all=25,event_percent_known=pytest.approx(100/3))
    assert tables['ratings']['count'].sum()==4
    assert tables['events']['count'].sum()==4
    assert tables['dates']['count'].sum()==3
    assert tables['lengths']['length'].tolist()==[13,3,13]
    assert tables['industries'].set_index('industry').loc['음식','mean_rating']==4
    pat=patterns(rows)
    assert len(pat['exact'])==2
    assert next(x for x in pat['phrases'] if x['phrase']=='리뷰 이벤트')['review_count']==2
    assert len(pat['expressions'])==4  # 리뷰 이벤트 + 이벤트 참여 for each matching review
    similar=[dict(rows[0],id='different',content='리뷰 이벤트 참여 좋아요!'),rows[0]]
    assert patterns(similar,.5)['similar']


@pytest.mark.parametrize('empty',[False,True])
def test_all_charts_and_png_korean(review_service,project,empty):
    if not empty:
        upload(review_service,project)
    rows=review_service.repo.rows(project)
    tables,_=statistics(rows)
    ctx=context(review_service.repo.project(project),rows,rows,{})
    for kind in TITLES:
        figure=plot(kind,tables)
        assert figure.layout.title.text
        blob=png(kind,tables,ctx)
        assert blob.startswith(b'\x89PNG')
        image=Image.open(io.BytesIO(blob))
        image.verify()
        assert image.width>1000
    assert statistics(rows,'industry','평균 별점')[0]['comparison']['denominator'].sum()==(0 if empty else 3)


def test_disk_backup_restore_full_equality_and_stage1_preservation(tmp_path,review_service,project,service,source):
    sid=service.save(source)
    before=source_backup(service.library)
    upload(review_service,project)
    rows=review_service.repo.rows(project)
    review_service.note(rows[0]['id'],'추가 메모')
    review_service.check(rows[0]['id'],'검토 완료','담당','2025-02-01','직접 읽은 테스트 근거','검토 메모')
    review_service.repo.link_sources(project,[sid])
    tables,_=statistics(rows)
    ctx=context(review_service.repo.project(project),rows,rows,{})
    ctx['input_ids']=[r['id'] for r in rows]
    transfer.save_run(review_service.repo,project,tables,ctx,patterns(rows))
    path=tmp_path/'한글 백업.csv'
    path.write_bytes(transfer.export_backup(review_service.repo,project))
    assert path.read_bytes().startswith(b'\xef\xbb\xbf')
    other=ReviewService(Library(tmp_path/'restored.db','actual'))
    from reviewlab.services.transfer import restore as restore_sources
    restore_sources(other.library,before,True)
    assert transfer.preview_restore(other.library,path.read_bytes())['added']==4
    assert not other.repo.projects()
    transfer.restore(other.library,path.read_bytes(),True)
    assert other.repo.rows(project)==review_service.repo.rows(project)
    assert transfer.export_backup(other.repo,project)==path.read_bytes()
    assert transfer.restore(other.library,path.read_bytes(),True)['skipped']==4
    assert source_backup(service.library)==before
    assert transfer.export_reviews(other.repo,project).decode('utf-8-sig').count('원본 메모')==1
    assert '리뷰 이벤트' in transfer.result_csv(tables,ctx,patterns(rows)).decode('utf-8-sig')
    assert other.library.path.exists()
    with other.library.connect() as db:
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]


def mutate(blob,table,key,value):
    reader=list(csv.DictReader(io.StringIO(blob.decode('utf-8-sig'))))
    for row in reader:
        if row['record_type']==table:
            record=json.loads(row['payload'])
            record[key]=value
            row['payload']=json.dumps(record,ensure_ascii=False)
            break
    return write_csv(transfer.HEADER,[[row[k] for k in transfer.HEADER] for row in reader])


@pytest.mark.parametrize('table,key,value',[
    ('reviews','rating',99),('reviews','import_id',str(uuid4())),('reviews','raw_json','{}'),
    ('review_projects','data_kind','example'),('review_projects','rating_max','NaN'),
    ('review_imports','total_rows',-1),('reviews','content','원본과 다른 손상된 내용'),
])
def test_corrupt_backups_are_atomic(tmp_path,review_service,project,table,key,value):
    upload(review_service,project)
    blob=mutate(transfer.export_backup(review_service.repo,project),table,key,value)
    target=ReviewService(Library(tmp_path/'target.db','actual'))
    existing=target.create_project('기존 프로젝트')
    upload(target,existing)
    before=transfer.export_backup(target.repo,existing)
    with pytest.raises(ValueError):
        transfer.restore(target.library,blob,True)
    assert transfer.export_backup(target.repo,existing)==before
    assert len(target.repo.projects())==1


def test_links_preserved_without_sources_and_deletion(tmp_path,review_service,project,service,source):
    sid=service.save(source)
    upload(review_service,project)
    review_service.repo.link_sources(project,[sid])
    other=ReviewService(Library(tmp_path/'no-source.db','actual'))
    transfer.restore(other.library,transfer.export_backup(review_service.repo,project),True)
    assert other.repo.links(project)[0]['source_id'] is None
    assert other.repo.links(project)[0]['title']==source['title']
    with service.library.connect() as db:
        db.execute('DELETE FROM sources WHERE id=?',(sid,))
    assert review_service.repo.links(project)[0]['source_id'] is None
    assert len(review_service.repo.rows(project))==4


def test_migration_preserves_stage1_and_snapshot(tmp_path,service,source,library):
    service.save(source)
    before=source_backup(library)
    # 1단계 파일 구성만 남긴 테스트 DB를 만든다. 실제 DB는 사용하지 않는다.
    with library.connect() as db:
        for table in reversed(transfer.TABLES):
            db.execute(f'DROP TABLE {table}')
        db.execute('DELETE FROM schema_migrations WHERE version=2')
    upgraded=Library(library.path,'actual')
    assert source_backup(upgraded)==before
    snapshots=list((library.path.parent/'backups').glob('before-change-*.sqlite3'))
    assert len(snapshots)==1
    with sqlite3.connect(snapshots[0]) as db:
        assert db.execute('SELECT version FROM schema_migrations').fetchall()==[(1,)]
        assert db.execute('SELECT title FROM sources').fetchone()[0]==source['title']
    Library(library.path,'actual')
    assert len(list((library.path.parent/'backups').glob('before-change-*.sqlite3')))==1


def test_migration_failure_rolls_back(tmp_path,monkeypatch):
    library=Library(tmp_path/'rollback.db','actual')
    with library.connect() as db:
        for table in reversed(transfer.TABLES):
            db.execute(f'DROP TABLE {table}')
        db.execute('DELETE FROM schema_migrations WHERE version=2')
    original=Path.read_text
    def invalid(path,*args,**kwargs):
        if path.name=='002_reviews.sql':
            return original(path,*args,**kwargs)+'; INVALID SQL;'
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',invalid)
    with pytest.raises(sqlite3.OperationalError):
        Library(library.path,'actual')
    with sqlite3.connect(library.path) as db:
        assert db.execute('SELECT version FROM schema_migrations').fetchall()==[(1,)]
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='reviews'").fetchall()


def test_export_is_safe_and_not_a_backup(review_service,project):
    upload(review_service,project,write_csv(['content','platform','rating'],[['=1+2','@fake',5]]))
    exported=transfer.export_reviews(review_service.repo,project)
    parsed=list(csv.reader(io.StringIO(exported.decode('utf-8-sig'))))
    assert "'=1+2" in parsed[1] and "'@fake" in parsed[1]
    assert review_service.repo.rows(project)[0]['raw']['content']=='=1+2'
    with pytest.raises(ValueError):
        transfer.restore(review_service.library,exported,True)


def test_backup_wrong_kind_missing_columns_null_id_and_confirmation(review_service,project):
    upload(review_service,project)
    good=transfer.export_backup(review_service.repo,project)
    for blob in (source_backup(review_service.library),b'wrong\n1',good.replace(b'payload',b'missing',1),
                 mutate(good,'review_projects','id',None)):
        with pytest.raises(ValueError):
            transfer.restore(review_service.library,blob,True)
    with pytest.raises(ValueError):
        transfer.restore(review_service.library,good,False)
    assert transfer.export_backup(review_service.repo,project)==good


def test_duplicate_id_keeps_original_even_if_csv_changed(review_service,project):
    upload(review_service,project)
    altered=data().decode('utf-8-sig').replace('리뷰 이벤트 참여 좋아요','바뀐 후기')
    assert upload(review_service,project,altered.encode('utf-8-sig'))['skipped']==4
    assert review_service.repo.rows(project)[0]['content']=='리뷰 이벤트 참여 좋아요'


def test_restore_conflict_with_other_project_rejects(tmp_path,review_service,project):
    upload(review_service,project)
    blob=transfer.export_backup(review_service.repo,project)
    # 같은 내부 리뷰 ID를 다른 프로젝트로 위장해도 원본을 덮어쓰지 않는다.
    replacement=str(uuid4())
    blob=blob.decode('utf-8-sig').replace(project,replacement).encode('utf-8-sig')
    with pytest.raises(ValueError,match='충돌'):
        transfer.restore(review_service.library,blob,True)
    assert len(review_service.repo.projects())==1


def test_restore_insert_error_rolls_back_whole_project(tmp_path,review_service,project,monkeypatch):
    upload(review_service,project)
    blob=transfer.export_backup(review_service.repo,project)
    other=ReviewService(Library(tmp_path/'failure.db','actual'))
    original=other.repo.insert
    def fail(db,table,record):
        # parse_backup의 메모리 검증은 통과시키고 실제 파일 DB 쓰기에서만 실패시킨다.
        filename=db.execute('PRAGMA database_list').fetchone()[2]
        if filename and table=='reviews':
            raise sqlite3.OperationalError('test disk write failure')
        return original(db,table,record)
    monkeypatch.setattr(type(other.repo),'insert',staticmethod(fail))
    with pytest.raises(sqlite3.OperationalError):
        transfer.restore(other.library,blob,True)
    assert not other.repo.projects()
    assert other.library.list_sources()==[]


def test_unresolved_reference_can_be_relinked_after_source_restore(tmp_path,review_service,project,service,source):
    sid=service.save(source)
    review_service.repo.link_sources(project,[sid])
    target=ReviewService(Library(tmp_path/'relink.db','actual'))
    transfer.restore(target.library,transfer.export_backup(review_service.repo,project),True)
    assert target.repo.links(project)[0]['source_id'] is None
    from reviewlab.services.transfer import restore as restore_sources
    restore_sources(target.library,source_backup(service.library),True)
    target.repo.link_sources(project,[sid])
    assert target.repo.links(project)[0]['source_id']==sid
    assert len(target.repo.links(project))==1
