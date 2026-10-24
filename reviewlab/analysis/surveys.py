"""기술통계만 제공한다. 결측 대치/유의성/인과 판단 없음."""
import json
import pandas as pd


def descriptive(frame, question):
    c=json.loads(question['config_json']); series=frame[question['variable']]
    present=series[series.notna()]; n=len(frame); valid=len(present)
    denominator=n if c['missing']=='전체 응답 기준' else valid
    meta=dict(total=n,valid=valid,missing=n-valid,denominator=denominator,missing_policy=c['missing'],reverse=c['reverse'])
    kind=c['kind']
    if kind=='분석 제외': return meta,pd.DataFrame(columns=['응답','빈도','비율(%)','분모'])
    if kind in ('수치형','리커트 척도형'):
        meta.update(mean=float(present.mean()) if valid else None,median=float(present.median()) if valid else None,
                    std=float(present.std(ddof=1)) if valid>1 else None,minimum=float(present.min()) if valid else None,maximum=float(present.max()) if valid else None)
    if kind in ('자유서술형','날짜형','수치형'):
        return meta,pd.DataFrame(columns=['응답','빈도','비율(%)','분모'])
    counts=present.explode().value_counts(sort=False) if kind=='다중 선택형' else present.value_counts(sort=False)
    categories=c['categories'] or list(counts.index)
    if kind=='리커트 척도형': categories=list(range(int(c['minimum']),int(c['maximum'])+1))
    result=pd.DataFrame([{'응답':str(k),'빈도':int(counts.get(k,0)),'비율(%)':100*int(counts.get(k,0))/denominator if denominator else 0,'분모':denominator} for k in categories])
    return meta,result


def cross(frame, first, second):
    # pairwise complete: 어느 한쪽 결측이면 제외. 명목형 평균은 허용하지 않는다.
    if first['variable']==second['variable']: raise ValueError('서로 다른 문항을 선택하세요.')
    subset=frame[[first['variable'],second['variable']]].dropna()
    a=json.loads(first['config_json'])['kind']; b=json.loads(second['config_json'])['kind']
    if a not in ('단일 선택형','리커트 척도형') or b not in ('단일 선택형','리커트 척도형'):
        raise ValueError('교차분석은 단일 선택형·리커트 척도형만 지원합니다.')
    counts=pd.crosstab(subset[first['variable']],subset[second['variable']])
    percents=counts.div(counts.sum(axis=1).replace(0,float('nan')),axis=0)*100
    group=subset.groupby(first['variable'])[second['variable']].agg(['count','mean']) if b=='리커트 척도형' else None
    correlation=None
    if a==b=='리커트 척도형' and len(subset)>1:
        v=subset[first['variable']].corr(subset[second['variable']])
        correlation=None if pd.isna(v) else float(v)
    return dict(n=len(subset),excluded=len(frame)-len(subset),policy='두 문항 모두 유효한 응답, 행별 비율 분모=해당 집단 응답 수',correlation=correlation),counts,percents,group


def figure(table, kind, context):
    from reviewlab.analysis.charts import plt  # 동일 한국어 폰트/Agg 사용
    import io
    fig,ax=plt.subplots(figsize=(10,7))
    try:
        if table.empty: ax.text(.5,.5,'분석할 유효 응답 없음',ha='center')
        elif kind=='히트맵':
            ax.imshow(table.to_numpy(dtype=float),aspect='auto')
            ax.set_xticks(range(len(table.columns)),[str(x) for x in table.columns]);ax.set_yticks(range(len(table.index)),[str(x) for x in table.index])
            for i in range(len(table.index)):
                for j in range(len(table.columns)): ax.text(j,i,str(round(float(table.iloc[i,j]),2)),ha='center')
        elif kind=='도넛':
            counts=table['빈도'].tolist();labels=table['응답'].tolist()
            denominator=int(table['분모'].iloc[0]) if '분모' in table else sum(counts)
            if denominator>sum(counts): labels.append('결측');counts.append(denominator-sum(counts))
            if sum(counts): ax.pie(counts,labels=labels,wedgeprops={'width':.4},autopct='%1.1f%%')
        else:
            x,y=('응답','빈도') if '빈도' in table else (table.columns[0],table.columns[-1])
            if kind=='가로 막대': ax.barh(table[x].astype(str),table[y])
            else: ax.bar(table[x].astype(str),table[y])
        ax.set_title(context['title']);fig.text(.02,.02,context['caption'],fontsize=9,wrap=True)
        fig.subplots_adjust(bottom=.28)
        out=io.BytesIO();fig.savefig(out,format='png',metadata={'Description':json.dumps(context,ensure_ascii=False)});return out.getvalue()
    finally: plt.close(fig)
