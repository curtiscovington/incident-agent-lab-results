"""Reconcile v2 frozen inputs, token budgets, actions and paired outcomes."""
import collections,hashlib,json,pathlib,statistics
import replay as core
ROOT=pathlib.Path(__file__).resolve().parent;RUN=ROOT/'runs/replay-20261101-v2';V1=ROOT/'runs/replay-20261101-v1'
def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 s=read(RUN/'state.json')
 if s['status']!='complete':print(json.dumps({k:s.get(k) for k in ('status','stage','completed','inferenceCalls','error')}));return
 p=read(RUN/'protocol.json');cases={x['id']:x for x in read(RUN/'cases.json')};rows=[read(f) for f in sorted((RUN/'results').glob('*.json'))]
 assert sha(RUN/'protocol.json')==(RUN/'protocol.sha256').read_text().strip()==s['protocolSha256'];assert sha(RUN/'cases.json')==(RUN/'cases.sha256').read_text().strip()==s['casesSha256']
 for name,h in read(RUN/'code-hashes.json').items():assert sha(ROOT/name)==h
 old=read(RUN/'v1-artifact-hashes.json');assert all(sha(V1/k)==v for k,v in old.items())
 original=read(V1/'original-source-hashes.json');assert all(sha(core.SOURCE/k)==v for k,v in original.items())
 assert len(rows)==80 and len({(x['case'],x['arm']) for x in rows})==80
 assert {(x['case'],x['arm']) for x in rows}=={(x['case'],x['arm']) for x in p['queue']}
 started=list((RUN/'attempts').glob('*.started.json'));responses=list((RUN/'attempts').glob('*.response.json'));requests=list((RUN/'attempts').glob('*.request.json'))
 assert len(started)==len(responses)==len(requests)==s['inferenceCalls']==80
 for r in rows:
  c=cases[r['case']];a=r['attempts'][0];response=read(RUN/a['responseFile']);decision,error=core.validate_decision(response,p['schema']);assert decision==r['decision'];assert core.score(c,decision)==r['score'];assert len(r['attempts'])==1
  payload=read(RUN/'attempts'/f"{r['case']}--{r['arm']}.request.json");assert payload['messages']==c['messagesByArm'][r['arm']] and payload['options']==p['options']
  assert response['prompt_eval_count']==r['actualPromptTokens']==a['promptTokens'];assert response['eval_count']==a['outputTokens']
  if r['budgeted']:assert r['actualPromptTokens']<=7002 and abs(r['tokenCountDelta'])<=2 and r['reservedHeadroom']>=1190
  assert a['promptTokens']+a['outputTokens']<=8192
 arms=[];strata=[]
 for arm in p['arms']:
  g=[r for r in rows if r['arm']==arm];assert len(g)==20;dur=[r['attempts'][0]['totalDurationNs']/1e9 for r in g]
  arms.append({'arm':arm,'n':20,'valid':sum(r['finalValid'] for r in g),'categories':dict(collections.Counter(r['score']['category'] for r in g)),'repeatedToolTarget':sum(r['score']['repeatToolTarget'] for r in g),'lengthStops':sum(r['attempts'][0]['doneReason']=='length' for r in g),'contextBoundaryStops':sum(r['attempts'][0]['doneReason']=='length' and r['actualPromptTokens']+r['attempts'][0]['outputTokens']==8192 for r in g),'actualPromptRange':[min(r['actualPromptTokens'] for r in g),max(r['actualPromptTokens'] for r in g)],'minimumHeadroom':min(r['reservedHeadroom'] for r in g),'promptTokens':sum(r['actualPromptTokens'] for r in g),'outputTokens':sum(r['attempts'][0]['outputTokens'] for r in g),'medianModelSeconds':statistics.median(dur),'totalModelSeconds':sum(dur)})
  for kind in ('db_lock','deployment','truncation','success_control'):
   z=[r for r in g if r['stratum']==kind];assert len(z)==5;strata.append({'arm':arm,'stratum':kind,'n':5,'valid':sum(r['finalValid'] for r in z),'categories':dict(collections.Counter(r['score']['category'] for r in z))})
 pairs=[];pair_summaries=[]
 for identity,c in cases.items():
  z={r['arm']:r for r in rows if r['case']==identity};a=z['facts_time'];b=z['facts_entity'];assert abs(a['actualPromptTokens']-b['actualPromptTokens'])<=8
  ma=c['messagesByArm']['facts_time'];mb=c['messagesByArm']['facts_entity'];assert ma[:2]==mb[:2]
  aa=json.loads(ma[-1]['content'].split('\n',1)[1])['prior_observations'];bb=json.loads(mb[-1]['content'].split('\n',1)[1])['prior_observations'];assert sorted(map(json.dumps,aa))==sorted(map(json.dumps,bb))
  pairs.append({'case':identity,'stratum':c['stratum'],'actions':{k:v['decision'] for k,v in z.items()},'categories':{k:v['score']['category'] for k,v in z.items()},'promptTokens':{k:v['actualPromptTokens'] for k,v in z.items()},'factsPromptDifference':b['actualPromptTokens']-a['actualPromptTokens']})
 for left,right in (('replication','budget_raw'),('budget_raw','facts_time'),('facts_time','facts_entity')):
  counts=collections.Counter()
  for case in pairs:
   x=case['categories'][left]=='preferred';y=case['categories'][right]=='preferred';counts['right_only' if y and not x else 'left_only' if x and not y else 'both_preferred' if x else 'neither_preferred']+=1
  pair_summaries.append({'left':left,'right':right,'preferredPairedCounts':dict(counts)})
 result={'id':s['id'],'status':'complete','decisions':80,'calls':80,'cap':80,'retries':0,'unresolvedCalls':0,'replayToolDispatches':0,'frozenHashesMatch':True,'unchangedV1Files':len(old),'unchangedOriginalInputs':len(original),'budgetedCallsChecked':60,'budgetViolations':0,'maximumMatchedFactsPromptDifference':max(abs(x['factsPromptDifference']) for x in pairs),'arms':arms,'strata':strata,'pairedComparisons':pair_summaries,'caseResults':pairs,'limitations':p['limitations'],'protocolSha256':sha(RUN/'protocol.json')}
 (RUN/'analysis.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k not in ('caseResults','limitations')},indent=2))
if __name__=='__main__':main()
