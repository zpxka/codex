"""설문 정의/CSV 진단/원본 보존. 웹/외부 서비스 호출 없음."""
import csv
import io
import json
import hashlib
import math
import re
from datetime import date
from uuid import uuid4
from reviewlab.models import now

TABLES = ('survey_projects','survey_questions','survey_imports','survey_responses','survey_answers','survey_analysis_runs')
TYPES = ('단일 선택형','다중 선택형','리커트 척도형','수치형','자유서술형','날짜형','분석 제외')
PII = re.compile(r'이메일|email|e-mail|전화|연락처|휴대|성명|이름|실명|학번|주소|계정|ip address', re.I)


def config(name, values):
    observed = [v.strip() for v in values if v.strip()]
    kind = '단일 선택형' if len(set(observed)) <= 20 else '자유서술형'
    try:
        numbers = [float(v) for v in observed]
        if numbers: kind = '수치형'
    except ValueError:
        pass
    if observed and all(re.fullmatch(r'\d{4}-\d{2}-\d{2}', v) for v in observed): kind = '날짜형'
    if PII.search(name) or name in ('데이터 구분','id') or any(re.search(r'[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}|0\d{1,2}[- ]\d{3,4}[- ]\d{4}',v) for v in observed): kind = '분석 제외'
    return dict(kind=kind, categories=[], minimum=1, maximum=5, reverse=False, missing='유효 응답 기준', delimiter=';', missing_values=[])


def validate_config(c):
    if set(c) != {'kind','categories','minimum','maximum','reverse','missing','delimiter','missing_values'} or c['kind'] not in TYPES:
        raise ValueError('문항 설정 형식이 올바르지 않습니다.')
    if c['missing'] not in ('유효 응답 기준','전체 응답 기준') or type(c['reverse']) is not bool:
        raise ValueError('결측 처리와 역문항 설정을 확인하세요.')
    if not all(isinstance(c[k],list) and all(isinstance(x,str) for x in c[k]) for k in ('categories','missing_values')):
        raise ValueError('범주·결측값은 문자열 목록이어야 합니다.')
    if c['kind']=='리커트 척도형' and any(type(c[k]) not in (int,float) or not float(c[k]).is_integer() for k in ('minimum','maximum')): raise ValueError('리커트 척도 경계는 정수여야 합니다.')
    if len(set(c['categories']))!=len(c['categories']): raise ValueError('응답 범주가 중복되었습니다.')
    if c['reverse'] and c['kind']!='리커트 척도형': raise ValueError('역문항은 리커트 척도에만 적용합니다.')
    if not isinstance(c['delimiter'],str) or not c['delimiter']: raise ValueError('다중 응답 구분자를 입력하세요.')
    if any(type(c[k]) not in (int,float) or not math.isfinite(c[k]) for k in ('minimum','maximum')) or c['maximum'] <= c['minimum']:
        raise ValueError('척도 범위가 올바르지 않습니다.')


def value(raw, c):
    validate_config(c)
    raw = raw.strip()
    if not raw or raw in c['missing_values'] or c['kind']=='분석 제외': return None
    kind = c['kind']
    if kind in ('수치형','리커트 척도형'):
        try: v = float(raw)
        except ValueError: raise ValueError('숫자로 변환할 수 없습니다.') from None
        if not math.isfinite(v): raise ValueError('유한한 숫자만 허용합니다.')
        if kind=='리커트 척도형':
            if v != int(v) or not c['minimum'] <= v <= c['maximum']: raise ValueError('리커트 척도 범위를 확인하세요.')
            if c['reverse']: v = c['minimum'] + c['maximum'] - v
        return v
    if kind=='날짜형':
        try: return date.fromisoformat(raw).isoformat()
        except ValueError: raise ValueError('날짜는 YYYY-MM-DD로 입력하세요.') from None
    if kind=='다중 선택형':
        v = list(dict.fromkeys(x.strip() for x in raw.split(c['delimiter']) if x.strip()))
        if c['categories'] and set(v)-set(c['categories']): raise ValueError('정의하지 않은 응답 범주입니다.')
        return v or None
    if c['categories'] and kind=='단일 선택형' and raw not in c['categories']: raise ValueError('정의하지 않은 응답 범주입니다.')
    return raw


class Surveys:
    def __init__(self, library): self.library = library
    def rows(self, table, project=None):
        if table not in TABLES: raise ValueError('알 수 없는 설문 테이블입니다.')
        with self.library.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM '+table+(' WHERE project_id=?' if project else '')+' ORDER BY id', (project,) if project else ())]
    @staticmethod
    def insert(db, table, row):
        fields=list(row)
        db.execute(f'INSERT INTO {table} ({",".join(fields)}) VALUES ({",".join("?" for _ in fields)})',tuple(row.values()))
    def project(self, name, **metadata):
        if not name.strip() or len(name)>200: raise ValueError('프로젝트 이름을 입력하세요(200자 이내).')
        fields=('purpose','period_start','period_end','sample','method','memo')
        data={k:metadata.get(k,'') for k in fields}
        for k in ('period_start','period_end'):
            if data[k]: date.fromisoformat(data[k])
        if data['period_start'] and data['period_end'] and data['period_start']>data['period_end']: raise ValueError('조사 기간을 확인하세요.')
        pid=str(uuid4())
        with self.library.connect() as db:
            self.insert(db,'survey_projects',dict(id=pid,name=name,**data,data_kind=self.library.mode,created_at=now()))
        return pid
    def update_project(self,pid,data):
        from datetime import date
        if set(data)!={'name','purpose','period_start','period_end','sample','method','memo'} or not data['name'].strip(): raise ValueError('프로젝트 정보를 확인하세요.')
        for k in ('period_start','period_end'):
            if data[k]: date.fromisoformat(data[k])
        if data['period_start'] and data['period_end'] and data['period_start']>data['period_end']: raise ValueError('조사 기간 오류입니다.')
        with self.library.connect() as db:
            db.execute('UPDATE survey_projects SET '+','.join(k+'=?' for k in data)+' WHERE id=?',(*data.values(),pid))
    def preview(self, pid, blob, definitions=None, encoding='auto'):
        if not any(p['id']==pid for p in self.rows('survey_projects')): raise ValueError('설문 프로젝트를 선택하세요.')
        if len(blob)>10*1024*1024: raise ValueError('설문 CSV는 10MB 이하여야 합니다.')
        text=None
        for codec in (('utf-8-sig','cp949') if encoding=='auto' else (encoding,)):
            if codec not in ('utf-8-sig','utf-8','cp949'): raise ValueError('지원하지 않는 인코딩입니다.')
            try: text=blob.decode(codec); break
            except UnicodeError: pass
        if text is None: raise ValueError('UTF-8 또는 CP949 CSV를 선택하세요.')
        try: records=list(csv.reader(io.StringIO(text,newline=''),strict=True))
        except csv.Error: raise ValueError('CSV 형식이 손상되었습니다.') from None
        if not records or not records[0] or len(records)>5001 or len(records[0])>100: raise ValueError('최대 5,000응답·100문항 CSV를 선택하세요.')
        if any('\x00' in cell for row in records for cell in row): raise ValueError('NUL 제어문자가 포함되어 있습니다.')
        headers=records[0]
        if any(not h.strip() for h in headers) or len(set(headers))!=len(headers): raise ValueError('빈 열 이름 또는 중복 열 이름을 수정하세요.')
        records=[headers]+[row if row else ['']*len(headers) for row in records[1:]]
        if any(len(row)!=len(headers) for row in records[1:]): raise ValueError('열 개수가 다른 행이 있습니다.')
        if self.library.mode=='actual' and '데이터 구분' in headers and any(r[headers.index('데이터 구분')].strip()=='가상·테스트용' for r in records[1:]): raise ValueError('가상 설문은 예시·연습 자료 모드에서만 업로드하세요.')
        definitions=definitions or {h:config(h,[r[i] for r in records[1:]]) for i,h in enumerate(headers)}
        if set(definitions)!=set(headers): raise ValueError('문항 정의와 CSV 열이 일치하지 않습니다.')
        for c in definitions.values(): validate_config(c)
        active=[h for h in headers if definitions[h]['kind']!='분석 제외']
        if not active: raise ValueError('저장할 문항을 하나 이상 선택하세요.')
        errors=[]; accepted=[]; empty=0; duplicates=0; seen=set()
        for index,row in enumerate(records[1:],2):
            data={h:row[headers.index(h)] for h in active}
            if not any(v.strip() for v in data.values()): empty+=1; continue
            digest=hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            duplicates+=int(digest in seen);seen.add(digest)
            for h,raw in data.items():
                try: value(raw,definitions[h])
                except ValueError as e: errors.append(dict(row=index,question=h,error=str(e)))
            accepted.append((index,data))
        sha=hashlib.sha256(blob).hexdigest()
        repeated=any(r['file_sha256']==sha for r in self.rows('survey_imports',pid))
        return dict(definitions=definitions,rows=accepted,total=len(records)-1,excluded=empty,duplicates=duplicates,errors=errors,
                    pii=[h for i,h in enumerate(headers) if h!='데이터 구분' and config(h,[r[i] for r in records[1:]])['kind']=='분석 제외'],sha256=sha,repeated=repeated,encoding=codec)
    def upload(self,pid,blob,definitions,confirmed=False,encoding='auto'):
        if not confirmed: raise ValueError('익명화·문항 설정과 제외 응답을 확인하세요.')
        p=self.preview(pid,blob,definitions,encoding)
        if p['errors']: raise ValueError('오류 행을 수정하세요. 전체 업로드를 중단했습니다.')
        if p['repeated']: raise ValueError('같은 원본 파일을 이미 업로드했습니다.')
        if not p['rows']: raise ValueError('유효 응답이 없습니다.')
        old=self.rows('survey_questions',pid)
        active={h:c for h,c in definitions.items() if c['kind']!='분석 제외'}
        if old and {q['variable']:json.loads(q['config_json']) for q in old}!=active: raise ValueError('기존 문항 정의와 다릅니다. 별도 프로젝트를 사용하세요.')
        iid=str(uuid4()); questions={q['variable']:q['id'] for q in old}
        with self.library.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM survey_imports WHERE project_id=? AND file_sha256=?',(pid,p['sha256'])).fetchone(): raise ValueError('이미 업로드한 파일입니다.')
            if not old:
                for h,c in active.items():
                    qid=str(uuid4());questions[h]=qid
                    self.insert(db,'survey_questions',dict(id=qid,project_id=pid,name=h,variable=h,config_json=json.dumps(c,ensure_ascii=False)))
            self.insert(db,'survey_imports',dict(id=iid,project_id=pid,file_sha256=p['sha256'],settings_json=json.dumps(dict(encoding=p['encoding'],excluded=p['excluded'],duplicates=p['duplicates'],definitions=active),ensure_ascii=False),created_at=now()))
            for index,row in p['rows']:
                rid=str(uuid4())
                self.insert(db,'survey_responses',dict(id=rid,project_id=pid,import_id=iid,source_row=index,created_at=now()))
                for h,raw in row.items(): self.insert(db,'survey_answers',dict(id=str(uuid4()),response_id=rid,question_id=questions[h],raw_value=raw))
        return dict(added=len(p['rows']),excluded=p['excluded'],duplicates=p['duplicates'])
    def update_question(self,qid,name,variable,c):
        validate_config(c)
        if not name.strip() or not variable.strip() or variable=='id': raise ValueError('문항명·변수명을 입력하세요.')
        with self.library.connect() as db:
            if not db.execute('SELECT 1 FROM survey_questions WHERE id=?',(qid,)).fetchone(): raise ValueError('설문 문항이 없습니다.')
            for row in db.execute('SELECT raw_value FROM survey_answers WHERE question_id=?',(qid,)): value(row[0],c)
            db.execute('UPDATE survey_questions SET name=?,variable=?,config_json=? WHERE id=?',(name,variable,json.dumps(c,ensure_ascii=False),qid))
    def original_answers(self,pid):
        with self.library.connect() as db:
            return [dict(r) for r in db.execute('SELECT r.source_row,q.name,a.raw_value FROM survey_answers a JOIN survey_responses r ON r.id=a.response_id JOIN survey_questions q ON q.id=a.question_id WHERE r.project_id=? ORDER BY r.source_row,q.name',(pid,))]
    def frame(self,pid):
        import pandas as pd
        questions=self.rows('survey_questions',pid);responses=self.rows('survey_responses',pid)
        with self.library.connect() as db:
            answers={(r['response_id'],r['question_id']):r['raw_value'] for r in db.execute('SELECT a.* FROM survey_answers a JOIN survey_responses r ON r.id=a.response_id WHERE r.project_id=?',(pid,))}
        rows=[dict(id=r['id'],**{q['variable']:value(answers.get((r['id'],q['id']),''),json.loads(q['config_json'])) for q in questions}) for r in responses]
        return pd.DataFrame(rows,columns=['id']+[q['variable'] for q in questions]),questions
    def save_analysis(self,pid,settings,results):
        known={r['id'] for r in self.rows('survey_responses',pid)}
        if not set(settings.get('input_ids',[])) <= known: raise ValueError('분석 입력 응답이 프로젝트와 다릅니다.')
        with self.library.connect() as db:
            self.insert(db,'survey_analysis_runs',dict(id=str(uuid4()),project_id=pid,config_json=json.dumps(settings,ensure_ascii=False,allow_nan=False),results_json=json.dumps(results,ensure_ascii=False,allow_nan=False),created_at=now()))
