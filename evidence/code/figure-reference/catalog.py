"""Small declarative figure schema plus saved-data adapters. No inference."""
import hashlib,json,pathlib,re
ROOT=pathlib.Path('<LOCAL_HOME>/Dev/datacenter-lab');DATA=ROOT/'.runtime/figures'
STUDY=ROOT/'.runtime/agent-recovery/e209d9ca6f1d4afabebff52941c58deb'
REPLAY=pathlib.Path('<LOCAL_HOME>/Dev/incident-decision-replay');V2=REPLAY/'runs/replay-20261101-v2'
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def source(p,label,run):return {'label':label,'run':run,'sha256':sha(p),'path':str(p)}
def project_id():return read(REPLAY/'.runtime/native-lab-project.json')['projectId']
def validate(s):
 allowed={'schemaVersion','id','projectId','kind','title','subtitle','data','caveats','sources','caption','eyebrow'}
 if not isinstance(s,dict) or set(s)-allowed:raise ValueError('Unknown figure fields')
 if s.get('schemaVersion')!=1 or not re.fullmatch('[a-z][a-z0-9-]{1,59}',s.get('id','')):raise ValueError('Expected schemaVersion 1 and a slug id')
 if s.get('kind') not in ('paired_counts','token_budget','matrix_counts','system_flow','evidence_sequence'):raise ValueError('Unsupported figure kind')
 if not re.fullmatch('[a-f0-9]{32}',s.get('projectId','')):raise ValueError('Expected an existing Lab project id')
 for k in ('title','subtitle','caption','eyebrow'):
  if not isinstance(s.get(k),str) or len(s[k])>500:raise ValueError('Invalid '+k)
 if not isinstance(s.get('caveats'),list) or not 1<=len(s['caveats'])<=4 or any(not isinstance(x,str) or len(x)>500 for x in s['caveats']):raise ValueError('Invalid caveats')
 if not isinstance(s.get('sources'),list) or not 1<=len(s['sources'])<=8:raise ValueError('Sources required')
 for src in s['sources']:
  if not isinstance(src,dict) or not all(isinstance(src.get(k),str) for k in ('label','run','sha256')) or not re.fullmatch('[a-f0-9]{64}',src['sha256']):raise ValueError('Invalid provenance')
 d=s.get('data')
 if not isinstance(d,dict):raise ValueError('Data must be an object')
 def integer(x,lo,hi):return type(x) is int and lo<=x<=hi
 if s['kind']=='paired_counts':
  if not integer(d.get('denominator'),1,10000) or not 1<=len(d.get('rows',[]))<=12:raise ValueError('Invalid paired data')
  for r in d['rows']:
   if not isinstance(r.get('label'),str) or len(r['label'])>80 or not all(integer(r.get(k),0,d['denominator']) for k in ('a','b')):raise ValueError('Invalid paired row')
 elif s['kind']=='token_budget':
  if not integer(d.get('capacity'),1,1000000) or not integer(d.get('outputCap'),1,d['capacity']) or len(d.get('rows',[]))!=2:raise ValueError('Invalid token budget')
  for r in d['rows']:
   if not all(integer(r.get(k),0,d['capacity']) for k in ('prompt','output')) or r['prompt']+r['output']>d['capacity']:raise ValueError('Invalid token row')
  if not integer(d.get('decisionDenominator'),1,10000) or not all(integer(d.get(k),0,d['decisionDenominator']) for k in ('validBefore','validAfter')):raise ValueError('Invalid validity counts')
 elif s['kind']=='matrix_counts':
  if not integer(d.get('denominator'),1,10000) or not 1<=len(d.get('columns',[]))<=6 or not 1<=len(d.get('rows',[]))<=6:raise ValueError('Invalid matrix')
  for r in d['rows']:
   if len(r.get('values',[]))!=len(d['columns']) or not all(integer(x,0,d['denominator']) for x in r['values']):raise ValueError('Invalid matrix row')
 elif s['kind']=='system_flow':
  if not 1<=len(d.get('nodes',[]))<=12 or not 1<=len(d.get('edges',[]))<=20:raise ValueError('Invalid flow')
  ids=[n.get('id') for n in d['nodes']]
  if len(set(ids))!=len(ids):raise ValueError('Duplicate nodes')
  for n in d['nodes']:
   if not all(integer(n.get(k),0,1050) for k in ('x','y','w','h')) or n['x']+n['w']>1000 or n['y']+n['h']>1050:raise ValueError('Node outside frame')
   if not isinstance(n.get('title'),str) or not 1<=len(n.get('lines',[]))<=3:raise ValueError('Invalid node label')
  for e in d['edges']:
   if e.get('kind') not in ('work','control','judge') or not 2<=len(e.get('points',[]))<=8:raise ValueError('Invalid edge')
   if any(len(p)!=2 or not all(integer(v,0,1050) for v in p) for p in e['points']+[e['labelAt']]):raise ValueError('Invalid coordinates')
 elif s['kind']=='evidence_sequence':
  if len(d.get('stages',[]))!=4 or not integer(d.get('case'),1,100000):raise ValueError('Invalid sequence')
  for stage in d['stages']:
   if not all(isinstance(stage.get(k),str) for k in ('title','time')) or not 1<=len(stage.get('lines',[]))<=4:raise ValueError('Invalid stage')
   if len(stage.get('args',[]))>2:raise ValueError('Too many arguments')
 if len(json.dumps(s))>100000:raise ValueError('Figure spec exceeds 100 KB')
 return s
def builtins():
 project=project_id();summary=read(STUDY/'summary.json');protocol=read(STUDY/'protocol.json');analysis=read(V2/'analysis.json')
 names={'api-regression':'API deployment','worker-backlog':'Worker deployment','database-route':'Database route','database-lock':'Database lock','scheduler-outage':'Scheduler outage','scheduler-lease':'Scheduler leadership','broker-quorum':'Broker quorum','worker-loss':'Single worker loss'}
 order=list(names);cells={(x['scenario'],x['condition']):x['passed'] for x in summary['cells']}
 a={'schemaVersion':1,'id':'recovery-by-scenario','projectId':project,'kind':'paired_counts','eyebrow':'01 / INCIDENT RESPONSE','title':'The runbook helped deployment faults most','subtitle':'Immediate verified recovery on eight known faults · 10 attempts per condition','data':{'denominator':10,'aLabel':'Current agent','bLabel':'Agent + runbook','rows':[{'label':names[k],'a':cells[k,'current'],'b':cells[k,'runbook']} for k in order]},'caveats':['Repeated executions share faults, fixture, model and seed. These are descriptive counts, not general incident-reliability estimates.','Independent checks measure immediate repair; diagnosis review and sustained recurrence testing remain outstanding.'],'sources':[source(STUDY/'summary.json','Control Plane repeated study','e209d9ca6f1d4afabebff52941c58deb')],'caption':'The runbook improved immediate verified repair from 57/80 to 67/80 in this fixed Lab, concentrated in deployment and leadership faults. Database locking remained unresolved.'}
 before=read(V2/'results/t158-s07--replication.json');after=read(V2/'results/t158-s07--budget_raw.json');arms={x['arm']:x for x in analysis['arms']}
 b={'schemaVersion':1,'id':'context-headroom','projectId':project,'kind':'token_budget','eyebrow':'02 / CONTEXT BUDGET','title':'A full prompt left only 19 tokens for the response','subtitle':'One saved decision · same model, seed and 8,192-token context','data':{'capacity':8192,'outputCap':384,'case':'t158-s07','rows':[{'label':'Original history','prompt':before['actualPromptTokens'],'output':before['attempts'][0]['outputTokens'],'valid':before['finalValid']},{'label':'Explicit prompt budget','prompt':after['actualPromptTokens'],'output':after['attempts'][0]['outputTokens'],'valid':after['finalValid']}],'responseText':before['attempts'][0]['responseText'],'validBefore':arms['replication']['valid'],'validAfter':arms['budget_raw']['valid'],'decisionDenominator':20},'caveats':['Bars show prompt occupancy and usable response allowance, not relative compute time. The output cap stays fixed at 384 tokens.','History retention changed too. This is a development next-decision replay; no repair tools executed.'],'sources':[source(V2/'results/t158-s07--replication.json','Original-history replay','replay-20261101-v2'),source(V2/'results/t158-s07--budget_raw.json','Budgeted-history replay','replay-20261101-v2')],'caption':'In case t158-s07, 8,173 prompt tokens left only 19 response tokens and produced truncated JSON. Explicit budgeting left enough room for a valid decision. Across the 20 states, valid JSON increased from 15/20 to 20/20.'}
 strata={(x['arm'],x['stratum']):x for x in analysis['strata']};kinds=['db_lock','deployment','truncation','success_control'];labels=[('replication','Original history','Unbudgeted replication'),('budget_raw','Budgeted raw history','Oldest-pair pruning'),('facts_time','Compact facts · time','Chronological order'),('facts_entity','Compact facts · component','Grouped by component')]
 c={'schemaVersion':1,'id':'replay-action-matrix','projectId':project,'kind':'matrix_counts','eyebrow':'03 / NEXT-ACTION REPLAY','title':'Valid JSON did not ensure the preferred next action','subtitle':'Two unresolved patterns in the saved-state replay · five states per case type','data':{'focusColumns':[0,3],'focusTitles':['Database diagnostic stayed unresolved','Successful-control choices regressed'],'compactLabels':['Original history','Budgeted raw history','Facts · time order','Facts · component order'],'denominator':5,'columns':['Database lock','Deployment','Truncation states','Successful controls'],'rows':[{'label':label,'note':note,'values':[strata[arm,kind]['categories'].get('preferred',0) for kind in kinds],'valid':arms[arm]['valid']} for arm,label,note in labels]},'caveats':['Development action scores, not live recovery rates. An unlisted or lower-scoring action can be a reasonable alternative; human review remains.','Only the two compact-fact arms match fact availability and near-exact length. Raw-history comparisons also change retained information.'],'sources':[source(V2/'analysis.json','V2 budget-controlled replay','replay-20261101-v2')],'caption':'No condition selected the preferred database-lock diagnostic on any of five saved states. On successful controls, preferred next-action scores fell from 5/5 with original history to 4/5, 4/5 and 3/5. Filled dots indicate preferred actions; open dots include other rubric outcomes, not necessarily incorrect actions. JSON validity is reported separately in the full results table.'}
 from explanatory import additions
 out={s['id']:validate(s) for s in (a,b,c)}
 out.update({k:validate(v) for k,v in additions(project,source).items()});return out
def all_specs():
 out=builtins()
 for p in sorted((DATA/'specs').glob('*.json')):
  s=validate(read(p));out[s['id']]=s
 return out
def get(identity):
 if not re.fullmatch('[a-z][a-z0-9-]{1,59}',identity):raise ValueError('Invalid figure id')
 return all_specs()[identity]
def put(s):
 s=validate(s)
 if s['id'] in builtins():raise ValueError('Built-in figures are source adapters; use a new id for a custom figure')
 p=DATA/'specs'/(s['id']+'.json');p.parent.mkdir(parents=True,exist_ok=True);temp=p.with_suffix('.tmp');temp.write_text(json.dumps(s,indent=2));temp.replace(p);return s

def dimensions(spec):return (1000,1050) if spec['kind']=='system_flow' else (800,1210) if spec['kind']=='evidence_sequence' else (800,1020) if spec['kind']=='token_budget' and 'responseText' in spec['data'] else (800,800) if spec['kind']=='paired_counts' else (800,850) if spec['kind']=='matrix_counts' and 'focusColumns' in spec['data'] else (1000,720)
