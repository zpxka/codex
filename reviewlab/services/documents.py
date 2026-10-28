"""원본 불변 파일 저장·추출·위치 기반 근거. 외부 URL 수집 없음."""
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path
from uuid import uuid4
from reviewlab.models import now

TABLES=('document_files','document_notes')
MAX_FILE=10*1024*1024
MAX_TEXT=1024*1024
CATEGORIES=('연구 목적','연구 질문','방법·대상','주요 변수','핵심 결과','한계','리뷰 공정성 관련','공통 결과','다른 결과','방법 차이','표본·환경 차이','주장 검토','수정 의견')


def digest(blob):return hashlib.sha256(blob).hexdigest()
def packed(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False).encode('utf-8')


def extract(blob,extension):
    if not blob or len(blob)>MAX_FILE:raise ValueError('파일은 비어 있지 않은 10MB 이하 파일이어야 합니다.')
    units=[];status='ready'
    try:
        if extension=='txt':
            text=blob.decode('utf-8-sig')
            if '\x00' in text:raise ValueError('텍스트에 허용되지 않는 NUL 문자가 있습니다. UTF-8 TXT 파일을 확인하세요.')
            units=[dict(location=f'문단 {i}',text=p) for i,p in enumerate(text.splitlines(),1) if p.strip()]
        elif extension=='pdf':
            from pypdf import PdfReader
            if not blob.startswith(b'%PDF-'):raise ValueError('PDF 파일 서명이 올바르지 않습니다. 원본 형식을 확인하세요.')
            reader=PdfReader(io.BytesIO(blob))
            if reader.is_encrypted:return 'encrypted',[]
            if len(reader.pages)>200:raise ValueError('PDF는 200페이지 이하여야 합니다.')
            blank=0
            for i,page in enumerate(reader.pages,1):
                text=page.extract_text() or ''
                if not text.strip():blank+=1
                units.append(dict(location=f'페이지 {i}',text=text))
            if blank:status='ocr_needed' if blank==len(units) else 'partial_ocr_needed'
        elif extension=='docx':
            from docx import Document
            from docx.text.paragraph import Paragraph
            from docx.table import Table
            with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                if sum(r.file_size for r in archive.infolist())>40*1024*1024 or len(archive.infolist())>2000:raise ValueError('DOCX 압축 해제 한도를 넘었습니다.')
            doc=Document(io.BytesIO(blob));p=t=0
            for node in doc.element.body:
                if node.tag.endswith('}p'):
                    p+=1;units.append(dict(location=f'문단 {p}',text=Paragraph(node,doc).text))
                elif node.tag.endswith('}tbl'):
                    t+=1
                    for n,row in enumerate(Table(node,doc).rows,1):units.append(dict(location=f'표 {t} 행 {n}',text=' | '.join(c.text for c in row.cells)))
        else:raise ValueError('PDF·DOCX·UTF-8 TXT만 지원합니다.')
        if len(packed(dict(extractor='reviewlab-v1',units=units)))>MAX_TEXT:raise ValueError('추출 텍스트는 문서별 1MB 이하여야 합니다.')
        if not any(u['text'].strip() for u in units):status='ocr_needed' if extension=='pdf' else 'empty'
        return status,units
    except (UnicodeError,zipfile.BadZipFile):raise ValueError('파일 형식/UTF-8 인코딩이 잘못되었습니다.') from None
    except Exception as error:
        if isinstance(error,ValueError):raise
        return 'extraction_failed',[]


class Files:
    """불변 해시 경로. 자동 삭제·덮어쓰기 없음. Storage는 사용자 JWT만 사용."""
    bucket='reviewlab-originals'
    def __init__(self,library):
        self.library=library
        self.root=Path(library.root if getattr(library,'is_cloud',False) else library.path.parent)/'documents'
    def key(self,sha):
        if not re.fullmatch('[a-f0-9]{64}',sha):raise ValueError('파일 해시 형식 오류입니다.')
        if getattr(self.library,'is_cloud',False):return f'{self.library.workspace_id}/{self.library.mode}/{sha}'
        return sha
    def remote(self,method,sha,blob=None):
        import requests
        client=self.library.client;client.refresh()
        try:
            response=client.http.request(method,client.url+'/storage/v1/object/'+('authenticated/' if method=='GET' else '')+self.bucket+'/'+self.key(sha),headers={'apikey':client.key,'Authorization':'Bearer '+client.access_token,'Content-Type':'application/octet-stream','x-upsert':'false'},data=blob,timeout=(5,30),allow_redirects=False,stream=method=='GET')
            if response.status_code>=300:
                response.close();raise ValueError('Private Storage 요청 실패. 버킷·권한·연결을 확인하세요. 자동 재전송하지 않습니다.')
            if method!='GET':response.close();return b''
            result=bytearray()
            try:
                for chunk in response.iter_content(65536):
                    result.extend(chunk)
                    if len(result)>MAX_FILE:raise ValueError('파일 다운로드 한도 초과')
            finally:response.close()
            return bytes(result)
        except requests.RequestException:raise ValueError('파일 저장 결과 또는 다운로드를 확인하지 못했습니다. 자동 재전송하지 않습니다.') from None
    def put(self,blob):
        if len(blob)>MAX_FILE:raise ValueError('파일 한도 초과')
        sha=digest(blob);key=self.key(sha)
        if getattr(self.library,'is_cloud',False):
            # 동일 객체는 내려받아 검증한 경우에만 재사용. 404만 신규 업로드 허용.
            client=self.library.client;client.refresh()
            import requests
            try:
                r=client.http.request('HEAD',client.url+'/storage/v1/object/authenticated/'+self.bucket+'/'+key,headers={'apikey':client.key,'Authorization':'Bearer '+client.access_token},timeout=(5,30),allow_redirects=False)
                code=r.status_code;r.close()
            except requests.RequestException:raise ValueError('파일 존재 여부를 확인하지 못했습니다.') from None
            if code==200:self.get(sha);return sha
            if code!=404:raise ValueError('Storage 접근을 확인하세요. 저장하지 않았습니다.')
            self.remote('POST',sha,blob)
            self.get(sha)  # 저장 성공/해시 확인 후에만 metadata 연결
        else:
            self.root.mkdir(parents=True,exist_ok=True)
            self.root.chmod(0o700)
            path=self.root/key
            if path.exists():self.get(sha);return sha
            import os
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as f:f.write(blob)
        return sha
    def get(self,sha):
        key=self.key(sha)
        if getattr(self.library,'is_cloud',False):
            blob=self.remote('GET',sha)
        else:
            path=self.root/key
            if path.stat().st_size>MAX_FILE:raise ValueError('파일 크기 한도 초과')
            blob=path.read_bytes()
        if len(blob)>MAX_FILE or digest(blob)!=sha:raise ValueError('원문 파일이 없거나 해시가 일치하지 않습니다.')
        return blob


class Documents:
    def __init__(self,library):self.library=library;self.files=Files(library)
    def rows(self,table='document_files'):
        if table not in TABLES:raise ValueError('테이블 오류')
        with self.library.connect() as db:return [dict(r) for r in db.execute('SELECT * FROM '+table+' ORDER BY created_at,id')]
    def upload(self,blob,name,source_id=None,kind='source'):
        if kind not in ('source','draft') or (kind=='source' and not source_id):raise ValueError('연결할 참고문헌을 선택하세요.')
        reference={}
        if kind=='source':
            source=next((r for r in self.library.list_sources() if r['id']==source_id),None)
            if source is None:raise ValueError('연결할 참고문헌이 없습니다.')
            reference={k:source[k] for k in ('id','title','authors','publication_date','original_url')}
        extension=Path(name).suffix.lower().lstrip('.')
        if any(r['sha256']==digest(blob) and r['source_id']==source_id and r['kind']==kind for r in self.rows()):raise ValueError('이미 연결한 동일 파일입니다.')
        status,units=extract(blob,extension)
        sha=self.files.put(blob);text_sha=self.files.put(packed(dict(extractor='reviewlab-v1',units=units)))
        row=dict(id=str(uuid4()),source_id=source_id,title=Path(name).name,reference_json=json.dumps(reference,ensure_ascii=False),kind=kind,format=extension,sha256=sha,text_sha256=text_sha,status=status,created_at=now())
        with self.library.connect() as db:self.insert(db,'document_files',row)
        return row
    @staticmethod
    def insert(db,table,row):db.execute('INSERT INTO '+table+' ('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')',tuple(row.values()))
    def units(self,doc_id,require_ready=True):
        row=next((r for r in self.rows() if r['id']==doc_id),None)
        if not row or (require_ready and row['status']!='ready'):raise ValueError('정상 추출된 원문이 필요합니다. OCR/추출 실패 상태를 확인하세요.')
        self.files.get(row['sha256'])
        return json.loads(self.files.get(row['text_sha256']))['units']
    def search(self,ids,query):
        terms=re.findall(r'\w+',query.lower());results=[]
        if not terms:return []
        for doc_id in ids:
            for unit in self.units(doc_id):
                # 긴 페이지도 위치+문자 offset을 유지하며 1,500자 단위로 검색한다.
                for offset in range(0,len(unit['text']),1200):
                    text=unit['text'][offset:offset+1500];score=sum(t in text.lower() for t in terms)
                    if score:results.append(dict(document_id=doc_id,location=unit['location'],offset=offset,quote=text,score=score))
        return sorted(results,key=lambda r:r['score'],reverse=True)[:30]
    def validate_evidence(self,evidence,scope):
        if not evidence:raise ValueError('원문에서 확인되지 않음: 근거가 없습니다.')
        for item in evidence:
            if item['document_id'] not in scope or not item['quote'].strip():raise ValueError('선택 범위 밖 또는 빈 근거입니다.')
            units=self.units(item['document_id'])
            if not any(u['location']==item['location'] and item['quote'] in u['text'] for u in units):raise ValueError('원문에서 확인되지 않음: 인용 문장/위치 불일치')
    def note(self,document_id,category,content,evidence,scope,state='미확인'):
        if category not in CATEGORIES or state not in ('미확인','연구자 확인','보류') or not content.strip() or len(content)>20000:raise ValueError('분석 분류·내용·확인 상태를 입력하세요.')
        if document_id not in scope:raise ValueError('분석 문서 범위 오류')
        self.validate_evidence(evidence,scope)
        if category=='주장 검토':
            kinds={r['id']:r['kind'] for r in self.rows()}
            if {kinds[e['document_id']] for e in evidence}!={'draft','source'}:raise ValueError('주장 검토에는 초고 문장과 참고문헌 근거를 함께 선택하세요.')
        row=dict(id=str(uuid4()),document_id=document_id,category=category,content=content,scope_json=json.dumps(scope),evidence_json=json.dumps(evidence,ensure_ascii=False),state=state,created_at=now())
        with self.library.connect() as db:self.insert(db,'document_notes',row)
        return row
