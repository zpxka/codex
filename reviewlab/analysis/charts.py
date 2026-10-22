"""Plotly 화면과 동일한 집계표를 사용하는 PNG 출력(브라우저 설치 불필요)."""
import io
import textwrap
import plotly.express as px
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from importlib.util import find_spec
from pathlib import Path
from matplotlib import font_manager

# 0.1.1의 초기화 코드는 Python 3.12에서 제거된 distutils를 사용한다.
# 패키지에 포함된 정식 폰트 리소스만 공개 Matplotlib API로 등록한다.
font_path = Path(find_spec('koreanize_matplotlib').origin).parent / 'fonts/NanumGothic.ttf'
font_manager.fontManager.addfont(str(font_path))
plt.rcParams['font.family'] = 'NanumGothic'
plt.rcParams['axes.unicode_minus'] = False

TITLES = {'ratings':'플랫폼별 별점 분포 (개)', 'events':'리뷰 이벤트 언급 비율 (전체 리뷰 대비 %)',
          'lengths':'별점별 리뷰 길이 (공백 포함 문자 수)', 'dates':'날짜별 리뷰 수 (개)',
          'comparison':'선택 기준 비교', 'words':'리뷰 단어 수 (공백 단위)'}


CHART_LABELS = dict(platform='플랫폼',rating_label='별점',count='리뷰 수',event_mentioned='이벤트 언급',percent='전체 대비 비율 (%)',length='문자 수',review_date='작성일',words='공백 기준 단어 수',group='비교 집단',value='선택 지표 값',denominator='지표 분모')


def plot(kind,tables):
    if kind=='ratings':
        return px.bar(tables['ratings'],x='platform',y='count',color='rating_label',barmode='group',title=TITLES[kind],labels=CHART_LABELS)
    if kind=='events':
        return px.bar(tables['events'],x='platform',y='percent',color='event_mentioned',title=TITLES[kind],labels=CHART_LABELS)
    if kind=='lengths':
        return px.box(tables['lengths'],x='rating_label',y='length',points='all',title=TITLES[kind],labels=CHART_LABELS)
    if kind=='dates':
        return px.line(tables['dates'],x='review_date',y='count',markers=True,title=TITLES[kind],labels=CHART_LABELS)
    if kind=='words':
        return px.histogram(tables['lengths'],x='words',title=TITLES[kind],labels=CHART_LABELS)
    return px.bar(tables['comparison'],x='group',y='value',hover_data=['denominator'],title=TITLES[kind],labels=CHART_LABELS)


def png(kind,tables,context):
    fig,ax = plt.subplots(figsize=(12,8))
    try:
        table = tables['lengths' if kind=='words' else kind]
        if table.empty:
            ax.text(.5,.5,'표시할 관측값 없음',ha='center',transform=ax.transAxes)
        elif kind in ('ratings','events'):
            color = 'rating_label' if kind=='ratings' else 'event_mentioned'
            value = 'count' if kind=='ratings' else 'percent'
            table.pivot(index='platform',columns=color,values=value).fillna(0).plot.bar(ax=ax,stacked=kind=='events',rot=0)
        elif kind=='lengths':
            groups = list(table.groupby('rating_label'))
            ax.boxplot([g['length'].tolist() for _,g in groups],tick_labels=[n for n,_ in groups])
        elif kind=='words':
            ax.hist(table['words'],bins='auto')
            ax.set_xlabel('공백 기준 단어 수')
        elif kind=='dates':
            ax.plot(table['review_date'],table['count'],marker='o')
            ax.tick_params(axis='x',labelrotation=30)
        else:
            valid=table.dropna(subset=['value'])
            ax.bar(valid['group'],valid['value'])
        ax.set_title(TITLES[kind])
        ax.set_xlabel({'ratings':'플랫폼','events':'플랫폼','lengths':'별점','dates':'작성일','comparison':'비교 집단','words':'공백 기준 단어 수'}[kind])
        ax.set_ylabel({'events':'비율 (%)','lengths':'문자 수','comparison':context.get('comparison','선택 지표 값')}.get(kind,'리뷰 수'))
        if kind in ('ratings','events') and ax.get_legend():
            ax.get_legend().set_title('별점' if kind=='ratings' else '이벤트 언급')
        mode='가상·테스트용 — 논문 근거 사용 금지' if context['data_kind']=='example' else '실제 연구자료'
        lines=[f"{mode} | 프로젝트 {context['project']} | 선택 N={context['selected_n']} / 전체 {context['project_n']}",
            f"작성일 기간: {' ~ '.join(context['period'])} | 날짜 미확인 {context['unknown_dates']}건 | 별점 관측 {context.get('rating_n','미확인')}건 | 내용 관측 {context.get('text_n','미확인')}건",
            f"조건: {context['filter_description']} | 비교: {context.get('comparison','')}",context['caution']]
        footer='\n'.join(textwrap.fill(line,105) for line in lines)
        fig.text(.02,.02,footer,fontsize=8,va='bottom')
        fig.subplots_adjust(bottom=min(.55,.16+.025*len(footer.splitlines())))
        out=io.BytesIO()
        fig.savefig(out,format='png',dpi=140,metadata={'Description':str(context)})
        return out.getvalue()
    finally:
        plt.close(fig)
