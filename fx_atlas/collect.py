"""Public, allow-listed acquisition. No tokens, scraping bypasses, or paid APIs.
All run times are recorded. A 200 response is not a fresh financial observation.
"""
from __future__ import annotations
import argparse, copy, hashlib, json, os, re, sys
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler
from urllib.parse import urljoin, urlsplit, urlencode
from datetime import timedelta
from email.utils import parsedate_to_datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from .core import *
from .parsers import *

@dataclass(frozen=True)
class Source:
    id: str
    name: str
    url: str
    parser: str
    publisher: str = ''
    currency: str = ''
    pair: str = ''
    discover: str = ''
    snapshot: bool = True

SOURCES=[
 Source('cftc','CFTC・6市場の区分別保有','https://www.cftc.gov/dea/options/financial_lof.htm','cftc'),
 Source('mufg','MUFG・月次予想表','https://www.mufgresearch.com/fx/','mufg',discover='mufg'),
 Source('ing','ING・予想表','https://think.ing.com/forecasts/','ing'),
 Source('tankan','日銀短観・企業の想定為替','https://www.boj.or.jp/statistics/tk/yoshi/index.htm','tankan',discover='tankan'),
 Source('cpi','米CPI・公表値','https://www.bls.gov/news.release/cpi.nr0.htm','bls'),
 Source('ppi','米PPI・公表値','https://www.bls.gov/news.release/ppi.nr0.htm','bls'),
 Source('fed','FRB・公表資料','https://www.federalreserve.gov/feeds/press_all.xml','feed','FRB','USD',snapshot=False),
 Source('ecb','ECB・公表資料','https://www.ecb.europa.eu/rss/press.html','feed','ECB','EUR',snapshot=False),
 Source('boj','日銀・公表資料','https://www.boj.or.jp/en/rss/whatsnew.xml','feed','日本銀行','JPY',snapshot=False),
 Source('boe','BoE・公表資料','https://www.bankofengland.co.uk/rss/news','feed','BoE','GBP',snapshot=False),
 Source('rba','RBA・公表資料','https://www.rba.gov.au/rss/rss-cb-media-releases.xml','feed','RBA','AUD',snapshot=False),
 Source('bls','BLS・公表資料','https://www.bls.gov/feed/bls_latest.rss','feed','BLS','USD',snapshot=False),
]
# Optional public social search. No auth workaround when permission is refused.
SOCIAL=[Source('social_'+p.replace('/',''),'Bluesky・'+p,
 'https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts?'+urlencode({'q':p.replace('/',''),'sort':'latest','limit':'25'}),
 'social',pair=p,snapshot=False) for p in PAIRS]
ALLOWED={'www.cftc.gov','cftc.gov','www.mufgresearch.com','mufgresearch.com','think.ing.com','www.boj.or.jp',
 'www.bls.gov','www.federalreserve.gov','www.ecb.europa.eu','www.bankofengland.co.uk','www.rba.gov.au','public.api.bsky.app'}

class TransportError(Exception):
    def __init__(self,message,status=None,retry_after=None):
        super().__init__(message);self.status=status;self.retry_after=retry_after

def allowed(url):
    safe_url(url)
    if urlsplit(url).hostname not in ALLOWED:raise ValueError('source_host_not_allowlisted')
    return url

class RedirectGuard(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        allowed(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)

class HTTP:
    def __init__(self,timeout=12,max_bytes=2_000_000):self.timeout=timeout;self.max_bytes=max_bytes
    def get(self,url,headers=None):
        allowed(url)
        hs={'User-Agent':'FXSentimentAtlas/3.0 (public-source monitoring; conditional requests)',
            'Accept':'application/json,application/xml,text/html;q=0.9,*/*;q=0.5','Accept-Encoding':'identity'}
        hs.update(headers or {})
        opener=build_opener(HTTPSHandler(),RedirectGuard())
        try:
            with opener.open(Request(url,headers=hs),timeout=self.timeout) as r:
                allowed(r.geturl());body=r.read(self.max_bytes+1)
                if len(body)>self.max_bytes:raise TransportError('response_too_large',r.status)
                charset=r.headers.get_content_charset() or 'utf-8'
                return {'status':r.status,'body':body,'url':r.geturl(),'headers':dict(r.headers),'charset':charset}
        except HTTPError as e:
            if e.code==304:return {'status':304,'body':b'','url':url,'headers':dict(e.headers),'charset':'utf-8'}
            raise TransportError('HTTP '+str(e.code),e.code,e.headers.get('Retry-After')) from e
        except (URLError,TimeoutError,OSError) as e:raise TransportError(str(e.reason) if isinstance(e,URLError) else str(e)) from e

def discover_url(kind,body,base,now):
    doc=Document(body);urls=[]
    for a in doc.links:
        u=urljoin(base,a['href'])
        if urlsplit(u).hostname not in ALLOWED:continue
        if kind=='mufg':
            m=re.search(r'/monthly-foreign-exchange-outlook-([a-z]+)-(20\d\d)/?$',urlsplit(u).path)
            if m and m[1] in MONTHS:
                key=(int(m[2]),MONTHS[m[1]])
                if key<=(now.year,now.month):urls.append((key,u))
        else:
            m=re.search(r'/tk(\d\d)(\d\d)\.htm$',urlsplit(u).path)
            if m and int(m[2]) in (3,6,9,12):
                key=(2000+int(m[1]),int(m[2]))
                if key<=(now.year,now.month):urls.append((key,u))
    if not urls:raise ValueError('latest_document_link_not_found')
    return max(urls)[1]

def retry_time(e,now,failures):
    # Access denials wait a day; 429 honors provider Retry-After. Never circumvent.
    seconds=86400 if e.status in (401,403) else min(21600,3600*2**min(failures,3))
    if e.status==429:
        seconds=max(3600,seconds)
        if e.retry_after:
            try:seconds=max(seconds,int(e.retry_after))
            except ValueError:
                try:seconds=max(seconds,int((parsedate_to_datetime(e.retry_after)-now).total_seconds()))
                except (ValueError,TypeError):pass
    return iso(now+timedelta(seconds=seconds))

def collect_one(spec,previous,now,client=None,raw_dir=None):
    client=client or HTTP();previous=previous or {}
    base={'source':spec.id,'name':spec.name,'url':spec.url,'snapshot':spec.snapshot}
    if previous.get('next_retry_at') and dt(previous['next_retry_at'])>now:
        return {**base,'status':'cooldown','error':previous.get('error','再試行待機'),
                'next_retry_at':previous['next_retry_at'],'http_status':previous.get('http_status')}
    try:
        url=spec.url
        if spec.discover:
            landing=client.get(url)
            if landing['status']!=200:raise TransportError('discovery_not_200',landing['status'])
            url=discover_url(spec.discover,landing['body'].decode(landing.get('charset','utf-8')),url,now)
        headers={}
        if previous.get('document_url',spec.url)==url:
            if previous.get('etag'):headers['If-None-Match']=previous['etag']
            if previous.get('last_modified'):headers['If-Modified-Since']=previous['last_modified']
        response=client.get(url,headers);base['document_url']=url;base['http_status']=response['status']
        if response['status']==304:
            if not previous.get('last_validated_at'):raise ValueError('304_without_prior_validated_body')
            return {**base,'status':'not_modified'}
        raw=response['body'];text=raw.decode(response.get('charset','utf-8'),errors='strict');digest=hashlib.sha256(raw).hexdigest()
        if raw_dir:
            raw_dir.mkdir(parents=True,exist_ok=True)
            (raw_dir/(spec.id+'_'+digest+'.raw')).write_bytes(raw)
        # Response headers are case insensitive.
        rh={k.lower():v for k,v in response['headers'].items()}
        kwargs=(text,spec.id,url,now)
        if spec.parser=='feed':rs=parse_feed(raw,spec.id,url,spec.publisher,spec.currency,now)
        elif spec.parser=='social':rs=parse_social(text,spec.id,url,spec.pair,now)
        else:rs={'cftc':parse_cftc,'mufg':parse_mufg,'ing':parse_ing,'tankan':parse_tankan,'bls':parse_bls}[spec.parser](*kwargs)
        for r in rs:
            r['raw_sha256']=digest;validate_record(r,now)
        return {**base,'status':'ok','sha256':digest,'etag':rh.get('etag'),'last_modified':rh.get('last-modified'),'records':rs}
    except TransportError as e:
        return {**base,'status':'failed','error':str(e)[:250],'http_status':e.status,
          'next_retry_at':retry_time(e,now,previous.get('consecutive_failures',0))}
    except (ValueError,KeyError,IndexError,TypeError,AttributeError,OverflowError,LookupError,UnicodeError,ET.ParseError) as e:
        return {**base,'status':'format_error','error':type(e).__name__+': '+str(e)[:200],
          'next_retry_at':iso(now+timedelta(hours=4))}

def run(root:Path,*,client=None,now=None,social=False,sources=None,save_raw=False):
    now=now or utcnow();data=root/'fx-sentiment-data';statepath=data/'state.json'
    state=json.loads(statepath.read_text()) if statepath.exists() else empty_state()
    if state.get('schema_version')!=3:raise ValueError('state_schema_mismatch')
    specs=sources if sources is not None else SOURCES+(SOCIAL if social else [])
    raw_dir=root/'private-raw'/iso(now).replace(':','-') if save_raw else None
    results=[]
    # Sequential mode with a test transport keeps deterministic tests; bounded public concurrency otherwise.
    if client:
        results=[collect_one(s,state['sources'].get(s.id),now,client,raw_dir) for s in specs]
    else:
        normal=[s for s in specs if s.parser!='social'];social_specs=[s for s in specs if s.parser=='social']
        with ThreadPoolExecutor(max_workers=3) as pool:
            fs={pool.submit(collect_one,s,state['sources'].get(s.id),now,None,raw_dir):s for s in normal}
            for f in as_completed(fs):results.append(f.result())
        stop=None
        for s in social_specs:
            if stop:
                results.append({'source':s.id,'name':s.name,'url':s.url,'status':'cooldown',
                  'error':'同じ公開APIの認証/利用制限により停止','http_status':stop.get('http_status'),
                  'next_retry_at':stop['next_retry_at']})
                continue
            r=collect_one(s,state['sources'].get(s.id),now,None,raw_dir);results.append(r)
            if r.get('http_status') in (401,403,429) and r.get('next_retry_at'):stop=r
    for result in sorted(results,key=lambda x:x['source']):
        merge_result(state,result,now)
        if result.get('document_url'):state['sources'][result['source']]['document_url']=result['document_url']
    # A consensus capture file is optional. The program never turns a previous actual into a forecast.
    cp=data/'consensus-captures.json';forecasts=json.loads(cp.read_text()) if cp.exists() else []
    for rid,r in list(state['records'].items()):
        if r['kind']=='economic_event':state['records'][rid]=attach_consensus(r,forecasts)
    # Direction interpretations must be dated, evidence-anchored, and explicitly reviewed.
    ip=data/'reviewed-interpretations.json'
    if ip.exists():
        for item in json.loads(ip.read_text()):
            if item.get('review_status')!='approved':continue
            validate_record(item,now);state['records'][item['id']]=item
    success=sum(x['status'] in ('ok','not_modified') for x in results)
    result={'at':iso(now),'attempted':len(results),'transport_ok':success,
      'fresh_sources':sum(state['sources'][x['source']]['status']=='validated_fresh' for x in results),
      'new_records':sum(state['sources'][x['source']].get('new_records',0) for x in results),
      'revisions':sum(state['sources'][x['source']].get('revisions',0) for x in results),
      'status':'all_failed' if success==0 else 'partial' if success<len(results) else 'collected',
      'test_transport':client is not None}
    state['runs']=(state['runs']+[result])[-240:]
    # Rolling bounded ledger; long-lived history is retained in Git commit versions.
    for rid,r in list(state['records'].items()):
        try:old=age_hours(evidence_date(r),now)>365*24
        except (ValueError,TypeError):old=False
        if old:del state['records'][rid]
    atomic_json(statepath,state)
    deploy={'hosted':False,'scheduler_active':False,'status':'コード実装・ローカル実行のみ / GitHubへの配置は未完了'}
    if os.environ.get('GITHUB_ACTIONS')=='true':
        deploy={'hosted':None,'scheduler_active':os.environ.get('GITHUB_EVENT_NAME')=='schedule',
          'status':'GitHub Actionsで収集。現在の公開URLからこの実行版を確認できれば配備済み。',
          'run_url':'https://github.com/'+os.environ.get('GITHUB_REPOSITORY','')+'/actions/runs/'+os.environ.get('GITHUB_RUN_ID',''),
          'event':os.environ.get('GITHUB_EVENT_NAME'),'cadence':'毎時17分 UTC / 遅延あり'}
    snap=snapshot(state,now,deploy);atomic_json(data/'snapshot.json',snap)
    text='通貨心理レーダー｜'+iso(now)+'\n\n'+'\n\n'.join(snap['summary']['paragraphs'])
    for p in snap['summary']['pairs']:
        text+='\n\n'+p['pair']+'｜短期：'+p['short']['label']+'｜'+p['investors']['label']
        for bank in p['banks']:
            text+='\n銀行 '+bank['horizon_end']+'：'+bank['label']
    (data/'summary.txt').write_text(text,encoding='utf-8')
    atomic_json(root/'audit'/'last_run.json',result)
    return result

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,default=Path('.'));ap.add_argument('--social',action='store_true');ap.add_argument('--save-raw',action='store_true')
    args=ap.parse_args();r=run(args.root,social=args.social,save_raw=args.save_raw);print(json.dumps(r,ensure_ascii=False))
    if r['status']=='all_failed':sys.exit(2)
if __name__=='__main__':main()
