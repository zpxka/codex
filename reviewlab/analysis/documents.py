"""초고를 대신 작성하지 않는 위치·문장 비교와 규칙 기반 검토 제안."""
import difflib,re


def sentences(text):return [s.strip() for s in re.split(r'(?<=[.!?。])\s+|\n+',text) if s.strip()]
def compare(old,new):
    a,b=sentences(old),sentences(new);rows=[]
    for op,i,j,k,l in difflib.SequenceMatcher(None,a,b,autojunk=False).get_opcodes():
        if op!='equal':rows.append(dict(change={'insert':'추가','delete':'삭제','replace':'수정'}[op],before='\n'.join(a[i:j]),after='\n'.join(b[k:l]),old_position=i+1,new_position=k+1))
    return rows


def suggestions(text):
    rows=[]
    for i,s in enumerate(sentences(text),1):
        if re.search(r'모든|반드시|항상|절대|증명|때문에',s):rows.append(dict(sentence=i,text=s,suggestion='과도한 일반화·인과 표현 가능성: 표본/조건과 근거를 대조하세요.'))
        if re.search(r'연구|조사|통계|\d+\s*%',s) and not re.search(r'\[[^\]]+\]|\([^)]*\d{4}[^)]*\)',s):rows.append(dict(sentence=i,text=s,suggestion='인용 누락 가능성: 실제 출처 연결을 확인하세요.'))
        if len(s)>180 or '  ' in s or re.search(r'(\b\w+\b)\s+\1',s):rows.append(dict(sentence=i,text=s,suggestion='긴 문장·중복어·연속 공백 표현을 검토하세요.'))
    for heading in ('서론','본론','결론'):
        if heading not in text:rows.append(dict(sentence=0,text='',suggestion=heading+' 표제를 찾지 못했습니다. 문서 구조를 직접 확인하세요.'))
    return rows


def claim_checks(claim,evidence):
    """문자 패턴 차이만 표시한다. 의미적 모순 여부를 확정하지 않는다."""
    result=[]
    missing=set(re.findall(r'\d+(?:\.\d+)?%?',claim))-set(re.findall(r'\d+(?:\.\d+)?%?',evidence))
    if missing:result.append('검토 제안: 주장 속 수치가 선택한 근거에 없습니다. 단위·표본·계산 과정을 확인하세요.')
    negative=lambda t:bool(re.search(r'없|아니|않|not\b|no\b',t,re.I))
    if negative(claim)!=negative(evidence):result.append('검토 제안: 부정 표현이 다릅니다. 문맥과 연구 조건을 대조하세요.')
    if not result:result.append('규칙으로 차이를 찾지 못했습니다. 근거가 주장을 입증한다는 뜻은 아닙니다.')
    return result
