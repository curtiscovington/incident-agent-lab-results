"""Bounded, read-only decision replay. Model suggestions are NEVER dispatched."""
import argparse, collections, copy, fcntl, hashlib, json, os, pathlib, random, subprocess, sys, time, urllib.request

ROOT=pathlib.Path(__file__).resolve().parent
SOURCE=pathlib.Path('<LOCAL_HOME>/Dev/datacenter-lab/.runtime/agent-recovery/e209d9ca6f1d4afabebff52941c58deb')
RUN=ROOT/'runs/replay-20261101-v1'
MODEL='qwen3.8:27b-q4_K_M'
DIGEST='25b843619e944cd0ae6069f94ff4e5e26a16e109ccbc0a66a0f05979ed70098e'
ENDPOINT='http://127.0.0.1:11439'
ARMS=('baseline','evidence_replan','format_retry','both')
EVIDENCE_INSTRUCTION='\nReplay development intervention: Retain the verified facts in the evidence record. Component health alone does not prove business recovery. If a tool/target has already been checked without a repair or material new evidence, reconsider the diagnosis and choose a discriminating diagnostic action or a supported scoped repair; do not simply repeat the same investigation cycle. Do not invent facts. The record summarizes past tool results, not new observations.'
REPAIR_INSTRUCTION='The preceding response was not a valid schema-conforming decision. Return exactly one complete JSON object conforming to the supplied schema. Keep summary brief (one sentence). Select an action using only the existing evidence. No tools have been executed since the last observation.'

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def read(path):return json.loads(path.read_text())
def atomic(path,data):
 path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data,indent=2));tmp.replace(path)
def request(path,data=None,timeout=15):
 req=urllib.request.Request(ENDPOINT+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=timeout) as r:return json.load(r)
def observation(tool,result):
 text=json.dumps({'tool':tool,'result':result})
 if len(text)<=12000:return text
 preview=text[:9000]
 while True:
  text=json.dumps({'tool':tool,'result':{'truncated':True,'detail':'Tool output exceeds the observation budget. Inspect an individual component for complete evidence.','preview':preview}})
  if len(text)<=12000:return text
  preview=preview[:len(preview)//2]
def pairs(a):
 out=[]
 for i,e in enumerate(a['events']):
  if e.get('kind')!='decision':continue
  for nxt in a['events'][i+1:]:
   if nxt.get('kind')=='decision':break
   if nxt.get('kind')=='result':out.append((e,nxt));break
 return out
def history(a,before):
 msgs=copy.deepcopy(a['messages'][:2]);prior=pairs(a)[:before-1]
 for index,(e,r) in enumerate(prior):
  decision=json.loads(a['modelResponses'][index]['response']['message']['content'])
  assert decision['tool']==e['tool'] and decision['target']==e['target']
  msgs += [{'role':'assistant','content':json.dumps(decision)},{'role':'user','content':observation(e['tool'],r['result'])}]
  if len(msgs)>14:msgs=msgs[:2]+msgs[-12:]
 return msgs,prior
def evidence_record(a,prior):
 # Only facts actually returned before the selected decision; no injection/judge fields.
 ctx=json.loads(a['messages'][1]['content']);facts={'initialBusiness':ctx.get('business'),'initialAlarms':ctx.get('alarms',[]),'initialQueue':ctx.get('queue',{}),'latestResources':{},'releases':{},'checks':{},'recentActions':[]}
 for e,event in prior:
  r=event['result'];tool=e['tool'];target=e['target']
  for x in r.get('resources',[]):facts['latestResources'][x['id']]={'observed':x.get('observed'),'health':(x.get('measured') or {}).get('state'),'evidenceSeq':event['seq']}
  for x in r.get('releases',[]):facts['releases'][x['app']]={'release':x.get('release'),'knownGood':(x.get('knownGood') or {}).get('release'),'evidenceSeq':event['seq']}
  if r.get('queue') is not None:facts['queue']=r['queue']
  if tool=='logs':facts['checks'][tool+':'+target]={'text':r.get('text','')[-600:],'evidenceSeq':event['seq']}
  if tool=='database_activity':facts['checks'][tool+':'+target]={k:r.get(k) for k in ('waitingSessions','maintenanceLockHolders','schedulerLeaseHolders')};facts['checks'][tool+':'+target]['evidenceSeq']=event['seq']
  if tool in ('verify','finish'):facts['checks'][tool+':'+target]={'passed':r.get('passed'),'businessOk':(r.get('job') or {}).get('ok'),'evidenceSeq':event['seq']}
  facts['recentActions'].append({'tool':tool,'target':target,'action':e.get('action'),'evidenceSeq':event['seq']})
 return facts
def match(decision,patterns):
 return any(all(decision.get(k)==v for k,v in pattern.items()) for pattern in patterns)
def validate_decision(response,schema):
 try:d=json.loads(response['message']['content'])
 except (ValueError,KeyError,TypeError):return None,'invalid JSON or absent content'
 if not isinstance(d,dict) or set(d)!=set(schema['required']):return None,'missing/extra schema fields'
 for k,p in schema['properties'].items():
  if not isinstance(d[k],str) or ('enum' in p and d[k] not in p['enum']):return None,'invalid field '+k
 if response.get('done_reason')!='stop':return None,'response did not finish normally'
 return d,None
def score(case,decision):
 if decision is None:return {'category':'invalid','ambiguous':False,'repeatToolTarget':False}
 preferred=match(decision,case['rubric']['preferred']);acceptable=match(decision,case['rubric']['acceptable'])
 recent={(x['tool'],x['target']) for x in case['evidence']['recentActions']}
 return {'category':'preferred' if preferred else 'acceptable' if acceptable else 'unlisted_review_required','ambiguous':not preferred,'repeatToolTarget':(decision['tool'],decision['target']) in recent,'evidenceUseProxy':bool(preferred)}
def create():
 if RUN.exists():raise RuntimeError('Run already exists; refusing to replace frozen inputs')
 original=read(SOURCE/'protocol.json');assert digest(SOURCE/'protocol.json')==(SOURCE/'protocol.sha256').read_text().strip()
 # Five cases per stratum, selected by trial and decision index BEFORE generation.
 specs=[(7,13,'db_lock'),(8,13,'db_lock'),(30,13,'db_lock'),(35,13,'db_lock'),(36,13,'db_lock'),(5,5,'deployment'),(18,6,'deployment'),(46,14,'deployment'),(86,15,'deployment'),(130,12,'deployment'),(11,11,'truncation'),(45,7,'truncation'),(85,8,'truncation'),(158,7,'truncation'),(91,10,'truncation'),(1,7,'success_control'),(3,6,'success_control'),(12,3,'success_control'),(15,3,'success_control'),(29,15,'success_control')]
 cases=[]
 for n,before,stratum in specs:
  a=read(SOURCE/f'{n}-agent.json');row=read(SOURCE/f'{n}-result.json');msgs,prior=history(a,before)
  assert len(prior)==before-1 and before<=len(a['modelResponses'])
  sc=row['scenario'];preferred=[];acceptable=[]
  if sc=='database-lock' and stratum!='success_control':
   preferred=[{'tool':'database_activity','target':'postgres'}]
   acceptable=[{'tool':'verify','target':'control-plane'},{'tool':'inspect','target':'control-plane'},{'tool':'connectivity','target':'control-plane'}]
   rationale='Healthy component checks coexist with failed business work; database activity is discriminating. Fresh verify/inspect/connectivity remains plausible, not proof of correct diagnosis.'
  elif sc in ('api-regression','worker-backlog'):
   target='workers' if sc=='worker-backlog' else 'job-api';targets=[target] if target=='workers' else ['job-api','api-1','api-2']
   preferred=[{'tool':'rollback','target':x} for x in targets]
   acceptable=[{'tool':'logs','target':x} for x in (['worker-1','worker-2','worker-3'] if target=='workers' else ['api-1','api-2'])]+[{'tool':'inspect','target':target}]
   rationale='Release mismatch and available failure evidence support scoped rollback. Further focused release/log inspection is plausible. Any single-replica rollback is only a next action, not complete recovery.'
  elif sc=='scheduler-lease':
   preferred=[{'tool':'release_scheduler_lease','target':'schedulers'}];acceptable=[{'tool':'database_activity','target':'postgres'}];rationale='Prior database activity identified abandoned scheduler lease holder.'
  elif sc=='scheduler-outage':
   preferred=[{'tool':'power','target':'schedulers','action':'start'},{'tool':'power','target':'scheduler-1','action':'start'},{'tool':'power','target':'scheduler-2','action':'start'}];acceptable=[{'tool':'inspect','target':'schedulers'}];rationale='Stopped scheduler evidence supports starting affected publishers.'
  elif sc=='database-route':
   preferred=[{'tool':'restore_network','target':'job-api'}];acceptable=[{'tool':'network_policy','target':'job-api'}];rationale='Prior connectivity and policy evidence identified API blackhole routes.'
  elif sc=='broker-quorum':
   preferred=[{'tool':'power','target':'nats-2','action':'start'},{'tool':'power','target':'nats-3','action':'start'}];acceptable=[{'tool':'messaging_status','target':'messaging'}];rationale='Missing broker members observed; start a missing member without deleting data.'
  else:
   preferred=[{'tool':'release_database_lock','target':'postgres'}];acceptable=[{'tool':'database_activity','target':'postgres'}];rationale='Prior activity directly identified maintenance table-lock holder.'
  case={'id':f't{n:03d}-s{before:02d}','sourceTrial':n,'beforeStep':before,'stratum':stratum,'scenario':sc,'sourceArm':row['condition'],'messages':msgs,'evidence':evidence_record(a,prior),'rubric':{'preferred':preferred,'acceptable':acceptable,'rationale':rationale,'unlisted':'Review required; not automatically wrong/unsafe'},'sourceSha256':digest(SOURCE/f'{n}-agent.json')}
  cases.append(case)
 queue=[];rng=random.Random(20261101)
 order=list(range(20));rng.shuffle(order)
 for i in order:
  arms=list(ARMS);rng.shuffle(arms)
  for arm in arms:queue.append({'case':cases[i]['id'],'arm':arm})
 protocol={'id':RUN.name,'type':'Development next-decision replay; never live tool execution','frozenAt':time.time(),'model':MODEL,'modelDigest':DIGEST,'endpoint':ENDPOINT,'options':original['options'],'schema':original['schema'],'arms':list(ARMS),'initialCalls':80,'maxRetryPerApplicableDecision':1,'maxInferenceCalls':120,'queue':queue,'evidenceInstruction':EVIDENCE_INSTRUCTION,'repairInstruction':REPAIR_INSTRUCTION,'primary':'Descriptive preferred/acceptable/unlisted next-action counts and schema validity; no significance or acceptance threshold','limitations':['Outcome-aware development case selection, not held-out confirmation','Saved trajectories are teacher-forced; no downstream recovery or safety conclusion','Source current/runbook prompts retained; source-arm mix is fixed across replay arms','Preferred action rubric uses known fault and prior trace evidence; unlisted choices require human review','evidenceUseProxy is next-action match, not a semantic or causal evidence-use rating','Natural malformed replies may not recur; no forced corruption in model experiment','Same quantized model and decoding settings; isolated Ollama 0.35.1 serving runtime recorded, exact historical server equivalence unverified','No retries after an uncertain request outcome; stop for inspection','No p-values or tuning after results'],'stopRule':'Run frozen queue to completion, stop on server/model/source drift or uncertain inference; pause between calls, save each attempt, resume completed records without repetition','selection':'Five fixed cases each: database-lock failures, deployment-evidence failures, historical truncations, successful controls. Not blinded. See cases.json for exact trial and before-step indices.','scoring':'Deterministic JSON-schema validator plus explicit preferred/acceptable action tuples; all other valid decisions marked unlisted_review_required; not human diagnosis adjudication','sourceProtocolSha256':digest(SOURCE/'protocol.json'),'hardware':{'machine':os.uname().machine}}
 RUN.mkdir(parents=True);atomic(RUN/'cases.json',cases);atomic(RUN/'protocol.json',protocol)
 (RUN/'protocol.sha256').write_text(digest(RUN/'protocol.json'));(RUN/'cases.sha256').write_text(digest(RUN/'cases.json'))
 atomic(RUN/'state.json',{'id':RUN.name,'status':'paused','stage':'Frozen; awaiting launch','completed':0,'total':80,'inferenceCalls':0,'maxInferenceCalls':120,'results':[],'protocolSha256':digest(RUN/'protocol.json'),'casesSha256':digest(RUN/'cases.json'),'reviewStatus':'Automated development rubric; human review pending'})
 (RUN/'pause').touch()
 return protocol

def arm_messages(case,arm,protocol):
 msgs=copy.deepcopy(case['messages'])
 if arm in ('evidence_replan','both'):
  msgs[0]['content']+=protocol['evidenceInstruction']
  msgs.append({'role':'user','content':'Verified prior-evidence record (not fresh observations):\n'+json.dumps(case['evidence'])})
 return msgs
def run():
 lock=(RUN/'worker.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 state=read(RUN/'state.json');protocol=read(RUN/'protocol.json');cases={c['id']:c for c in read(RUN/'cases.json')};proc=None;log=None
 saved=[read(x) for x in sorted((RUN/'results').glob('*.json'))] if (RUN/'results').exists() else []
 state.update(completed=len(saved),results=saved)
 def save(**values):state.update(values,updatedAt=time.time());atomic(RUN/'state.json',state)
 def check_frozen():
  for name in ('protocol','cases'):
   if digest(RUN/(name+'.json'))!=(RUN/(name+'.sha256')).read_text().strip():raise RuntimeError('Frozen input drift: '+name)
  if digest(ROOT/'replay.py')!=read(RUN/'code-hashes.json')['replay.py']:raise RuntimeError('Runner source drift')
  for c in cases.values():
   if digest(SOURCE/f"{c['sourceTrial']}-agent.json")!=c['sourceSha256']:raise RuntimeError('Source trace drift')
 def wait_pause():
  while (RUN/'pause').exists():save(status='paused',stage='Paused between model calls');time.sleep(.5)
  save(status='running')
 try:
  check_frozen()
  # Fail closed if an inference attempt was started without an atomic completion.
  for marker in (RUN/'attempts').glob('*.started.json') if (RUN/'attempts').exists() else []:
   if not marker.with_name(marker.name.replace('.started.json','.response.json')).exists():raise RuntimeError('Uncertain prior inference: '+marker.name+'; will not repeat automatically')
  wait_pause();save(stage='Checking workload and starting isolated model server')
  if read_json_url('http://127.0.0.1:11436/api/ps').get('models'):raise RuntimeError('Other Ollama workload resident; refusing to compete')
  mem=int(next(x.split()[1] for x in pathlib.Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))
  if mem<50*1024*1024:raise RuntimeError('Less than 50 GiB available; refusing model load')
  manifest=pathlib.Path('/usr/share/ollama/.ollama/models/manifests/registry.ollama.ai/library/qwen3.8/27b-q4_K_M')
  if digest(manifest)!=DIGEST:raise RuntimeError('Installed manifest digest mismatch')
  try:request('/api/version');raise RuntimeError('Isolated port already occupied; refusing takeover')
  except urllib.error.URLError:pass
  env=dict(os.environ);env.update(OLLAMA_HOST='127.0.0.1:11439',OLLAMA_MODELS='/usr/share/ollama/.ollama/models',OLLAMA_NUM_PARALLEL='1',OLLAMA_MAX_LOADED_MODELS='1',OLLAMA_KEEP_ALIVE='5m',HOME=str(ROOT/'.runtime/ollama-home'))
  pathlib.Path(env['HOME']).mkdir(parents=True,exist_ok=True)
  log=(RUN/'ollama.log').open('a');proc=subprocess.Popen(['<LOCAL_HOME>/.local/opt/pokemon-ollama-0.35.1/bin/ollama','serve'],env=env,stdout=log,stderr=log)
  deadline=time.time()+30
  while True:
   try:version=request('/api/version');break
   except urllib.error.URLError:
    if time.time()>deadline or proc.poll() is not None:raise RuntimeError('Isolated server failed to start')
    time.sleep(.5)
  tags=request('/api/tags')['models'];installed=next(m for m in tags if m['name']==MODEL)
  if installed['digest']!=DIGEST:raise RuntimeError('Serving digest mismatch')
  atomic(RUN/'environment.json',{'server':version,'model':installed,'python':sys.version,'kernel':os.uname().release,'availableKiBAtStart':mem,'capturedAt':time.time()})
  for index,item in enumerate(protocol['queue']):
   case=cases[item['case']];arm=item['arm'];key=case['id']+'--'+arm;out=RUN/'results'/(key+'.json')
   if out.exists():continue
   check_frozen();wait_pause();save(stage='Selecting next action',current={'index':index+1,'case':case['id'],'arm':arm,'stratum':case['stratum']})
   msgs=arm_messages(case,arm,protocol);attempts=[];decision=None
   for attempt in range(2 if arm in ('format_retry','both') else 1):
    wait_pause();base=RUN/'attempts'/(key+f'-a{attempt}')
    response_path=base.with_suffix('.response.json');request_path=base.with_suffix('.request.json');marker=base.with_suffix('.started.json')
    payload={'model':MODEL,'messages':msgs,'stream':False,'think':False,'format':protocol['schema'],'options':protocol['options'],'keep_alive':'5m'}
    if response_path.exists():response=read(response_path)
    else:
     if state['inferenceCalls']>=120:raise RuntimeError('Hard inference cap reached')
     atomic(request_path,payload);atomic(marker,{'at':time.time(),'case':case['id'],'arm':arm,'attempt':attempt})
     save(inferenceCalls=state['inferenceCalls']+1,stage=f'Inference {index+1}/80'+(' · bounded format retry' if attempt else ''))
     started=time.time();response=request('/api/chat',payload,timeout=180)
     atomic(response_path,response);atomic(base.with_suffix('.timing.json'),{'wallSeconds':time.time()-started})
    decision,error=validate_decision(response,protocol['schema']);attempts.append({'attempt':attempt,'schemaValid':decision is not None,'error':error,'doneReason':response.get('done_reason'),'totalDurationNs':response.get('total_duration',0),'promptTokens':response.get('prompt_eval_count',0),'outputTokens':response.get('eval_count',0),'responseFile':str(response_path.relative_to(RUN)),'responseText':response.get('message',{}).get('content','')})
    if decision is not None:break
    if attempt==0 and arm in ('format_retry','both'):msgs=msgs+[{'role':'assistant','content':response.get('message',{}).get('content','')},{'role':'user','content':protocol['repairInstruction']}]
   row={'case':case['id'],'arm':arm,'stratum':case['stratum'],'scenario':case['scenario'],'sourceTrial':case['sourceTrial'],'beforeStep':case['beforeStep'],'initialValid':attempts[0]['schemaValid'],'finalValid':decision is not None,'decision':decision,'score':score(case,decision),'attempts':attempts,'savedAt':time.time()};atomic(out,row)
   results=[read(x) for x in sorted((RUN/'results').glob('*.json'))]
   save(completed=len(results),results=results,stage='Checkpoint saved')
   if len(results)==4: # bounded early inspection gate, visible and manually resumable
    (RUN/'pause').touch();save(status='paused',stage='First four decisions saved; early spot-check gate')
  save(status='complete',stage='80 decisions saved · automated rubric review pending',current=None)
 except Exception as e:save(status='blocked',stage='Stopped for inspection',error=str(e));raise
 finally:
  if proc is not None:
   proc.terminate()
   try:proc.wait(timeout=20)
   except subprocess.TimeoutExpired:proc.kill();proc.wait()
  if log:log.close()
def read_json_url(url):
 with urllib.request.urlopen(url,timeout=5) as r:return json.load(r)
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('action',choices=['freeze','run']);args=parser.parse_args()
 if args.action=='freeze':
  create();atomic(RUN/'code-hashes.json',{'replay.py':digest(ROOT/'replay.py')});print(RUN)
 else:run()
