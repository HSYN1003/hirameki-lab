"""Strict, bounded extractors. Unknown structures stop instead of guessing numbers."""
from __future__ import annotations
import calendar, hashlib, html, json, re, unicodedata
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET
from .core import *

MONTHS={x.lower():i for i,x in enumerate(calendar.month_name) if x}
MONTHS.update({x.lower():i for i,x in enumerate(calendar.month_abbr) if x})

def clean(s):return re.sub(r'\s+',' ',html.unescape(s)).strip()

class Document(HTMLParser):
    """Collect text and unexpanded table cells with span-aware expansion."""
    def __init__(self,body):
        super().__init__(convert_charrefs=True);self.parts=[];self.tables=[];self.links=[];self.meta={};self.times=[]
        self.skip=0;self.table=None;self.row=None;self.cell=None;self.caption=None;self.depth=0;self.anchor=None
        self.feed(body)
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag in ('script','style'):self.skip+=1
        if self.skip:return
        if tag=='meta':self.meta[a.get('property',a.get('name','')).lower()]=a.get('content','')
        if tag=='time' and a.get('datetime'):self.times.append(a['datetime'])
        if tag=='a':self.anchor={'href':a.get('href',''),'text':''}
        if tag=='table':
            if self.table is not None:raise ValueError('nested_table_unsupported')
            self.table={'rows':[],'caption':'','context':clean(''.join(self.parts))[-350:]}
        if tag=='caption' and self.table is not None:self.caption=[]
        if tag=='tr' and self.table is not None:self.row=[]
        if tag in ('td','th') and self.row is not None:
            self.cell={'text':[],'colspan':min(50,int(a.get('colspan','1'))),'rowspan':min(50,int(a.get('rowspan','1')))}
        if tag in ('p','div','tr','h1','h2','h3','h4','br','pre','li','table'):self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in ('script','style'):self.skip=max(0,self.skip-1);return
        if self.skip:return
        if tag=='a' and self.anchor:self.links.append(self.anchor);self.anchor=None
        if tag in ('td','th') and self.cell is not None:
            self.cell['text']=clean(''.join(self.cell['text']));self.row.append(self.cell);self.cell=None
        if tag=='tr' and self.row is not None:self.table['rows'].append(self.row);self.row=None
        if tag=='caption' and self.caption is not None:self.table['caption']=clean(''.join(self.caption));self.caption=None
        if tag=='table' and self.table is not None:
            self.table['grid']=self.expand(self.table['rows']);self.tables.append(self.table);self.table=None
        if tag in ('p','div','tr','h1','h2','h3','h4','pre','li','table'):self.parts.append('\n')
    def handle_data(self,data):
        if self.skip:return
        self.parts.append(data)
        if self.cell is not None:self.cell['text'].append(data)
        if self.caption is not None:self.caption.append(data)
        if self.anchor is not None:self.anchor['text']+=data
    @staticmethod
    def expand(rows):
        occupied={};out=[]
        for i,row in enumerate(rows):
            j=0
            for cell in row:
                while (i,j) in occupied:j+=1
                for a in range(cell['rowspan']):
                    for b in range(cell['colspan']):occupied[(i+a,j+b)]=cell['text']
                j+=cell['colspan']
            width=max((c+1 for (r,c) in occupied if r==i),default=0)
            out.append([occupied.get((i,c),'') for c in range(width)])
        return out
    @property
    def text(self):return '\n'.join(clean(x) for x in ''.join(self.parts).splitlines() if clean(x))

def day_english(s):
    m=re.search(r'\b([A-Za-z]+)\s+(\d{1,2}),?\s+(20\d{2})\b',s)
    if not m or m[1].lower() not in MONTHS:raise ValueError('full_date_missing')
    return datetime(int(m[3]),MONTHS[m[1].lower()],int(m[2]),tzinfo=UTC).date().isoformat()

def publication_date(doc):
    for k in ('article:published_time','datepublished','date'):
        if doc.meta.get(k):
            try:return dt(doc.meta[k]).date().isoformat()
            except ValueError:pass
    for s in doc.times:
        try:return dt(s).date().isoformat()
        except ValueError:pass
    return day_english(doc.text)

def val(s):
    s=clean(s).replace(',','')
    if not re.fullmatch(r'-?\d+(\.\d+)?',s):raise ValueError('nonnumeric_cell')
    return float(s)

def parse_cftc(body,source,url,now,provenance='https_validated'):
    text=Document(body).text
    dates={day_english(x) for x in re.findall(r'Options and Futures Combined Positions as of\s+([^\n]+)',text,re.I)}
    if len(dates)!=1:raise ValueError('expected_single_TFF_COMBINED_date')
    observed=dates.pop()
    if dt(observed)>now:raise ValueError('future_position_date')
    codes={'096742':('GBP/USD',1),'097741':('USD/JPY',-1),'099741':('EUR/USD',1),'232741':('AUD/USD',1),
           '299741':('EUR/GBP',1),'399741':('EUR/JPY',1)}
    rows=[];found=set()
    for match in re.finditer(r'CFTC Code\s*#(\d+)\s+Open Interest is\s+([\d,]+)\s+Positions\s*\n([^\n]+)',text):
        code=match[1]
        if code not in codes:continue
        if code in found:raise ValueError('duplicate_market')
        found.add(code);pair,sign=codes[code];oi=int(match[2].replace(',',''));nums=[int(s.replace(',','')) for s in match[3].split()]
        if len(nums)!=14 or oi<=0 or min(nums)<0:raise ValueError('bad_position_row')
        groups={}
        for i,name in enumerate(('dealer','asset','leveraged','other')):
            lo,sh,sp=nums[i*3:i*3+3];groups[name]={'long':lo,'short':sh,'spreading':sp}
        groups['nonreportable']={'long':nums[-2],'short':nums[-1],'spreading':0}
        for side in ('long','short'):
            if abs(sum(g[side]+g['spreading'] for g in groups.values())-oi)>2:raise ValueError('open_interest_checksum_failed')
        p={'pair':pair,'market_code':code,'report_type':'TFF_COMBINED','open_interest':oi,'groups':groups,'orientation_sign':sign,
           'asset_net_oi_pct':sign*100*(groups['asset']['long']-groups['asset']['short'])/oi,
           'leveraged_net_oi_pct':sign*100*(groups['leveraged']['long']-groups['leveraged']['short'])/oi,
           'direct_cross_contract':code in ('299741','399741'),'probability':None}
        tail=text[match.end():].split('Traders in Financial Futures',1)[0]
        ch=re.search(r'Changes from:\s*([^\n]+?)\s+Total Change is:\s*([-\d,]+)\s*\n([^\n]+)',tail)
        if ch:
            ns=[int(s.replace(',','')) for s in ch[3].split()]
            if len(ns)==14:
                p['change_from']=day_english(ch[1]);p['asset_net_change_contracts']=sign*(ns[3]-ns[4]);p['leveraged_net_change_contracts']=sign*(ns[6]-ns[7])
        rows.append(record('positioning',source,'CFTC',url,observed,p,provenance=provenance,now=now))
    if not {'096742','097741','099741','232741'}.issubset(found):raise ValueError('core_four_markets_missing')
    return rows

def derive_bank_crosses(records,source,publisher,url,published,now,provenance):
    output=list(records);ends=sorted({r['payload']['horizon_end'] for r in records})
    for end in ends:
        direct={r['payload']['pair']:r for r in records if r['payload']['horizon_end']==end}
        if not all(p in direct for p in ('USD/JPY','EUR/USD','GBP/USD','AUD/USD')):continue
        targets={'USD':1.0,'JPY':1/direct['USD/JPY']['payload']['target']}
        refs={'USD':1.0,'JPY':None if direct['USD/JPY']['payload'].get('reference') is None else 1/direct['USD/JPY']['payload']['reference']}
        for c in ('EUR','GBP','AUD'):
            targets[c]=direct[c+'/USD']['payload']['target'];refs[c]=direct[c+'/USD']['payload'].get('reference')
        rd={r['payload'].get('reference_at') for r in direct.values()}
        for pair in PAIRS:
            if pair in direct:continue
            a,b=pair.split('/');p={'pair':pair,'horizon_end':end,'target':targets[a]/targets[b],
              'reference':refs[a]/refs[b] if refs[a] is not None and refs[b] else None,
              'reference_at':next(iter(rd)) if len(rd)==1 else None,'derived':True,'method':'同一銀行・同一終点の対ドル予想の比率'}
            output.append(record('bank_forecast',source,publisher,url,published,p,published=published,provenance=provenance,now=now))
    return output

def parse_mufg(body,source,url,now,provenance='https_validated'):
    doc=Document(body);pub=publication_date(doc);records=[]
    for table in doc.tables:
        grid=table['grid'];header=None
        for i,row in enumerate(grid[:5]):
            if any('Spot close' in x for x in row) and sum(bool(re.fullmatch(r'Q[1-4]\s+20\d\d',x)) for x in row)>=2:
                header=(i,row);break
        if header is None:continue
        idx,head=header;spotcol=next(j for j,x in enumerate(head) if 'Spot close' in x)
        d=re.search(r'(\d{2})\.(\d{2})\.(\d{2})',head[spotcol])
        if not d:raise ValueError('bank_own_spot_date_missing')
        refdate=f'{2000+int(d[3])}-{int(d[2]):02d}-{int(d[1]):02d}'
        horizons={j:qend(x) for j,x in enumerate(head) if re.fullmatch(r'Q[1-4]\s+20\d\d',x)}
        seen=set()
        for row in grid[idx+1:]:
            if row and any(x in ('Consensus','Range') for x in row):break
            label=row[0] if row else ''
            pair={'JPY':'USD/JPY','EUR':'EUR/USD','GBP':'GBP/USD','AUD':'AUD/USD'}.get(label,label)
            if pair not in PAIRS or pair in seen:continue
            if max(horizons)>=len(row):raise ValueError('forecast_row_length_mismatch')
            ref=val(row[spotcol]);seen.add(pair)
            for j,end in horizons.items():
                p={'pair':pair,'target':val(row[j]),'horizon_end':end,'reference':ref,'reference_at':refdate,'derived':False}
                records.append(record('bank_forecast',source,'MUFG',url,pub,p,published=pub,provenance=provenance,now=now))
        # The main overview table includes all four core currencies; don't use an unrelated interest-rate table.
        if all(p in seen for p in ('USD/JPY','EUR/USD','GBP/USD','AUD/USD')):break
    if not records:raise ValueError('MUFG_forecast_table_missing')
    return derive_bank_crosses(records,source,'MUFG',url,pub,now,provenance)

def parse_ing(body,source,url,now,provenance='https_validated'):
    doc=Document(body);records=[]
    # The forecast panel may expose only "16 July". A missing year stays missing.
    pub=None
    for t in doc.tables:
        if not any(re.fullmatch(r'(?:[1-4]Q\d{2}F|Q[1-4]\s*20\d{2})',x) for row in t['grid'][:3] for x in row):continue
        if not any(re.fullmatch(r'[A-Z]{3}/[A-Z]{3}',x) for row in t['grid'] for x in row):continue
        context=t['context'];m=re.search(r'Last updated:\s*(\d{1,2})\s+([A-Za-z]+)\s+(20\d{2})',context,re.I)
        pub=f'{m[3]}-{MONTHS[m[2].lower()]:02d}-{int(m[1]):02d}' if m and m[2].lower() in MONTHS else None
        header=next((row for row in t['grid'][:3] if sum(bool(re.fullmatch(r'[1-4]Q\d{2}F|Q[1-4]\s*20\d{2}',x)) for x in row)>=2),None)
        if not header:continue
        ends={j:qend(x) for j,x in enumerate(header) if re.fullmatch(r'[1-4]Q\d{2}F|Q[1-4]\s*20\d{2}',x)}
        for row in t['grid']:
            pair=next((x for x in row[:2] if x in PAIRS),None)
            if not pair:continue
            if max(ends)>=len(row):raise ValueError('ING_header_alignment_failed')
            for j,end in ends.items():
                p={'pair':pair,'target':val(row[j]),'horizon_end':end,'reference':None,'reference_at':None,'derived':False,
                   'publication_label':re.search(r'Last updated:[^\n]*',context).group(0) if 'Last updated:' in context else '未取得'}
                records.append(record('bank_forecast',source,'ING',url,pub,p,published=pub,provenance=provenance,now=now))
        if records:break
    if not records:raise ValueError('ING_forecast_table_missing')
    return derive_bank_crosses(records,source,'ING',url,pub,now,provenance)

def parse_tankan(body,source,url,now,provenance='https_validated'):
    doc=Document(body);text=unicodedata.normalize('NFKC',doc.text)
    # Find article title, then publication date. Do not use dates from navigation/footer.
    anchor=re.search(r'短観[（(]要旨[）)]\s*[（(](20\d\d)年(\d{1,2})月[）)]',text)
    if not anchor:raise ValueError('tankan_title_missing')
    yy,mm=int(anchor[1]),int(anchor[2]);survey=f'{yy}-{mm:02d}-{calendar.monthrange(yy,mm)[1]:02d}'
    match=re.search(r'(20\d\d)年\s*(\d{1,2})月\s*(\d{1,2})日',text[anchor.end():anchor.end()+200])
    if not match:raise ValueError('tankan_publication_missing')
    pub=f'{int(match[1]):04d}-{int(match[2]):02d}-{int(match[3]):02d}';out=[]
    for t in doc.tables:
        context=unicodedata.normalize('NFKC',t['caption']+' '+t['context'])
        if '想定為替' not in context:continue
        caption=unicodedata.normalize('NFKC',t['caption'])
        currency_label=caption if ('円/ドル' in caption or '円/ユーロ' in caption) else context
        pair='USD/JPY' if '円/ドル' in currency_label else 'EUR/JPY' if '円/ユーロ' in currency_label else None
        if not pair:continue
        grid=t['grid'];header=next((row for row in grid[:4] if any(f'{yy}年{mm}月調査' in x.replace(' ','') for x in row)),None)
        if header is None:raise ValueError('tankan_survey_column_missing')
        col=max(i for i,x in enumerate(header) if f'{yy}年{mm}月調査' in x.replace(' ',''))
        fiscal=None
        for row in grid:
            if col>=len(row):continue
            f=next((re.fullmatch(r'(20\d\d)年度',x) for x in row[:2] if re.fullmatch(r'(20\d\d)年度',x)),None)
            if f:fiscal=int(f[1])
            if fiscal is None or any('調査' in x for x in row):continue
            if not f and not any(x in ('上期','下期') for x in row[:2]):continue
            half='H1' if '上期' in row[:2] else 'H2' if '下期' in row[:2] else 'annual'
            try:value=val(row[col])
            except ValueError:continue
            p={'pair':pair,'fiscal_year':fiscal,'half':half,'value':value,'unit':'JPY_per_base','survey_month':f'{yy}-{mm:02d}',
               'population':'全規模・全産業','meaning':'企業の事業計画前提。予想・注文・売買方向ではない'}
            out.append(record('corporate_plan',source,'日本銀行 短観',url,survey,p,published=pub,provenance=provenance,now=now,
                 identity=f'corporate|{survey}|{pair}|{fiscal}|{half}'))
    if not out:raise ValueError('tankan_FX_assumptions_missing')
    return out

def parse_bls(body,source,url,now,provenance='https_validated'):
    doc=Document(body);text=doc.text
    m=re.search(r'(CONSUMER PRICE INDEX|PRODUCER PRICE INDEXES)\s*[-–]\s*([A-Z]+)\s+(20\d\d)',text)
    if not m:raise ValueError('BLS_series_period_missing')
    name='CPI' if m[1].startswith('CONSUMER') else 'PPI';month=MONTHS[m[2].lower()];year=int(m[3]);period=f'{year}-{month:02d}'
    before=text[:m.start()];release=re.search(r'(\d{1,2}):(\d\d)\s*(a\.m\.|p\.m\.)\s*\(ET\)\s*\w+,?\s*([A-Za-z]+\s+\d{1,2},\s+20\d\d)',before)
    if not release:raise ValueError('BLS_release_time_missing')
    h=int(release[1])%12+(12 if release[3]=='p.m.' else 0);day=day_english(release[4]);pub=datetime.fromisoformat(day).replace(hour=h,minute=int(release[2]),tzinfo=ZoneInfo('America/New_York'))
    if pub>now:raise ValueError('embargo_not_lifted')
    paragraph=clean(text[m.end():m.end()+1400]);subject=r'The Consumer Price Index for All Urban Consumers \(CPI-U\)' if name=='CPI' else r'The Producer Price Index for final demand'
    change=re.search(subject+r'\s+(increased|rose|advanced|decreased|fell|declined)\s+(\d+(?:\.\d+)?)\s+percent',paragraph,re.I)
    flat=re.search(subject+r'\s+(?:was |remained )?unchanged',paragraph,re.I)
    if not change and not flat:raise ValueError('headline_monthly_change_missing')
    amount=0.0 if flat else float(change[2])*(-1 if change[1].lower() in ('decreased','fell','declined') else 1)
    if 'seasonally adjusted' not in paragraph[:600]:raise ValueError('seasonal_adjustment_ambiguous')
    p={'currency':'USD','metric':f'US_{name}_HEADLINE','name':f'米{name} 総合/最終需要','period':period,'unit':'percent',
      'change_basis':'mom','adjustment':'SA','actual':amount,'consensus':None,'surprise':None,'previous_initial':None,'previous_revised':None,
      'note':'前月値と市場予想は別。事前保存した予想がなければサプライズ算出なし。'}
    return [record('economic_event',source,'BLS',url,period+'-01',p,published=iso(pub),precision='second',provenance=provenance,now=now,
          identity=f'event|US_{name}_HEADLINE|{period}|mom|SA')]

def feed_datetime(s):
    try:x=dt(s)
    except ValueError:
        x=parsedate_to_datetime(s)
        if x.tzinfo is None:raise ValueError('feed_timezone_absent')
    return x.astimezone(UTC)

def parse_feed(raw,source,url,publisher,currency,now,provenance='https_validated'):
    if len(raw)>2_000_000 or re.search(rb'<!\s*(DOCTYPE|ENTITY)',raw,re.I):raise ValueError('unsafe_XML')
    root=ET.fromstring(raw);local=lambda el:el.tag.rsplit('}',1)[-1]
    if local(root) not in ('rss','feed','RDF'):raise ValueError('not_feed')
    entries=[x for x in root.iter() if local(x) in ('entry','item')];out=[]
    for e in entries[:100]:
        v={local(x):''.join(x.itertext()).strip() for x in e};link=v.get('link','')
        if not link:link=next((x.attrib.get('href','') for x in e if local(x)=='link' and x.attrib.get('rel','alternate')=='alternate'),'')
        date=v.get('pubDate') or v.get('published') or v.get('date');updated=v.get('updated')
        # Do not relabel update time as publication time.
        if not date:continue
        try:d=feed_datetime(date);target=canonical_url(urljoin(url,link))
        except (ValueError,TypeError):continue
        if d>now+timedelta(minutes=5):continue
        title=clean(Document(v.get('title','')).text)[:220]
        if not title:continue
        p={'title':title,'currencies':[currency] if currency else [],'updated_at':updated,
           'direction':None,'role':'official_release' if publisher not in ('ING','MUFG') else 'bank_commentary',
           'importance':'高' if re.search(r'inflation|monetary policy|interest rate|employment|consumer price|介入|金融政策|物価',title,re.I) else '未分類'}
        out.append(record('news',source,publisher,target,iso(d),p,published=iso(d),precision='second',provenance=provenance,now=now,
             identity='news|'+target))
    return out

def parse_social(body,source,url,pair,now,provenance='https_validated'):
    obj=json.loads(body)
    if not isinstance(obj.get('posts'),list):raise ValueError('social_response_invalid')
    out=[];seen=set()
    for x in obj['posts'][:100]:
        text=x.get('record',{}).get('text','');when=x.get('record',{}).get('createdAt');did=x.get('author',{}).get('did');uri=x.get('uri','')
        if not when or not did:continue
        try:d=dt(when)
        except ValueError:continue
        if d>now or d<now-timedelta(hours=24):continue
        canon=re.sub(r'\W','',text).casefold();key=hashlib.sha256(canon.encode()).hexdigest()
        if key in seen:continue
        seen.add(key)
        token=re.escape(pair.replace('/',''));t=unicodedata.normalize('NFKC',text).upper().replace('/','')
        if pair.replace('/','') not in t:continue
        stance='不明'
        neg=bool(re.search(r'\bNOT\b|\bNO\b|\bDON.T\b|\?|？|しない|ではない|利確|決済|CLOSED|COVERED',t))
        buy=bool(re.search(r'(?:LONG|BUY(?:ING)?|BULLISH)\s*\$?#?'+token+r'\b|'+token+r'\s*(?:LONG|BULLISH|買い)',t))
        sell=bool(re.search(r'(?:SHORT|SELL(?:ING)?|BEARISH)\s*\$?#?'+token+r'\b|'+token+r'\s*(?:SHORT|BEARISH|売り)',t))
        if not neg and buy!=sell:stance='買いの明示' if buy else '売りの明示'
        p={'pair':pair,'author_hash':hashlib.sha256(did.encode()).hexdigest()[:20],'text_hash':key,'explicit_stance':stance,
           'excerpt':' '.join(text.split()[:20])[:85],'classification':'限定的な語句判定。自己申告の真偽/予測力は未検証。'}
        posturl='https://bsky.app/profile/'+did+'/post/'+uri.split('/')[-1]
        out.append(record('social_post',source,'Bluesky 公開検索',posturl,iso(d),p,published=iso(d),precision='second',provenance=provenance,now=now,
             identity='social|'+uri))
    return out
