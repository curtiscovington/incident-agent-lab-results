"""Final read-only reconciliation; writes only separate derived validation.json."""
import collections,hashlib,json,pathlib,statistics
import replay
R=replay.RUN
def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def validate():
 s=read(R/'state.json')
 if s['status']!='complete':print(json.dumps({k:s.get(k) for k in ('status','stage','completed','inferenceCalls','error')}));return
 p=read(R/'protocol.json');cases={c['id']:c for c in read(R/'cases.json')}
 assert sha(R/'protocol.json')==(R/'protocol.sha256').read_text().strip()==s['protocolSha256']
 assert sha(R/'cases.json')==(R/'cases.sha256').read_text().strip()==s['casesSha256']
 assert sha(replay.ROOT/'replay.py')==read(R/'code-hashes.json')['replay.py']
 originals=read(R/'original-source-hashes.json');changed=[n for n,h in originals.items() if sha(replay.SOURCE/n)!=h];assert not changed,changed
 rows=[read(f) for f in sorted((R/'results').glob('*.json'))]
 queue={(x['case'],x['arm']) for x in p['queue']};assert len(rows)==len(queue)==80
 assert {(r['case'],r['arm']) for r in rows}==queue
 assert len({(r['case'],r['arm']) for r in rows})==80
 markers=list((R/'attempts').glob('*.started.json'));responses=list((R/'attempts').glob('*.response.json'));requests=list((R/'attempts').glob('*.request.json'))
 assert len(markers)==len(responses)==len(requests)==s['inferenceCalls']<=120
 per_arm=[]
 for arm in replay.ARMS:
  group=[r for r in rows if r['arm']==arm];assert len(group)==20
  for row in group:
   attempts=row['attempts'];assert 1<=len(attempts)<=(2 if arm in ('format_retry','both') else 1)
   if len(attempts)==2:assert not attempts[0]['schemaValid']
   for i,a in enumerate(attempts):
    response=read(R/a['responseFile']);decision,error=replay.validate_decision(response,p['schema']);assert (decision is not None)==a['schemaValid'];assert a['attempt']==i
    assert response.get('message',{}).get('content','')==a['responseText']
    assert response.get('total_duration',0)==a['totalDurationNs']
   final,error=replay.validate_decision(read(R/attempts[-1]['responseFile']),p['schema'])
   assert row['decision']==final and row['finalValid']==(final is not None)
   assert row['score']==replay.score(cases[row['case']],final)
  attempts=[a for r in group for a in r['attempts']];durations=[a['totalDurationNs']/1e9 for a in attempts]
  per_arm.append({'arm':arm,'decisions':20,'initialValid':sum(r['initialValid'] for r in group),'finalValid':sum(r['finalValid'] for r in group),'categories':dict(collections.Counter(r['score']['category'] for r in group)),'repeatToolTarget':sum(r['score']['repeatToolTarget'] for r in group),'calls':len(attempts),'retries':len(attempts)-20,'successfulRetries':sum(len(r['attempts'])==2 and r['finalValid'] for r in group),'failedRetries':sum(len(r['attempts'])==2 and not r['finalValid'] for r in group),'lengthStops':sum(a['doneReason']=='length' for a in attempts),'contextBoundaryStops':sum(a['doneReason']=='length' and a['promptTokens']+a['outputTokens']==8192 for a in attempts),'medianCallSeconds':statistics.median(durations),'totalModelSeconds':sum(durations),'promptTokens':sum(a['promptTokens'] for a in attempts),'outputTokens':sum(a['outputTokens'] for a in attempts),'initialPromptTokenRange':[min(r['attempts'][0]['promptTokens'] for r in group),max(r['attempts'][0]['promptTokens'] for r in group)]})
 identical={};responses_same={}
 for left,right in (('baseline','format_retry'),('evidence_replan','both')):
  identical[left+'/'+right]=sum(read(R/'attempts'/f'{case}--{left}-a0.request.json')==read(R/'attempts'/f'{case}--{right}-a0.request.json') for case in cases)
  responses_same[left+'/'+right]=sum(read(R/'attempts'/f'{case}--{left}-a0.response.json')['message']['content']==read(R/'attempts'/f'{case}--{right}-a0.response.json')['message']['content'] for case in cases)
 assert all(n==20 for n in identical.values())
 strata=[]
 for arm in replay.ARMS:
  for stratum in ('db_lock','deployment','truncation','success_control'):
   g=[r for r in rows if r['arm']==arm and r['stratum']==stratum];assert len(g)==5
   strata.append({'arm':arm,'stratum':stratum,'n':len(g),'categories':dict(collections.Counter(r['score']['category'] for r in g)),'initialValid':sum(r['initialValid'] for r in g),'finalValid':sum(r['finalValid'] for r in g)})
 unusual=[{'case':r['case'],'arm':r['arm'],'decision':r['decision'],'score':r['score']} for r in rows if r['score']['category'] in ('unlisted_review_required','acceptable')]
 result={'id':s['id'],'status':s['status'],'validatedDecisions':80,'actualModelCalls':len(responses),'hardCap':120,'unresolvedStartedCalls':0,'originalInputsHashChecked':len(originals),'originalInputsChanged':changed,'protocolSha256':sha(R/'protocol.json'),'casesSha256':sha(R/'cases.json'),'runnerHashMatchesFrozen':True,'duplicateResultKeys':0,'replayToolDispatches':0,'dispatchEvidence':'Audited frozen runner posts only inference payloads to isolated Ollama; returned tool JSON is scored and saved, never dispatched. This is not a global audit of other users/processes.','arms':per_arm,'strata':strata,'identicalInitialRequests':identical,'identicalInitialResponseTexts':responses_same,'rubricReviewRequired':unusual,'caveat':'Automatic development action labels, not human diagnosis, live recovery, independent confirmation or a mechanism-isolating comparison. Context retention and cache/order differ.'}
 (R/'validation.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='rubricReviewRequired'},indent=2))
if __name__=='__main__':validate()
