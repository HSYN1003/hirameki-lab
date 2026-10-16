"""Evidence ledger and deterministic summaries. No price-prediction accuracy claim.
Python 3.12 standard library only. Times are UTC; source date precision is preserved.
"""
from __future__ import annotations
import calendar, copy, hashlib, json, math, re, os, ipaddress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

UTC=timezone.utc
CURRENCIES=['USD','EUR','JPY','AUD','GBP']
PAIRS=['USD/JPY','EUR/USD','GBP/USD','AUD/USD','EUR/JPY','GBP/JPY','AUD/JPY','EUR/GBP','EUR/AUD','GBP/AUD']
MAX_AGE={'bank_forecast':45*24,'positioning':14*24,'corporate_plan':100*24,
         'news':72,'economic_event':72,'social_post':24,'reference_rate':96,'option_vol':24,'interpretation':24}
TRUST={'https_validated','web_primary_extract'}
DIRECTION={1:'上方向',0:'同水準',-1:'下方向'}

def utcnow():return datetime.now(UTC)
def iso(d):return d.astimezone(UTC).isoformat(timespec='seconds')
def dt(s):
    if not isinstance(s,str) or not s:raise ValueError('date_missing')
    x=datetime.fromisoformat(s.replace('Z','+00:00'))
    if x.tzinfo is None:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}',s):return x.replace(tzinfo=UTC)
        raise ValueError('timezone_missing')
    return x.astimezone(UTC)
def age_hours(s,now):return (now-dt(s)).total_seconds()/3600

def jhash(x):return hashlib.sha256(json.dumps(x,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def atomic_json(path:Path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    os.replace(tmp,path)

def safe_url(s):
    p=urlsplit(s)
    if p.scheme!='https' or not p.hostname or p.username or p.password or p.port not in (None,443):raise ValueError('unsafe_url')
    if p.hostname in ('localhost','127.0.0.1','::1') or p.hostname.endswith('.local'):raise ValueError('local_url')
    try: addr=ipaddress.ip_address(p.hostname)
    except ValueError: addr=None
    if addr is not None and not addr.is_global:raise ValueError('nonpublic_url')
    return urlunsplit(('https',p.netloc.lower(),p.path,p.query,''))

def canonical_url(s):
    s=safe_url(s);p=urlsplit(s)
    query=sorted((k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if not k.lower().startswith('utm_') and k.lower() not in ('fbclid','gclid','mc_cid','mc_eid'))
    return urlunsplit((p.scheme,p.netloc,p.path,urlencode(query),''))

def qend(s):
    a=re.fullmatch(r'Q([1-4])\s*(20\d{2})',s.strip(),re.I)
    b=re.fullmatch(r'([1-4])Q(\d{2})(?:F)?',s.strip(),re.I)
    if a:q,y=int(a[1]),int(a[2])
    elif b:q,y=int(b[1]),2000+int(b[2])
    else:raise ValueError('unrecognized_quarter')
    m=q*3;return f'{y:04d}-{m:02d}-{calendar.monthrange(y,m)[1]:02d}'

def record(kind,source,publisher,url,observed,payload,*,published=None,precision='day',provenance='https_validated',identity=None,now=None):
    n=now or utcnow()
    ident=identity or f'{kind}|{source}|{observed}|{payload.get("pair",payload.get("metric",""))}|{payload.get("horizon_end","")}'
    r={'id':hashlib.sha256(ident.encode()).hexdigest()[:24],'kind':kind,'source':source,'publisher':publisher,
       'url':canonical_url(url),'observed_at':observed,'published_at':published,'date_precision':precision,
       'payload':payload,'provenance':provenance,'fetched_at':iso(n)}
    return r

def evidence_date(r):
    # Positions describe Tuesday holdings, never Friday acquisition. Dates cannot be refreshed by fetching.
    return r.get('observed_at') if r['kind'] in ('positioning','reference_rate','option_vol') else r.get('published_at') or r.get('observed_at')

def eligible(r,now,*,horizon='all'):
    if r.get('provenance') not in TRUST:return False,'未再検証/テスト用'
    d=evidence_date(r)
    if not d:return False,'資料日付なし'
    try:a=age_hours(d,now)
    except (ValueError,TypeError):return False,'資料日付不正'
    if a < -0.084:return False,'未来の資料'
    if a>MAX_AGE.get(r['kind'],24):return False,'鮮度期限経過'
    if r['kind']=='bank_forecast':
        end=r['payload'].get('horizon_end')
        if not end or dt(end).date()<now.date():return False,'予測終点経過/不明'
    if horizon=='short' and r['kind']!='interpretation':return False,'短期方向の直接根拠ではない'
    return True,'採用可能（的中率ではない）'

def validate_record(r,now):
    if r['kind'] not in MAX_AGE:raise ValueError('unknown_kind')
    safe_url(r['url'])
    for key in ('observed_at','published_at','fetched_at'):
        if r.get(key) and dt(r[key])>now+timedelta(minutes=5):raise ValueError('future_timestamp')
    p=r['payload']
    if 'pair' in p and p['pair'] not in PAIRS:raise ValueError('unsupported_pair')
    json.dumps(r,allow_nan=False)
    if r['kind']=='bank_forecast':
        if type(p.get('target')) not in (int,float) or p['target']<=0:raise ValueError('invalid_forecast')
        if p.get('reference') is not None and p['reference']<=0:raise ValueError('invalid_reference')
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',p.get('horizon_end','')):raise ValueError('forecast_horizon_missing')
    if r['kind']=='economic_event':
        if p.get('actual') is None or type(p['actual']) not in (int,float):raise ValueError('actual_missing')
        if p.get('change_basis') not in ('mom','yoy','level','qoq','qoq_annualized'):raise ValueError('basis_missing')
    if r['kind']=='interpretation':
        if p.get('direction') not in (-1,0,1) or p.get('horizon') not in ('1-24h','1-4w'):raise ValueError('invalid_interpretation')
        if not p.get('evidence_ids') or not p.get('rationale') or not p.get('counterargument'):raise ValueError('unanchored_interpretation')

def empty_state():return {'schema_version':3,'records':{},'sources':{},'revision_log':[],'runs':[],'created_at':iso(utcnow())}

def merge_result(state,result,now):
    """Transport success != new observation != valid signal. Merge a complete source atomically."""
    sid=result['source'];old=state['sources'].get(sid,{})
    health=copy.deepcopy(old)
    health.update({'name':result.get('name',sid),'url':result.get('url',old.get('url')),
      'last_attempt_at':iso(now),'status':result['status'],'error':result.get('error'),
      'http_status':result.get('http_status'),'next_retry_at':result.get('next_retry_at'),
      'attempts':old.get('attempts',0)+1,'new_records':0,'revisions':0,'record_count':len(result.get('records',[]))})
    if result['status'] in ('failed','cooldown','format_error'):
        if result['status']!='cooldown':health['consecutive_failures']=old.get('consecutive_failures',0)+1
        state['sources'][sid]=health;return
    if result['status']=='not_modified':
        health['last_transport_success_at']=iso(now);health['consecutive_failures']=0
        health['fresh_records']=sum(eligible(r,now)[0] for r in state['records'].values() if r['source']==sid)
        state['sources'][sid]=health;return
    records=result.get('records',[])
    for r in records:validate_record(r,now)
    health['last_transport_success_at']=iso(now);health['consecutive_failures']=0
    health['sha256']=result.get('sha256');health['etag']=result.get('etag');health['last_modified']=result.get('last_modified')
    dates=[evidence_date(r) for r in records if evidence_date(r)]
    health['newest_observation']=max(dates) if dates else None
    # Snapshot source date rollback is rejected; news is an append-only set of dated events.
    if result.get('snapshot') and old.get('newest_observation') and health['newest_observation'] and health['newest_observation']<old['newest_observation']:
        health.update(status='rollback_rejected',error='取得版が既存の資料日付より古いため不採用',newest_observation=old['newest_observation'])
        state['sources'][sid]=health;return
    if records:health['last_validated_at']=iso(now)
    for r in records:
        r=copy.deepcopy(r)
        # fetched_at, provenance and transport hash are not new economic observations.
        semantic={k:v for k,v in r.items() if k not in ('fetched_at','raw_sha256','first_seen_at','last_seen_at','semantic_hash','version','provenance')}
        digest=jhash(semantic);prior=state['records'].get(r['id'])
        if prior and prior.get('semantic_hash')==digest:
            prior['last_seen_at']=iso(now)
            if r['provenance']=='https_validated':prior['provenance']=r['provenance']
            continue
        r['first_seen_at']=prior['first_seen_at'] if prior else iso(now)
        r['last_seen_at']=iso(now);r['semantic_hash']=digest;r['version']=(prior.get('version',1)+1) if prior else 1
        if prior:
            health['revisions']+=1
            state['revision_log'].append({'record_id':r['id'],'seen_at':iso(now),'prior_payload':prior['payload'],'new_payload':r['payload'],
                 'prior_observed_at':prior['observed_at'],'observed_at':r['observed_at'],'version':r['version']})
        else:health['new_records']+=1
        state['records'][r['id']]=r
    if health['new_records'] or health['revisions']:health['last_content_change_at']=iso(now)
    fresh=sum(eligible(r,now)[0] for r in records)
    health['fresh_records']=fresh
    health['status']='validated_fresh' if fresh else 'validated_but_stale' if records else 'empty_valid_response'
    state['sources'][sid]=health
    state['revision_log']=state['revision_log'][-2000:]

def attach_consensus(event,forecasts):
    """Only actually captured pre-release survey forecasts qualify. Match basis/unit/period."""
    p=event['payload'];pub=event.get('published_at');out=copy.deepcopy(event)
    out['payload'].update(consensus=None,surprise=None,consensus_status='発表前保存の予想なし')
    if not pub or event.get('date_precision')!='second':return out
    candidates=[]
    for f in forecasts:
        if not all(f.get(k)==p.get(k) for k in ('metric','period','unit','change_basis','adjustment')):continue
        try:
            if not f.get('source_url') or dt(f['captured_at'])>=dt(pub):continue
            if dt(f['captured_at'])<dt(pub)-timedelta(days=14):continue
            safe_url(f['source_url'])
            if type(f.get('value')) not in (int,float) or not math.isfinite(f['value']):continue
        except (KeyError,ValueError,TypeError):continue
        candidates.append(f)
    if candidates:
        f=max(candidates,key=lambda x:x['captured_at'])
        out['payload'].update(consensus=f['value'],surprise=round(p['actual']-f['value'],10),consensus_status='発表前に取得・保存',
          consensus_source=f['source_url'],consensus_captured_at=f['captured_at'],surprise_unit='percentage_points' if p['unit']=='percent' else p['unit'])
    return out

def unique_news(rows):
    """Exact/near-identical title duplicates share a cluster, not independent evidence votes."""
    clusters={}
    for r in sorted(rows,key=lambda x:evidence_date(x) or '',reverse=True):
        text=re.sub(r'\W+','',r['payload'].get('title','').lower())
        key=(text,(evidence_date(r) or '')[:10])
        if key not in clusters:
            x=copy.deepcopy(r);x['also_reported_by']=[];clusters[key]=x
        else:clusters[key]['also_reported_by'].append({'publisher':r['publisher'],'url':r['url']})
    return list(clusters.values())

def latest_banks(rows,pair,now):
    result={}
    for r in rows:
        if r['kind']!='bank_forecast' or r['payload']['pair']!=pair or not eligible(r,now)[0]:continue
        k=(r['publisher'],r['payload']['horizon_end'])
        if k not in result or (r.get('published_at') or '')>(result[k].get('published_at') or ''):result[k]=r
    return list(result.values())

def bank_groups(rows,pair,now):
    groups={}
    for r in latest_banks(rows,pair,now):
        p=r['payload'];end=p['horizon_end'];groups.setdefault(end,[]).append(r)
    out=[]
    for end,rs in sorted(groups.items()):
        # Same forecast end date AND exact same own reference date. No imported ING spot for MUFG.
        refs={r['payload'].get('reference_at') for r in rs}
        usable=[r for r in rs if r['payload'].get('reference') is not None]
        if len(usable)!=len(rs) or len(refs)!=1 or None in refs:
            label='基準時点が不一致/未取得：銀行の目標値は個別表示';agree=None
        else:
            signs=[(r['payload']['target']>r['payload']['reference'])-(r['payload']['target']<r['payload']['reference']) for r in rs]
            label='見方が分かれる' if len(set(signs))>1 else DIRECTION[signs[0]]+('（1行のみ）' if len(rs)==1 else 'で一致')
            agree=len(set(signs))==1
        out.append({'horizon_end':end,'publisher_count':len(rs),'label':label,'agreement':agree,
          'record_ids':[r['id'] for r in rs],'targets':[{**r['payload'],'publisher':r['publisher'],'published_at':r['published_at'],'url':r['url']} for r in rs]})
    return out

def analyst_direction(rows,pair,now,horizon):
    candidates=[];byid={r['id']:r for r in rows}
    for r in rows:
        p=r['payload']
        if r['kind']!='interpretation' or p.get('pair')!=pair or p.get('horizon')!=horizon or not eligible(r,now)[0]:continue
        refs=[byid[i] for i in p.get('evidence_ids',[]) if i in byid]
        if len(refs)!=len(p['evidence_ids']) or not all(eligible(e,now)[0] for e in refs):continue
        # Positioning, corporate plans and social word counts alone are not direct price forecasts.
        if not any(e['kind'] in ('bank_forecast','economic_event','news','option_vol') for e in refs):continue
        candidates.append(r)
    if not candidates:return {'label':'判定保留','reason':'同期間の検証済み方向解釈が不足','evidence_ids':[]}
    r=max(candidates,key=lambda x:x.get('published_at') or x['observed_at'])
    return {'label':DIRECTION[r['payload']['direction']],'reason':r['payload']['rationale'],
       'counterargument':r['payload']['counterargument'],'evidence_ids':r['payload']['evidence_ids'],'as_of':r['observed_at']}

def build_summary(state,now):
    rows=list(state['records'].values());pairs=[]
    fresh=[r for r in rows if eligible(r,now)[0]]
    for pair in PAIRS:
        poses=[r for r in fresh if r['kind']=='positioning' and r['payload'].get('pair')==pair]
        pos=max(poses,key=lambda r:r['observed_at']) if poses else None
        investor={'label':'未取得/期限経過','record_id':None}
        if pos:
            p=pos['payload'];a=p['asset_net_oi_pct'];l=p['leveraged_net_oi_pct']
            investor={'label':'区分間で逆向き' if a*l<0 else '同方向側（保有）' if a*l>0 else '片側/両側がネットゼロ',
                'record_id':pos['id'],'as_of':pos['observed_at'],**p}
        corp=[r for r in fresh if r['kind']=='corporate_plan' and r['payload'].get('pair')==pair and r['payload'].get('half')=='annual']
        plans={}
        for r in corp:
            k=(r['publisher'],r['payload'].get('fiscal_year'))
            if k not in plans or r['observed_at']>plans[k]['observed_at']:plans[k]=r
        social=[r for r in fresh if r['kind']=='social_post' and r['payload'].get('pair')==pair]
        # One author per pair; copied text ignored. Never "percent of the market bullish".
        authors={};texts=set()
        for r in sorted(social,key=lambda x:x['observed_at'],reverse=True):
            p=r['payload'];textkey=p.get('text_hash');author=p.get('author_hash')
            if not author or textkey in texts or author in authors:continue
            texts.add(textkey);authors[author]=p.get('explicit_stance','不明')
        bank=bank_groups(rows,pair,now)
        pairs.append({'pair':pair,'short':analyst_direction(rows,pair,now,'1-24h'),
         'medium':analyst_direction(rows,pair,now,'1-4w'),'banks':bank,'investors':investor,
         'corporate_plans':[r for r in plans.values()],
         'social':{'unique_authors':len(authors),'stance_counts':{x:list(authors.values()).count(x) for x in ('買いの明示','売りの明示','不明')},'ratio':None,
           'note':'抽出された自己申告・語句の数。代表性/予測力は未検証。'},
         'stability':{'opinion_agreement':next((b['label'] for b in bank),'同期間の銀行資料不足'),
           'price_stability':'未測定（オプション実測未接続）','revision_count':sum(x['record_id'] in [r['id'] for r in rows if r['payload'].get('pair')==pair] for x in state['revision_log'])}})
    n=len(unique_news([r for r in fresh if r['kind']=='news']));events=sum(r['kind']=='economic_event' for r in fresh)
    health=list(state['sources'].values());fails=sum(x.get('status') in ('failed','format_error','cooldown','rollback_rejected') for x in health)
    active_pos=[p for p in pairs if p['investors']['record_id']]
    split=sum(p['investors']['label']=='区分間で逆向き' for p in active_pos)
    bank_count=len(set(r['publisher'] for r in fresh if r['kind']=='bank_forecast'))
    lines=[f'鮮度・日付・形式を通過した資料：銀行{bank_count}行、直接の投資家保有{len(active_pos)}ペア、指標{events}件、ニュース{n}件。',
       f'投資家の直接保有を確認できた{len(active_pos)}ペア中、資産運用とレバレッジ型が逆向きなのは{split}ペア。' if active_pos else '新鮮な投資家保有がないため、参加者の一致・不一致は判定しません。',
       '企業の計画前提・投資家保有・銀行予想は合計点にせず、同じ予測終点の銀行だけを読み合わせます。',
       f'取得/形式/巻戻しの問題が{fails}系統。取得失敗は「新情報なし」としません。' if fails else '収集状態と資料の鮮度は別々に表示しています。']
    short_ok=sum(p['short']['label']!='判定保留' for p in pairs)
    lines.append(f'短期の方向判断は{short_ok}/10ペアで根拠を確認。未確認ペアは保留し、上昇確率・安全点数は作りません。')
    # Content-led factual synthesis, not a constant paragraph or majority-vote trading signal.
    for event in sorted([r for r in fresh if r['kind']=='economic_event'],key=lambda r:r['published_at'],reverse=True)[:3]:
        p=event['payload'];line=f"{p['name']}（{p['period']}・{p['change_basis']}・{p['adjustment']}）実績{p['actual']:+g}%"
        if p.get('consensus') is not None:line+=f"、発表前保存の予想{p['consensus']:+g}%との差{p['surprise']:+g}ポイント"
        else:line+='。発表前保存の市場予想がないため、サプライズは計算しません'
        lines.append(line+'。指標の強弱だけで為替の方向は断定しません。')
    top_news=unique_news([r for r in fresh if r['kind']=='news'])[:3]
    if top_news:lines.append('確認できた主な公表：'+' ／ '.join(r['publisher']+'「'+r['payload']['title']+'」' for r in top_news)+'。')
    for p in pairs:
        b=p['banks'][0] if p['banks'] else None
        if b:
            details=[]
            for t in b['targets']:
                ref=t.get('reference');bias='基準値なし' if ref is None else '基準比'+('上方向' if t['target']>ref else '下方向' if t['target']<ref else '同水準')
                details.append(f"{t['publisher']} {t['target']:g}（{bias}{'・換算' if t.get('derived') else ''}）")
            banktext='銀行 '+b['horizon_end']+'：'+'、'.join(details)
        else:banktext='銀行：同期間の新鮮な資料不足'
        investor=p['investors'];invest='投資家：'+investor['label']
        if investor['record_id']:
            invest+=f"（資産運用{investor['asset_net_oi_pct']:+.1f}%／レバレッジ型{investor['leveraged_net_oi_pct']:+.1f}%・総建玉比）"
        plans=p['corporate_plans']
        corptext=('企業：'+ '、'.join(f"{r['payload']['fiscal_year']}年度計画{r['payload']['value']:g}" for r in plans)+'。価格目標ではない') if plans else '企業：対応する新鮮な計画データなし'
        p['synthesis']=banktext+'。'+invest+'。'+corptext+'。短期は'+p['short']['label']+'。'
    return {'generated_at':iso(now),'headline':'新着・見方の対立・不足情報を自動集約','paragraphs':lines,'pairs':pairs,
     'coverage':{'fresh_records':len(fresh),'total_records':len(rows),'banks':bank_count,'direct_position_pairs':len(active_pos),'failed_sources':fails},
     'news':unique_news([r for r in fresh if r['kind']=='news'])[:80]}

def snapshot(state,now,deployment=None):
    summary=build_summary(state,now)
    rows=[]
    for r in state['records'].values():
        x=copy.deepcopy(r);ok,why=eligible(r,now);x.update(eligible=ok,exclusion_reason=None if ok else why);rows.append(x)
    rows.sort(key=lambda x:evidence_date(x) or '',reverse=True)
    obj={'schema_version':3,'generated_at':iso(now),'summary':summary,'records':rows,'sources':state['sources'],
       'revision_log':state['revision_log'][-200:],'runs':state['runs'][-72:],
       'deployment':deployment or {'hosted':False,'scheduler_active':False,'status':'ローカル検証版 / GitHub書込403'},
       'limitations':['毎時の取得は秒単位速報ではない','未契約の市場予想・オプション・X/Redditは創作しない','データ整合テストは相場予測の的中率を測らない']}
    obj['snapshot_hash']=jhash(obj);return obj
