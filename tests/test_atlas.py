"""Numerical format checks + failure injection. Not investment backtests."""
import copy, json, tempfile, unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
from fx_atlas.core import *
from fx_atlas.parsers import *
from fx_atlas.collect import *

F=Path(__file__).parent/'fixtures'
NOW=dt('2026-10-16T08:00:00Z')
HIST=dt('2026-08-06T10:00:00Z')
def fixture(name):return (F/(name+'_reconstructed.html')).read_text()
def sample(pair='USD/JPY',now=NOW):
 return record('bank_forecast','bank','Sample Bank','https://example.com/bank',iso(now-timedelta(days=1)),
   {'pair':pair,'target':155.,'reference':150.,'reference_at':now.date().isoformat(),'horizon_end':'2026-12-31','derived':False},
   published=iso(now-timedelta(days=1)),now=now)
def result(rows,source='bank',**kw):return {'source':source,'status':'ok','url':'https://example.com/','records':rows,'snapshot':True,**kw}
class FakeHTTP:
 def __init__(self,response):self.response=response;self.calls=[]
 def get(self,url,headers=None):
  self.calls.append((url,headers))
  r=self.response.get(url) if isinstance(self.response,dict) and 'body' not in self.response and 'status' not in self.response else self.response
  if isinstance(r,Exception):raise r
  return copy.deepcopy(r)
def response(body,status=200):return {'status':status,'body':body.encode(),'headers':{'ETag':'"v1"'},'charset':'utf-8','url':'https://www.bls.gov/news.release/cpi.nr0.htm'}

class ActualNumberFixtures(unittest.TestCase):
 def test_cftc_six_markets(self):
  rs=parse_cftc(fixture('cftc_20260804'),'cftc','https://www.cftc.gov/a',HIST)
  self.assertEqual(len(rs),6);self.assertEqual({r['payload']['pair'] for r in rs},{'USD/JPY','EUR/USD','GBP/USD','AUD/USD','EUR/GBP','EUR/JPY'})
 def test_cftc_usdjpy_inversion(self):
  rs=parse_cftc(fixture('cftc_20260804'),'cftc','https://www.cftc.gov/a',HIST)
  r=next(x['payload'] for x in rs if x['payload']['pair']=='USD/JPY')
  self.assertAlmostEqual(r['asset_net_oi_pct'],100*(116164-73419)/477812)
  self.assertEqual(r['asset_net_change_contracts'],-(524+36808))
 def test_direct_cross_not_synthetic(self):
  rs=parse_cftc(fixture('cftc_20260804'),'cftc','https://www.cftc.gov/a',HIST)
  r=next(x['payload'] for x in rs if x['payload']['pair']=='EUR/JPY')
  self.assertEqual(r['market_code'],'399741');self.assertAlmostEqual(r['asset_net_oi_pct'],100*(7758-11121)/20899)
 def test_oi_corruption_rejected(self):
  with self.assertRaisesRegex(ValueError,'checksum'):parse_cftc(fixture('cftc_20260804').replace('267,615','267,000'),'c','https://www.cftc.gov/a',HIST)
 def test_missing_core_currency_rejected(self):
  s=fixture('cftc_20260804').replace('#096742','#096743')
  with self.assertRaisesRegex(ValueError,'core_four'):parse_cftc(s,'c','https://www.cftc.gov/a',HIST)
 def test_missing_cross_does_not_invent(self):
  s=fixture('cftc_20260804').replace('#299741','#999999').replace('#399741','#888888')
  self.assertEqual(len(parse_cftc(s,'c','https://www.cftc.gov/a',HIST)),4)
 def test_mufg_own_reference_40_values(self):
  rs=parse_mufg(fixture('mufg_202608'),'mufg','https://www.mufgresearch.com/a',HIST)
  self.assertEqual(len(rs),40)
  r=next(r for r in rs if r['payload']['pair']=='USD/JPY' and r['payload']['horizon_end']=='2026-12-31')
  self.assertEqual(r['payload']['reference'],159.23);self.assertEqual(r['payload']['target'],156)
 def test_mufg_cross_same_period(self):
  rs=parse_mufg(fixture('mufg_202608'),'mufg','https://www.mufgresearch.com/a',HIST)
  r=next(r for r in rs if r['payload']['pair']=='EUR/JPY' and r['payload']['horizon_end']=='2026-12-31')
  self.assertAlmostEqual(r['payload']['target'],1.16*156);self.assertTrue(r['payload']['derived'])
 def test_ing_missing_year(self):
  rs=parse_ing(fixture('ing_missing_year'),'ing','https://think.ing.com/forecasts/',NOW)
  self.assertEqual(len(rs),60);self.assertTrue(all(r['published_at'] is None for r in rs));self.assertTrue(all(not eligible(r,NOW)[0] for r in rs))
 def test_ing_missing_reference(self):
  rs=parse_ing(fixture('ing_missing_year').replace('16 July','16 October 2026'),'ing','https://think.ing.com/forecasts/',NOW)
  self.assertTrue(all(r['payload']['reference'] is None for r in rs))
  self.assertIsNone(bank_groups(rs,'USD/JPY',NOW)[0]['agreement'])
 def test_tankan_latest_column(self):
  rs=parse_tankan(fixture('tankan_202606'),'t','https://www.boj.or.jp/a',NOW)
  self.assertEqual(len(rs),12)
  annual=next(r for r in rs if r['payload']['pair']=='USD/JPY' and r['payload']['fiscal_year']==2026 and r['payload']['half']=='annual')
  self.assertEqual(annual['payload']['value'],152.57);self.assertNotEqual(annual['payload']['value'],150.10)
 def test_tankan_h2_is_not_annual(self):
  rs=parse_tankan(fixture('tankan_202606'),'t','https://www.boj.or.jp/a',NOW)
  h2=next(r for r in rs if r['payload']['pair']=='EUR/JPY' and r['payload']['fiscal_year']==2026 and r['payload']['half']=='H2')
  self.assertEqual(h2['payload']['value'],175.56)
 def test_cpi_sign_and_release_time(self):
  r=parse_bls(fixture('cpi_202606'),'cpi','https://www.bls.gov/a',NOW)[0]
  self.assertEqual(r['payload']['actual'],-.4);self.assertEqual(r['published_at'],'2026-07-14T12:30:00+00:00')
 def test_ppi_not_yoy(self):
  r=parse_bls(fixture('ppi_202606')+'<p>5.5 percent year over year</p>','p','https://www.bls.gov/a',NOW)[0]
  self.assertEqual(r['payload']['actual'],-.3);self.assertEqual(r['payload']['change_basis'],'mom')
 def test_bls_unchanged_zero(self):
  r=parse_bls(fixture('ppi_202606').replace('fell 0.3 percent','was unchanged'),'p','https://www.bls.gov/a',NOW)[0]
  self.assertEqual(r['payload']['actual'],0)
 def test_bls_embargo(self):
  with self.assertRaisesRegex(ValueError,'embargo'):parse_bls(fixture('cpi_202606'),'p','https://www.bls.gov/a',dt('2026-07-14T12:29:59Z'))
 def test_source_old_cache_excluded(self):
  rs=parse_cftc(fixture('cftc_20260804'),'c','https://www.cftc.gov/a',NOW)
  self.assertFalse(any(eligible(r,NOW)[0] for r in rs))
 def test_fixture_not_current_evidence(self):
  r=sample();r['provenance']='fixture_only';self.assertFalse(eligible(r,NOW)[0])

class LedgerTests(unittest.TestCase):
 def test_same_data_not_new(self):
  st=empty_state();r=sample();merge_result(st,result([r]),NOW);t=NOW+timedelta(hours=1)
  r['fetched_at']=iso(t);merge_result(st,result([r]),t)
  self.assertEqual(st['sources']['bank']['new_records'],0);self.assertEqual(st['sources']['bank']['revisions'],0)
  self.assertEqual(st['sources']['bank']['last_content_change_at'],iso(NOW))
 def test_failure_keeps_last_good(self):
  st=empty_state();r=sample();merge_result(st,result([r]),NOW);before=copy.deepcopy(st['records'])
  merge_result(st,{'source':'bank','status':'failed','error':'DNS'},NOW+timedelta(hours=1));self.assertEqual(st['records'],before)
  self.assertEqual(st['sources']['bank']['last_transport_success_at'],iso(NOW))
 def test_same_date_correction_saved(self):
  st=empty_state();r=sample();merge_result(st,result([r]),NOW);r['payload']['target']=154
  merge_result(st,result([r]),NOW+timedelta(hours=1));self.assertEqual(len(st['revision_log']),1)
  self.assertEqual(st['revision_log'][0]['prior_payload']['target'],155)
 def test_older_document_does_not_replace(self):
  st=empty_state();r=sample();merge_result(st,result([r]),NOW)
  old=sample(now=NOW-timedelta(days=2));merge_result(st,result([old]),NOW+timedelta(hours=1))
  self.assertEqual(st['sources']['bank']['status'],'rollback_rejected');self.assertEqual(len(st['records']),1)
 def test_invalid_source_atomic(self):
  st=empty_state();r=sample();bad=sample('EUR/USD');bad['payload']['target']=-1
  with self.assertRaises(ValueError):merge_result(st,result([r,bad]),NOW)
  self.assertFalse(st['records'])
 def test_http200_not_fresh(self):
  st=empty_state();r=sample(now=HIST);merge_result(st,result([r]),NOW)
  self.assertEqual(st['sources']['bank']['status'],'validated_but_stale');self.assertEqual(build_summary(st,NOW)['coverage']['fresh_records'],0)
 def test_304_does_not_refresh_observation(self):
  st=empty_state();r=sample();merge_result(st,result([r]),NOW);observed=r['observed_at']
  merge_result(st,{'source':'bank','status':'not_modified'},NOW+timedelta(hours=1))
  self.assertEqual(st['records'][r['id']]['observed_at'],observed)
 def test_dates_no_timezone_rejected(self):
  with self.assertRaises(ValueError):dt('2026-10-16T08:00:00')
 def test_unknown_observation(self):
  r=sample();r['observed_at']=r['published_at']=None;self.assertFalse(eligible(r,NOW)[0])
 def test_future_rejected(self):
  with self.assertRaisesRegex(ValueError,'future'):validate_record(sample(now=NOW+timedelta(days=3)),NOW)
 def test_nan_rejected(self):
  r=sample();r['payload']['target']=float('nan')
  with self.assertRaises(ValueError):validate_record(r,NOW)
 def test_url_safety(self):
  for s in ['http://example.com','javascript:alert(1)','https://user:pass@example.com','https://127.0.0.1','https://169.254.169.254']:
   with self.subTest(url=s):
    with self.assertRaises(ValueError):safe_url(s)
 def test_keep_non_tracking_query(self):self.assertEqual(canonical_url('https://example.com/a?id=1&utm_source=x'),'https://example.com/a?id=1')
 def test_expired_target(self):
  r=sample();r['payload']['horizon_end']='2026-09-30';self.assertFalse(eligible(r,NOW)[0])

class SummaryTests(unittest.TestCase):
 def test_same_horizon_different_reference_no_consensus(self):
  a=sample();b=sample();b['id']='two';b['publisher']='Bank B';b['payload']['reference_at']='2026-10-15'
  g=bank_groups([a,b],'USD/JPY',NOW)[0];self.assertIsNone(g['agreement']);self.assertEqual(g['publisher_count'],2)
 def test_same_source_not_two_votes(self):
  a=sample();b=sample();b['id']='copy';self.assertEqual(bank_groups([a,b],'USD/JPY',NOW)[0]['publisher_count'],1)
 def test_different_forecast_horizons(self):
  a=sample();b=sample();b['payload']['horizon_end']='2027-03-31'
  self.assertEqual(len(bank_groups([a,b],'USD/JPY',NOW)),2)
 def test_forecast_missing_reference_not_neutral(self):
  r=sample();r['payload']['reference']=None;self.assertIsNone(bank_groups([r],'USD/JPY',NOW)[0]['agreement'])
 def test_corporate_not_price_vote(self):
  rs=parse_tankan(fixture('tankan_202606'),'t','https://www.boj.or.jp/a',dt('2026-07-02'))
  st=empty_state();merge_result(st,result(rs,'t'),dt('2026-07-02'));p=build_summary(st,dt('2026-07-02'))['pairs'][0]
  self.assertEqual(p['short']['label'],'判定保留');self.assertTrue(all(r['payload']['half']=='annual' for r in p['corporate_plans']))
 def test_news_dedup(self):
  a=record('news','a','A','https://example.com/a',iso(NOW),{'title':'Rates on hold'},published=iso(NOW),now=NOW)
  b=copy.deepcopy(a);b['id']='b';b['publisher']='B';b['url']='https://example.org/a'
  self.assertEqual(len(unique_news([a,b])),1);self.assertEqual(len(unique_news([a,b])[0]['also_reported_by']),1)
 def test_news_unrelated_same_day_not_dedup(self):
  a=record('news','a','A','https://example.com/a',iso(NOW),{'title':'Rates on hold'},published=iso(NOW),now=NOW)
  b=copy.deepcopy(a);b['payload']['title']='GDP release';self.assertEqual(len(unique_news([a,b])),2)
 def test_interpretation_missing_evidence_gated(self):
  r=record('interpretation','a','Analyst','https://example.com/a',iso(NOW),{'pair':'USD/JPY','direction':1,'horizon':'1-24h','rationale':'test','counterargument':'test','evidence_ids':['missing']},published=iso(NOW),now=NOW)
  self.assertEqual(analyst_direction([r],'USD/JPY',NOW,'1-24h')['label'],'判定保留')
 def test_reviewed_interpretation_not_hardcoded_hold(self):
  a=record('news','a','A','https://example.com/a',iso(NOW),{'title':'Test event'},published=iso(NOW),now=NOW)
  r=record('interpretation','i','Reviewer','https://example.com/i',iso(NOW),{'pair':'USD/JPY','direction':-1,'horizon':'1-24h','rationale':'fixture reasoning','counterargument':'fixture counter','evidence_ids':[a['id']]},published=iso(NOW),now=NOW)
  self.assertEqual(analyst_direction([a,r],'USD/JPY',NOW,'1-24h')['label'],'下方向')
 def test_past_interpretation_expires(self):
  a=sample();a['kind']='interpretation';a['payload']={'pair':'USD/JPY','direction':1,'horizon':'1-24h','rationale':'r','counterargument':'c','evidence_ids':['none']}
  a['published_at']=a['observed_at']=iso(NOW-timedelta(hours=25));self.assertFalse(eligible(a,NOW)[0])

class ConsensusTests(unittest.TestCase):
 def event(self):return parse_bls(fixture('cpi_202606'),'cpi','https://www.bls.gov/a',NOW)[0]
 def forecast(self):
  p=self.event()['payload'];return {k:p[k] for k in ('metric','period','unit','change_basis','adjustment')}|{'value':.1,'source_url':'https://example.com/survey','captured_at':'2026-07-14T12:00:00Z'}
 def test_pre_release_forecast(self):
  p=attach_consensus(self.event(),[self.forecast()])['payload'];self.assertEqual(p['surprise'],-.5)
 def test_after_release_forecast_rejected(self):
  f=self.forecast();f['captured_at']='2026-07-14T12:31:00Z';self.assertIsNone(attach_consensus(self.event(),[f])['payload']['surprise'])
 def test_same_instant_not_before(self):
  f=self.forecast();f['captured_at']='2026-07-14T12:30:00Z';self.assertIsNone(attach_consensus(self.event(),[f])['payload']['surprise'])
 def test_yoy_not_mom(self):
  f=self.forecast();f['change_basis']='yoy';self.assertIsNone(attach_consensus(self.event(),[f])['payload']['surprise'])
 def test_prior_actual_not_forecast(self):self.assertIsNone(attach_consensus(self.event(),[])['payload']['surprise'])
 def test_zero_forecast_is_valid(self):
  f=self.forecast();f['value']=0;self.assertEqual(attach_consensus(self.event(),[f])['payload']['surprise'],-.4)

class FeedSocialTests(unittest.TestCase):
 def test_atom_updated_not_publication(self):
  xml=b'<feed><entry><title>Test</title><link href="https://www.ecb.europa.eu/a"/><updated>2026-10-16T07:00:00Z</updated></entry></feed>'
  self.assertEqual(parse_feed(xml,'e','https://www.ecb.europa.eu/rss/press.html','ECB','EUR',NOW),[])
 def test_feed_no_entities(self):
  with self.assertRaises(ValueError):parse_feed(b'<!DOCTYPE a><rss/>','e','https://example.com','P','USD',NOW)
 def test_html_is_not_feed(self):
  with self.assertRaises(ValueError):parse_feed(b'<html/>','e','https://example.com','P','USD',NOW)
 def test_rss_valid(self):
  x=b'<rss><channel><item><title>Policy decision</title><link>https://www.ecb.europa.eu/a</link><pubDate>Fri, 16 Oct 2026 07:00:00 GMT</pubDate></item></channel></rss>'
  self.assertEqual(len(parse_feed(x,'e','https://www.ecb.europa.eu/a','ECB','EUR',NOW)),1)
 def social(self,text,author='x',uri='one',when='2026-10-16T07:00:00Z'):
  return {'uri':'at://did:plc:'+author+'/post/'+uri,'author':{'did':'did:plc:'+author},'record':{'text':text,'createdAt':when}}
 def test_social_explicit_not_probability(self):
  rs=parse_social(json.dumps({'posts':[self.social('Long USDJPY')]}),'s','https://public.api.bsky.app/a','USD/JPY',NOW)
  self.assertEqual(rs[0]['payload']['explicit_stance'],'買いの明示')
 def test_social_negation(self):
  rs=parse_social(json.dumps({'posts':[self.social('Not long USDJPY')]}),'s','https://public.api.bsky.app/a','USD/JPY',NOW)
  self.assertEqual(rs[0]['payload']['explicit_stance'],'不明')
 def test_social_duplicates(self):
  rs=parse_social(json.dumps({'posts':[self.social('Long USDJPY'),self.social('Long USDJPY','y','two')]}),'s','https://public.api.bsky.app/a','USD/JPY',NOW)
  self.assertEqual(len(rs),1)
 def test_social_old_or_future(self):
  rs=parse_social(json.dumps({'posts':[self.social('Long USDJPY',when='2026-10-14T07:00:00Z'),self.social('Long USDJPY',when='2026-10-17T07:00:00Z')]}),'s','https://public.api.bsky.app/a','USD/JPY',NOW)
  self.assertFalse(rs)
 def test_social_one_author(self):
  rs=parse_social(json.dumps({'posts':[self.social('Long USDJPY today'),self.social('Sell USDJPY today',uri='two')]}),'s','https://public.api.bsky.app/a','USD/JPY',NOW)
  st=empty_state();merge_result(st,result(rs,'s'),NOW);self.assertEqual(build_summary(st,NOW)['pairs'][0]['social']['unique_authors'],1)

class AcquisitionTests(unittest.TestCase):
 def test_unauthorized_stops(self):
  f=FakeHTTP(TransportError('forbidden',403));r=collect_one(SOURCES[0],{},NOW,f)
  self.assertEqual(r['status'],'failed');self.assertEqual(dt(r['next_retry_at']),NOW+timedelta(days=1));self.assertEqual(len(f.calls),1)
 def test_rate_limit_respected(self):
  f=FakeHTTP(TransportError('limited',429,'7200'));r=collect_one(SOURCES[0],{},NOW,f)
  self.assertGreaterEqual(dt(r['next_retry_at']),NOW+timedelta(hours=2))
 def test_cooldown_no_network(self):
  f=FakeHTTP(TransportError('bad'));r=collect_one(SOURCES[0],{'next_retry_at':iso(NOW+timedelta(hours=2))},NOW,f)
  self.assertEqual(r['status'],'cooldown');self.assertFalse(f.calls)
 def test_304_without_cache_rejected(self):
  f=FakeHTTP(response('',304));r=collect_one(SOURCES[4],{},NOW,f);self.assertEqual(r['status'],'format_error')
 def test_discovery_mufg_no_future(self):
  body='<a href="/fx/monthly-foreign-exchange-outlook-august-2026/">A</a><a href="/fx/monthly-foreign-exchange-outlook-november-2026/">B</a>'
  self.assertIn('august',discover_url('mufg',body,'https://www.mufgresearch.com',NOW))
 def test_discovery_tankan_latest(self):
  body='<a href="tk2603.htm">A</a><a href="tk2606.htm">B</a>'
  self.assertIn('tk2606',discover_url('tankan',body,'https://www.boj.or.jp/statistics/tk/yoshi/index.htm',NOW))
 def test_source_redirect_allowlist(self):
  with self.assertRaises(ValueError):allowed('https://attacker.example/a')
 def test_full_replay_success_then_failure(self):
  spec=SOURCES[4]
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);one=run(root,client=FakeHTTP(response(fixture('cpi_202606'))),now=dt('2026-07-15T08:00:00Z'),sources=[spec])
   state=json.loads((root/'fx-sentiment-data/state.json').read_text());before=copy.deepcopy(state['records'])
   self.assertEqual(one['transport_ok'],1)
   two=run(root,client=FakeHTTP(TransportError('simulated outage')),now=dt('2026-07-15T09:00:00Z'),sources=[spec])
   state=json.loads((root/'fx-sentiment-data/state.json').read_text());self.assertEqual(before,state['records']);self.assertEqual(two['status'],'all_failed')
 def test_duplicate_replay_no_new(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);s=[SOURCES[4]]
   run(root,client=FakeHTTP(response(fixture('cpi_202606'))),now=dt('2026-07-15T08:00:00Z'),sources=s)
   r=run(root,client=FakeHTTP(response(fixture('cpi_202606'))),now=dt('2026-07-15T09:00:00Z'),sources=s)
   self.assertEqual(r['new_records'],0);self.assertEqual(r['revisions'],0)
if __name__=='__main__':unittest.main(verbosity=2)
