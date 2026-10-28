"""3단계: 모두 허구 자료/임시 DB. 운영 Supabase 접속 없음."""
import hashlib
import io
import json
import sqlite3
from contextlib import closing
from pathlib import Path
import pytest
from PIL import Image
from reviewlab.repositories.library import Library
from reviewlab.services.surveys import Surveys,config,TABLES,value
from reviewlab.services.transfer import write_csv,export_backup as reference_backup
from reviewlab.services import survey_transfer
from reviewlab.analysis.surveys import descriptive,cross,figure
from reviewlab.cloud.dataset import read_local,fingerprint


def seed(library,encoding='utf-8-sig'):
    service=Surveys(library);pid=service.project('[가상] 리뷰 설문',purpose='테스트',sample='허구 4명')
    blob=write_csv(['집단','신뢰','만족','선택','수치','날짜','서술','이메일'],[
        ['A','1','5','배달;포털','2.5','2025-01-01','가상 의견','fake@example.invalid'],
        ['A','3','3','포털','3','2025-01-02','허구','fake@example.invalid'],
        ['B','5','1','배달','','','테스트','fake@example.invalid'],
        ['B','','','','','','','fake@example.invalid'],
    ]).decode('utf-8-sig').encode(encoding)
    definitions=service.preview(pid,blob)['definitions']
    definitions['신뢰'].update(kind='리커트 척도형',reverse=True)
    definitions['만족'].update(kind='리커트 척도형')
    definitions['선택'].update(kind='다중 선택형',categories=['배달','포털'])
    definitions['서술']['kind']='자유서술형'
    service.upload(pid,blob,definitions,True)
    return service,pid,blob,definitions


@pytest.mark.parametrize('encoding',['utf-8','utf-8-sig','cp949'])
def test_upload_encoding_privacy_and_duplicate_file(library,encoding):
    service,pid,blob,defs=seed(library,encoding)
    assert service.preview(pid,blob,defs)['repeated']
    with pytest.raises(ValueError,match='이미'): service.upload(pid,blob,defs,True)
    frame,questions=service.frame(pid)
    assert len(frame)==4 and '이메일' not in frame
    assert all('이메일' not in r['config_json'] for r in questions)
    assert all('fake@' not in r['raw_value'] for r in service.rows('survey_answers'))
    record=service.rows('survey_imports',pid)[0]
    assert record['file_sha256']==hashlib.sha256(blob).hexdigest()
    assert json.loads(record['settings_json'])['excluded']==0
    assert Surveys(Library(library.path,library.mode)).frame(pid)[0].equals(frame)


def test_known_statistics_denominators_reverse_and_cross(library):
    service,pid,_,_=seed(library);frame,qs=service.frame(pid);questions={q['variable']:q for q in qs}
    meta,table=descriptive(frame,questions['신뢰'])
    assert meta['total']==4 and meta['valid']==3 and meta['missing']==1
    assert meta['mean']==meta['median']==3 and meta['std']==2
    assert frame['신뢰'].dropna().tolist().count(5)==1
    meta,table=descriptive(frame,questions['선택'])
    assert meta['denominator']==3 and table['빈도'].sum()==4
    assert table['비율(%)'].tolist()==pytest.approx([200/3,200/3])
    q=dict(questions['선택']);c=json.loads(q['config_json']);c['missing']='전체 응답 기준';q['config_json']=json.dumps(c)
    assert descriptive(frame,q)[1]['비율(%)'].tolist()==[50,50]
    assert 'mean' not in descriptive(frame,questions['집단'])[0]
    meta,counts,percents,group=cross(frame,questions['집단'],questions['만족'])
    assert meta['n']==3 and meta['excluded']==1 and group.loc['A','mean']==4
    assert percents.loc['A'].sum()==100
    meta,*_=cross(frame,questions['신뢰'],questions['만족'])
    assert meta['correlation']==pytest.approx(1)
    filtered=frame[frame['집단']=='A'];assert descriptive(filtered,questions['신뢰'])[0]['mean']==4
    with pytest.raises(ValueError): cross(frame,questions['선택'],questions['서술'])


@pytest.mark.parametrize('payload',[b'bad,bad\n1,2',b',empty\n1,2',b'one,two\n1',b'\xff'])
def test_invalid_csv_has_no_partial_write(library,payload):
    service,pid,_,_=seed(library);before=read_local(library.path,library.mode)
    with pytest.raises(ValueError): service.preview(pid,payload)
    assert read_local(library.path,library.mode)==before


def test_empty_rows_duplicates_bad_values_and_question_changes(library):
    s=Surveys(library);pid=s.project('가상')
    blob=write_csv(['신뢰'],[['1'],['1'],[''],['99']]);defs=s.preview(pid,blob)['definitions'];defs['신뢰']['kind']='리커트 척도형'
    report=s.preview(pid,blob,defs)
    assert report['excluded']==1 and report['duplicates']==1 and report['errors'][0]['row']==5
    with pytest.raises(ValueError): s.upload(pid,blob,defs,True)
    assert not s.rows('survey_responses',pid)
    good=write_csv(['신뢰'],[['1'],['1'],['']]);s.upload(pid,good,defs,True)
    assert len(s.frame(pid)[0])==2
    q=s.rows('survey_questions',pid)[0];defs['신뢰']['maximum']=2;defs['신뢰']['reverse']=True
    original=[r['raw_value'] for r in s.rows('survey_answers')]
    s.update_question(q['id'],'신뢰 수정','trust',defs['신뢰'])
    assert s.frame(pid)[0]['trust'].tolist()==[2,2]
    assert [r['raw_value'] for r in s.rows('survey_answers')]==original


@pytest.mark.parametrize('kind',['막대','가로 막대','도넛','리커트 분포','히트맵'])
def test_png_korean_metadata_and_empty(library,kind):
    s,pid,_,_=seed(library);frame,qs=s.frame(pid);q=next(q for q in qs if q['variable']=='신뢰')
    _,table=descriptive(frame,q)
    if kind=='히트맵': table=table.set_index('응답')[['빈도']]
    context={'title':'가상 설문 한국어','caption':'example | N=4 | 결측 1 | 유효 응답 기준'}
    blob=figure(table,kind,context);image=Image.open(io.BytesIO(blob));image.load()
    assert image.format=='PNG' and '유효 응답' in image.info['Description']
    assert figure(table.iloc[:0],kind,context).startswith(b'\x89PNG')


def test_backup_roundtrip_all_histories_atomic_restore_and_mode(tmp_path,library,service,source,event):
    sid=service.save(source);service.verify(sid,event,True);old=reference_backup(library)
    s,pid,_,_=seed(library)
    frame,qs=s.frame(pid);meta,table=descriptive(frame,qs[0]);s.save_analysis(pid,{'ids':frame['id'].tolist()},{'meta':meta})
    blob=survey_transfer.export_backup(library);path=tmp_path/'한글.csv';path.write_bytes(blob)
    target=Library(tmp_path/'target.db','actual');service2=__import__('reviewlab.services.research',fromlist=['Research']).Research(target);service2.save(source)
    prior=reference_backup(target)
    assert survey_transfer.preview(target,path.read_bytes())['added']>0
    survey_transfer.restore(target,path.read_bytes(),True)
    assert survey_transfer.export_backup(target)==blob
    assert reference_backup(target)==prior and reference_backup(library)==old
    assert survey_transfer.restore(target,blob,True)==0
    with pytest.raises(ValueError): survey_transfer.restore(target,b'corrupt',True)
    assert survey_transfer.export_backup(target)==blob
    with pytest.raises(ValueError): survey_transfer.restore(Library(tmp_path/'example.db','example'),blob,True)
    assert s.rows('survey_analysis_runs',pid)
    assert read_local(library.automatic_backups()[0],'actual')['survey_answers']


def test_sqlite_v2_migration_and_rollback_preserves_references(tmp_path,source,monkeypatch):
    path=tmp_path/'old.db';root=Path('reviewlab/db/migrations')
    with closing(sqlite3.connect(path)) as db,db:
        db.executescript((root/'001_initial.sql').read_text()+(root/'002_reviews.sql').read_text())
        db.execute("INSERT INTO project_metadata VALUES(1,'actual')")
        db.execute("INSERT INTO schema_migrations VALUES(1,'old'),(2,'old')")
    from reviewlab.services.research import Research
    original=Path.read_text
    def bad(path,*a,**kw):
        text=original(path,*a,**kw)
        return text+'; INVALID SQL;' if path.name=='003_surveys.sql' else text
    before=path.read_bytes()
    with monkeypatch.context() as m:
        m.setattr(Path,'read_text',bad)
        with pytest.raises(sqlite3.Error): Library(path,'actual')
    assert path.read_bytes()==before
    library=Library(path,'actual');Research(library).save(source)
    with library.connect() as db: assert db.execute('SELECT max(version) FROM schema_migrations').fetchone()[0]==4
    backups=list((tmp_path/'backups').glob('before-change-*'));assert backups
    assert all(read_local(p,'actual')['survey_projects']==[] for p in backups)


def test_example_sample_rejected_and_sensitive_values_detected(library):
    s=Surveys(library);pid=s.project('가상 테스트')
    with pytest.raises(ValueError,match='예시'):s.preview(pid,write_csv(['데이터 구분','문항'],[['가상·테스트용','예']]))
    defs=s.preview(pid,write_csv(['연락','문항'],[['test@example.invalid','예']]))['definitions']
    assert defs['연락']['kind']=='분석 제외'


def test_cloud_survey_sync_conflict_migration_and_restore(tmp_path):
    from cloud_helpers import MemoryServer
    from reviewlab.cloud.library import CloudLibrary
    from reviewlab.cloud.client import CloudConflict
    from reviewlab.cloud.migration import prepare,execute
    server=MemoryServer();a=CloudLibrary(server.client(),server.workspace,'example',tmp_path/'a')
    b=CloudLibrary(server.client(),server.workspace,'example',tmp_path/'b')
    try:
        s,pid,_,_=seed(a);b.reload()
        assert fingerprint(a.dataset())==fingerprint(b.dataset())
        s.update_project(pid,dict(name='가상 수정',purpose='',period_start='',period_end='',sample='',method='',memo=''))
        with pytest.raises(CloudConflict):Surveys(b).project('오래된 세션')
        b.reload();assert Surveys(b).rows('survey_projects')[0]['name']=='가상 수정'
        local=Library(tmp_path/'local.db','example');seed(local)
        result=prepare(b,local.path);execute(b,result,True,True,True)
        assert len(Surveys(b).rows('survey_projects'))==2
        example_blob=survey_transfer.export_backup(b)
        assert survey_transfer.restore(b,example_blob,True)==0
    finally:a.close();b.close()


@pytest.mark.parametrize('kind,raw',[('리커트 척도형','0'),('리커트 척도형','6'),('리커트 척도형','1.5'),('수치형','NaN'),('날짜형','2025-02-30'),('다중 선택형','정의되지않은값')])
def test_wrong_values_reject_entire_upload(library,kind,raw):
    s=Surveys(library);pid=s.project('가상 입력 오류')
    blob=write_csv(['문항'],[[raw]])
    c=config('문항',[]);c.update(kind=kind,categories=['선택A'] if kind=='다중 선택형' else [])
    assert s.preview(pid,blob,{'문항':c})['errors']
    with pytest.raises(ValueError):s.upload(pid,blob,{'문항':c},True)
    assert not s.rows('survey_questions',pid) and not s.rows('survey_imports',pid)


def test_corrupt_backup_missing_answer_or_foreign_analysis_id_is_atomic(library,tmp_path):
    s,pid,_,_=seed(library);s.save_analysis(pid,{'input_ids':s.frame(pid)[0]['id'].tolist()},{'test':'허구'})
    import csv
    original=survey_transfer.export_backup(library)
    rows=list(csv.DictReader(io.StringIO(original.decode('utf-8-sig'))))
    for field in ('survey_answers','survey_analysis_runs'):
        data=json.loads(rows[0]['payload'])
        if field=='survey_answers':data[field].pop()
        else:data[field][0]['config_json']=json.dumps({'input_ids':['missing-private-id']})
        blob=write_csv(['format','mode','payload'],[['reviewlab-survey-v1','actual',json.dumps(data)]])
        before=fingerprint(read_local(library.path,'actual'))
        with pytest.raises(ValueError): survey_transfer.restore(library,blob,True)
        assert fingerprint(read_local(library.path,'actual'))==before
