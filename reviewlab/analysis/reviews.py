"""기술 통계와 재현 가능한 탐색. 가짜/AI 리뷰 판정은 수행하지 않는다."""
from collections import Counter
from itertools import combinations
import re
import pandas as pd

UNKNOWN = '미확인'


def select(rows, filters):
    result = []
    for r in rows:
        if filters.get('exclude', True) and r['status'] == '분석 제외':
            continue
        if filters.get('platforms') and (r['platform'] or UNKNOWN) not in filters['platforms']:
            continue
        if filters.get('ratings') and (str(r['rating']) if r['rating'] is not None else UNKNOWN) not in filters['ratings']:
            continue
        if filters.get('events') and r['event_mentioned'] not in filters['events']:
            continue
        if filters.get('start') or filters.get('end'):
            if not r['review_date']:
                if not filters.get('unknown_dates', False):
                    continue
            elif (filters.get('start') and r['review_date'] < filters['start']) or (filters.get('end') and r['review_date'] > filters['end']):
                continue
        keyword = filters.get('keyword','').casefold()
        haystack = ' '.join([r['content'] or '', r['raw'].get('research_memo','')] + [n['memo'] for n in r['notes']]).casefold()
        if keyword not in haystack:
            continue
        result.append(r)
    return result


def statistics(rows, group='platform', metric='자료 수'):
    if group not in ('platform','industry','event_mentioned') or metric not in ('자료 수','평균 별점','중앙 리뷰 길이'):
        raise ValueError('지원하지 않는 비교 기준입니다.')
    records = []
    for r in rows:
        records.append(dict(platform=r['platform'] or UNKNOWN,industry=r['industry'] or UNKNOWN,
            rating=r['rating'],rating_label=str(r['rating']) if r['rating'] is not None else UNKNOWN,
            event_mentioned=r['event_mentioned'],review_date=r['review_date'],
            length=len(r['content']) if r['content'] is not None else None,
            words=len(r['content'].split()) if r['content'] is not None else None))
    df = pd.DataFrame(records,columns=['platform','industry','rating','rating_label','event_mentioned','review_date','length','words'])
    rating = df.groupby(['platform','rating_label'],dropna=False).size().reset_index(name='count')
    event = df.groupby(['platform','event_mentioned']).size().reset_index(name='count')
    if not event.empty:
        event['denominator'] = event.groupby('platform')['count'].transform('sum')
        event['percent'] = event['count']/event['denominator']*100
    else:
        event = pd.DataFrame(columns=['platform','event_mentioned','count','denominator','percent'])
    lengths = df.dropna(subset=['length'])[['rating_label','length','words']]
    dates = df.dropna(subset=['review_date']).groupby('review_date').size().reset_index(name='count')
    industry = df.groupby('industry').agg(count=('platform','size'),rating_n=('rating','count'),
        mean_rating=('rating','mean'),text_n=('length','count'),median_length=('length','median')).reset_index()
    comparison = df.groupby(group).agg(count=('platform','size'),rating_n=('rating','count'),
        mean_rating=('rating','mean'),text_n=('length','count'),median_length=('length','median')).reset_index().rename(columns={group:'group'})
    comparison['value'] = comparison[{'자료 수':'count','평균 별점':'mean_rating','중앙 리뷰 길이':'median_length'}[metric]]
    comparison['denominator'] = comparison[{'자료 수':'count','평균 별점':'rating_n','중앙 리뷰 길이':'text_n'}[metric]]
    yes = sum(r['event_mentioned']=='예' for r in rows)
    known = sum(r['event_mentioned'] != UNKNOWN for r in rows)
    summary = dict(total=len(rows),rating_n=int(df['rating'].count()),text_n=len(lengths),date_n=int(df['review_date'].count()),
        event_yes=yes,event_known=known,event_percent_all=yes/len(rows)*100 if rows else None,
        event_percent_known=yes/known*100 if known else None)
    return dict(ratings=rating,events=event,lengths=lengths,dates=dates,industries=industry,comparison=comparison),summary


def patterns(rows, threshold=.8, cap=200):
    if not .5 <= threshold <= 1 or not 2 <= cap <= 300:
        raise ValueError('유사도는 0.5~1, 비교 한도는 2~300입니다.')
    text_rows = [(r['id'],re.sub(r'\s+',' ',r['content'].strip()).casefold()) for r in rows if r['content']]
    counts = Counter(text for _,text in text_rows)
    exact = [dict(review_id=rid,text=text,count=counts[text]) for rid,text in text_rows if counts[text]>1]
    phrases = Counter()
    flags = []
    terms = ('리뷰 이벤트','이벤트 참여','서비스 받','무료 제공','협찬','광고','원고료','체험단')
    for rid,text in text_rows:
        words = text.split()
        phrases.update({' '.join(words[i:i+n]) for n in (2,3) for i in range(len(words)-n+1)})
        for term in terms:
            if term in text:
                pos = text.index(term)
                flags.append(dict(review_id=rid,term=term,evidence=text[max(0,pos-30):pos+len(term)+30]))
    near = []
    candidates = text_rows[:cap]
    def grams(t):
        return {t[i:i+3] for i in range(max(1,len(t)-2))}
    prepared = [(rid,text,grams(text[:1000])) for rid,text in candidates]
    for (a,ta,ga),(b,tb,gb) in combinations(prepared,2):
        if ta == tb:
            continue
        score = len(ga&gb)/len(ga|gb) if ga|gb else 0
        if score >= threshold:
            near.append(dict(review_a=a,review_b=b,similarity=score))
    return dict(exact=exact,phrases=[dict(phrase=p,review_count=n) for p,n in phrases.most_common(50) if n>=2],
        expressions=flags,similar=near,parameters=dict(algorithm='공백 정규화·문자 3-gram Jaccard',threshold=threshold,
            compared=len(candidates),available=len(text_rows),cap=cap,text_limit=1000,compared_ids=[rid for rid,_ in candidates]))


def filter_description(filters):
    parts=[]
    for key,label in (('platforms','플랫폼'),('ratings','별점'),('events','이벤트 언급')):
        parts.append(label+': '+(', '.join(filters.get(key,[])) or '전체'))
    parts.append('키워드: '+(filters.get('keyword') or '없음'))
    parts.append('기간: '+(filters.get('start') or '제한 없음')+' ~ '+(filters.get('end') or '제한 없음'))
    parts.append('기간 내 날짜 미확인 포함: '+('예' if filters.get('unknown_dates') else '아니오'))
    parts.append('연구자 분석 제외 반영: '+('예' if filters.get('exclude',True) else '아니오'))
    return ' / '.join(parts)


def context(project, all_rows, rows, filters):
    dates = sorted(r['review_date'] for r in rows if r['review_date'])
    return dict(project_id=project['id'],project=project['name'],data_kind=project['data_kind'],
        rating_scale=[project['rating_min'],project['rating_max']],project_n=len(all_rows),selected_n=len(rows),
        filtered_out=len(all_rows)-len(rows),review_excluded=sum(r['status']=='분석 제외' for r in all_rows),
        period=[dates[0],dates[-1]] if dates else ['미확인','미확인'],
        unknown_dates=sum(not r['review_date'] for r in rows),filters=filters,filter_description=filter_description(filters),
        caution='기술 통계입니다. 표본 대표성·통계적 유의성·인과관계·가짜/AI 생성 여부를 입증하지 않습니다.')
