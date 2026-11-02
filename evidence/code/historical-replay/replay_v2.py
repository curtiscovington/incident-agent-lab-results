"""V2: fixed-context budgeting and matched-fact organization; no tool execution."""
import collections,copy,fcntl,hashlib,json,os,pathlib,random,subprocess,sys,time,urllib.error,urllib.request
import replay as core

ROOT=pathlib.Path(__file__).resolve().parent
V1=ROOT/'runs/replay-20261101-v1';RUN=ROOT/'runs/replay-20261101-v2'
TOK=pathlib.Path('<LOCAL_HOME>/.cache/huggingface/hub/models--Qwen--Qwen3.8-27B-FP8/snapshots/017b9c7af6b5689d5dd426a76e0bc077eb5ca20a')
ARMS=('replication','budget_raw','facts_time','facts_entity')
LABELS={'replication':'Original history','budget_raw':'Budgeted raw history','facts_time':'Budgeted facts · time','facts_entity':'Budgeted facts · component'}
CAP=7000
INTRO='Prior observations only. Each item gives a historical source sequence, fact key and value. Facts may be ordered by time or grouped by component. Missing facts are unknown; do not invent them. Choose one next tool using the original instructions.\n'
_tokenizer=None
def tokenizer():
 global _tokenizer
 if _tokenizer is None:
  from transformers import AutoTokenizer
  _tokenizer=AutoTokenizer.from_pretrained(TOK,local_files_only=True,trust_remote_code=False)
 return _tokenizer
def count(messages):
 t=tokenizer();text=t.apply_chat_template(messages,tokenize=False,add_generation_prompt=True,enable_thinking=False)
 return len(t.encode(text,add_special_tokens=False))
def fact_pool(case):
 a=core.read(core.SOURCE/f"{case['sourceTrial']}-agent.json");prior=core.pairs(a)[:case['beforeStep']-1];facts={}
 def add(seq,key,value):
  if value is not None:facts[key]={'seq':seq,'key':key,'value':value}
 for e,event in prior:
  r=event['result'];seq=event['seq'];tool=e['tool'];target=e['target']
  for x in r.get('resources',[]):
   add(seq,'resource.'+x['id']+'.process',x.get('observed'));add(seq,'resource.'+x['id']+'.health',(x.get('measured') or {}).get('state'))
  for x in r.get('releases',[]):
   add(seq,'release.'+x['app']+'.current',x.get('release'));add(seq,'release.'+x['app']+'.known_good',(x.get('knownGood') or {}).get('release'))
  for k,v in (r.get('queue') or {}).items():add(seq,'business.queue.'+k,v)
  if tool=='logs':add(seq,'logs.'+target,r.get('text','')[-600:])
  if tool=='database_activity':
   for k in ('waitingSessions','maintenanceLockHolders','schedulerLeaseHolders'):add(seq,'database.'+k,r.get(k))
  if tool in ('verify','finish'):
   add(seq,'verification.passed',r.get('passed'));add(seq,'business.job_ok',(r.get('job') or {}).get('ok'))
  if tool=='connectivity':
   for x in r.get('checks',[]):add(seq,'connectivity.'+x.get('source','')+'.'+x.get('destination',''),x.get('reachable'))
  if tool=='network_policy':
   for k,v in r.get('observed',{}).items():add(seq,'network.'+k+'.blocked',v.get('blocked'))
  if tool=='messaging_status':
   for x in r.get('members',[]):add(seq,'messaging.'+x['id']+'.reachable',x.get('reachable'))
  if r.get('error'):add(seq,'tool_error.'+tool+'.'+target,r['error'])
 for e,event in prior[-6:]:add(event['seq'],'prior_action.'+str(e['seq']),{'tool':e['tool'],'target':e['target'],'action':e.get('action')})
 return sorted(facts.values(),key=lambda x:(x['seq'],x['key']))
def facts_messages(case,pool,order):
 units=sorted(pool,key=(lambda x:(x['seq'],x['key'])) if order=='time' else (lambda x:(x['key'],x['seq'])))
 return copy.deepcopy(case['messages'][:2])+[{'role':'user','content':INTRO+json.dumps({'prior_observations':units},separators=(',',':'),ensure_ascii=True)}]
def raw_budget(messages):
 out=copy.deepcopy(messages);removed=0
 while count(out)>CAP and len(out)>2:
  out=out[:2]+out[4:];removed+=1
 if count(out)>CAP:raise RuntimeError('Initial context alone exceeds cap')
 return out,removed
def freeze():
 if RUN.exists():raise RuntimeError('V2 exists; refusing overwrite')
 v1=core.read(V1/'protocol.json');cases=core.read(V1/'cases.json');calibration=[];prepared=[]
 for case in cases:
  full=count(case['messages']);old=core.read(V1/'attempts'/f"{case['id']}--baseline-a0.response.json")['prompt_eval_count']
  if full<=7808:
   if full!=old:raise RuntimeError('Offline template calibration mismatch '+case['id'])
   calibration.append({'case':case['id'],'offline':full,'recorded':old})
  raw,removed=raw_budget(case['messages']);pool=fact_pool(case);dropped=[]
  while max(count(facts_messages(case,pool,'time')),count(facts_messages(case,pool,'entity')))>CAP:
   if not pool:raise RuntimeError('Cannot fit fact record')
   dropped.append(pool.pop(0))
  time_msgs=facts_messages(case,pool,'time');entity_msgs=facts_messages(case,pool,'entity')
  time_tokens=count(time_msgs);entity_tokens=count(entity_msgs)
  if abs(time_tokens-entity_tokens)>8:raise RuntimeError('Matched-fact token lengths differ by more than 8')
  c={k:copy.deepcopy(case[k]) for k in ('id','sourceTrial','beforeStep','stratum','scenario','sourceArm','rubric','sourceSha256','evidence')}
  c.update(messagesByArm={'replication':case['messages'],'budget_raw':raw,'facts_time':time_msgs,'facts_entity':entity_msgs},offlineTokens={'replication':full,'budget_raw':count(raw),'facts_time':time_tokens,'facts_entity':entity_tokens},rawPairsDropped=removed,sharedFactPool=pool,factsDropped=len(dropped))
  prepared.append(c)
 assert len(calibration)>=5
 rng=random.Random(20261102);queue=[];order=list(prepared);rng.shuffle(order)
 for c in order:
  arms=list(ARMS);rng.shuffle(arms)
  queue.extend({'case':c['id'],'arm':arm} for arm in arms)
 protocol={'id':RUN.name,'frozenAt':time.time(),'purpose':'Development context-budget and matched-evidence-organization diagnostic; no tool execution','model':core.MODEL,'modelDigest':core.DIGEST,'options':v1['options'],'schema':v1['schema'],'arms':list(ARMS),'armLabels':LABELS,'queue':queue,'initialCalls':80,'maxInferenceCalls':80,'retries':0,'promptTokenCap':CAP,'responseTokenCap':384,'contextTokens':8192,'minimumReservedHeadroom':1192,'budgetValidationTolerance':2,'matchedFactsTokenTolerance':8,'primaryContrast':'facts_time versus facts_entity: identical shared fact multiset, same instructions, only ordering differs; predicted token counts within eight','secondaryContrasts':'replication versus budget_raw tests explicit oldest-pair pruning; raw versus normalized facts remains confounded by selected/normalized information and prompt length','policy':'Raw budget retains initial two messages and removes oldest complete action/result pairs until offline prompt<=7000. Shared facts are latest per key from completed prior observations, logs retain last600chars, last6 prior actions retained. If necessary drop oldest shared facts until both orders fit. No ground truth, future decisions, judge or rubric is inserted in any prompt. No replanning directive added.','selection':'Same twenty outcome-aware development cases and frozen action rubrics as v1. No held-out or live-recovery claim.','tokenizer':{'path':str(TOK),'files':{name:core.digest(TOK/name) for name in ('tokenizer.json','tokenizer_config.json','chat_template.jinja')},'thinking':False,'calibrationAgainstSavedV1':calibration},'sourceCasesSha256':core.digest(V1/'cases.json'),'sourceProtocolSha256':core.digest(V1/'protocol.json'),'scoring':'Reuse v1 schema and preferred/acceptable/unlisted rubrics unchanged; unlisted is review-required, not automatically wrong. Descriptive paired differences, no significance or acceptance threshold.','stopRule':'Stop on transport/uncertain call, input/model drift, actual budgeted prompt>7002 or matched-pair actual prompt difference>8. Never silently repeat an uncertain call. Preserve all outcomes; no post-outcome retuning. Pause between calls.','limitations':['Twenty outcome-aware development states, not independent confirmation.','Facts-time and facts-entity control fact availability and near-exact prompt length, but test organization of an already normalized record, not semantics or memory mechanisms generally.','Raw versus fact-record contrasts cannot isolate budgeting from normalization, omitted details, recency or length.','Unbudgeted replication intentionally retains historical context-pressure behavior.','Historical source prompts mix current/runbook but are fixed within every case.','Automatic action rubric; no live repairs or human diagnosis adjudication.','Offline HF tokenizer matches recorded under-cap prompts; actual serving counters are checked per call.','Cache/order affects latency; no intrinsic speed claim.']}
 RUN.mkdir(parents=True);core.atomic(RUN/'cases.json',prepared);core.atomic(RUN/'protocol.json',protocol)
 (RUN/'protocol.sha256').write_text(core.digest(RUN/'protocol.json'));(RUN/'cases.sha256').write_text(core.digest(RUN/'cases.json'))
 core.atomic(RUN/'code-hashes.json',{'replay_v2.py':core.digest(ROOT/'replay_v2.py'),'replay.py':core.digest(ROOT/'replay.py')})
 core.atomic(RUN/'state.json',{'id':RUN.name,'status':'paused','stage':'Frozen v2; awaiting launch','completed':0,'total':80,'inferenceCalls':0,'maxInferenceCalls':80,'armLabels':LABELS,'designDescription':'Fixed 8192 context / 384 output. Three budgeted arms cap prompts at 7000 tokens. Facts arms share identical observations and near-equal lengths; no extra replanning rule.','results':[],'protocolSha256':core.digest(RUN/'protocol.json'),'casesSha256':core.digest(RUN/'cases.json'),'reviewStatus':'Automatic development rubrics; no live recovery; human review pending'})
 (RUN/'pause').touch();print(json.dumps({'run':RUN.name,'calibrationCases':len(calibration),'protocol':core.digest(RUN/'protocol.json'),'tokenCounts':[{'case':c['id'],**c['offlineTokens']} for c in prepared]},indent=2))
def run():
 lock=(RUN/'worker.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 s=core.read(RUN/'state.json');p=core.read(RUN/'protocol.json');cases={c['id']:c for c in core.read(RUN/'cases.json')};proc=None;log=None
 saved=[core.read(f) for f in sorted((RUN/'results').glob('*.json'))];s.update(completed=len(saved),results=saved)
 def save(**v):s.update(v,updatedAt=time.time());core.atomic(RUN/'state.json',s)
 def check():
  for name in ('cases','protocol'):
   if core.digest(RUN/(name+'.json'))!=(RUN/(name+'.sha256')).read_text().strip():raise RuntimeError('Frozen input drift')
  for name,h in core.read(RUN/'code-hashes.json').items():
   if core.digest(ROOT/name)!=h:raise RuntimeError('Code drift '+name)
  if core.digest(V1/'cases.json')!=p['sourceCasesSha256'] or core.digest(V1/'protocol.json')!=p['sourceProtocolSha256']:raise RuntimeError('V1 drift')
  for c in cases.values():
   if core.digest(core.SOURCE/f"{c['sourceTrial']}-agent.json")!=c['sourceSha256']:raise RuntimeError('Original trace drift')
 def pause():
  while (RUN/'pause').exists():save(status='paused',stage='Paused between calls');time.sleep(.5)
  save(status='running')
 try:
  check()
  for marker in (RUN/'attempts').glob('*.started.json'):
   if not marker.with_name(marker.name.replace('.started.json','.response.json')).exists():raise RuntimeError('Uncertain prior call; refusing automatic repetition')
  pause();save(stage='Checking workload and starting isolated model')
  if core.read_json_url('http://127.0.0.1:11436/api/ps').get('models'):raise RuntimeError('Other Ollama workload resident')
  mem=int(next(x.split()[1] for x in pathlib.Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))
  if mem<50*1024*1024:raise RuntimeError('Insufficient available memory')
  try:core.request('/api/version');raise RuntimeError('Port11439 occupied; refusing takeover')
  except urllib.error.URLError:pass
  env=dict(os.environ);env.update(OLLAMA_HOST='127.0.0.1:11439',OLLAMA_MODELS='/usr/share/ollama/.ollama/models',OLLAMA_NUM_PARALLEL='1',OLLAMA_MAX_LOADED_MODELS='1',HOME=str(ROOT/'.runtime/ollama-home-v2'))
  pathlib.Path(env['HOME']).mkdir(parents=True,exist_ok=True);log=(RUN/'ollama.log').open('a');proc=subprocess.Popen(['<LOCAL_HOME>/.local/opt/pokemon-ollama-0.35.1/bin/ollama','serve'],env=env,stdout=log,stderr=log)
  deadline=time.time()+30
  while True:
   try:version=core.request('/api/version');break
   except urllib.error.URLError:
    if time.time()>deadline or proc.poll() is not None:raise RuntimeError('Model server startup failed')
    time.sleep(.5)
  model=next(x for x in core.request('/api/tags')['models'] if x['name']==core.MODEL)
  if model['digest']!=core.DIGEST:raise RuntimeError('Model digest mismatch')
  core.atomic(RUN/'environment.json',{'server':version,'model':model,'python':sys.version,'kernel':os.uname().release,'memoryAvailableKiB':mem})
  for i,q in enumerate(p['queue']):
   key=q['case']+'--'+q['arm'];out=RUN/'results'/(key+'.json');c=cases[q['case']];arm=q['arm']
   if out.exists():continue
   check();pause();save(current={'index':i+1,'case':c['id'],'arm':arm,'stratum':c['stratum']},stage=f'Inference {i+1}/80')
   base=RUN/'attempts'/key;response_path=base.with_suffix('.response.json')
   if response_path.exists():r=core.read(response_path)
   else:
    if s['inferenceCalls']>=80:raise RuntimeError('Hard 80-call cap reached')
    payload={'model':core.MODEL,'messages':c['messagesByArm'][arm],'stream':False,'think':False,'format':p['schema'],'options':p['options'],'keep_alive':'5m'}
    core.atomic(base.with_suffix('.request.json'),payload);core.atomic(base.with_suffix('.started.json'),{'at':time.time(),'case':c['id'],'arm':arm});save(inferenceCalls=s['inferenceCalls']+1)
    r=core.request('/api/chat',payload,timeout=180);core.atomic(response_path,r)
   decision,error=core.validate_decision(r,p['schema']);prompt=r.get('prompt_eval_count',0);output=r.get('eval_count',0)
   row={'case':c['id'],'arm':arm,'stratum':c['stratum'],'scenario':c['scenario'],'sourceTrial':c['sourceTrial'],'beforeStep':c['beforeStep'],'initialValid':decision is not None,'finalValid':decision is not None,'decision':decision,'score':core.score(c,decision),'offlinePromptTokens':c['offlineTokens'][arm],'actualPromptTokens':prompt,'reservedHeadroom':8192-prompt,'budgeted':arm!='replication','budgetAdherent':arm=='replication' or prompt<=7002,'tokenCountDelta':prompt-c['offlineTokens'][arm],'attempts':[{'attempt':0,'schemaValid':decision is not None,'error':error,'doneReason':r.get('done_reason'),'promptTokens':prompt,'outputTokens':output,'totalDurationNs':r.get('total_duration',0),'responseText':r.get('message',{}).get('content',''),'responseFile':str(response_path.relative_to(RUN))}],'savedAt':time.time()}
   core.atomic(out,row);rows=[core.read(f) for f in sorted((RUN/'results').glob('*.json'))];save(completed=len(rows),results=rows,stage='Checkpoint saved')
   if not row['budgetAdherent']:raise RuntimeError('Actual prompt budget violated; outcome saved')
   if arm!='replication' and abs(row['tokenCountDelta'])>2:raise RuntimeError('Offline/actual tokenizer mismatch; outcome saved')
   pair=[x for x in rows if x['case']==c['id'] and x['arm'] in ('facts_time','facts_entity')]
   if len(pair)==2 and abs(pair[0]['actualPromptTokens']-pair[1]['actualPromptTokens'])>8:raise RuntimeError('Matched fact-arm prompt lengths differ by >8')
   if len(rows)==4:(RUN/'pause').touch();save(status='paused',stage='First four saved; budget-validation gate')
  save(status='complete',current=None,stage='V2 complete · automatic review pending')
 except Exception as e:save(status='blocked',stage='Stopped for inspection',error=str(e));raise
 finally:
  if proc is not None:
   proc.terminate()
   try:proc.wait(timeout=20)
   except subprocess.TimeoutExpired:proc.kill();proc.wait()
  if log:log.close()
if __name__=='__main__':
 if sys.argv[1]=='freeze':freeze()
 elif sys.argv[1]=='run':run()
