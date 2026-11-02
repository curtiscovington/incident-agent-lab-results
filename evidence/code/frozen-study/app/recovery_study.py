"""Frozen recovery studies. Trial truth never enters the agent prompt.

Persistent queue; immutable fixture archives; independent judge. This is not a
held-out evaluation or a reliability estimate. No arbitrary paths/actions in API.
"""
import copy, hashlib, json, pathlib, platform, threading, time, uuid
from fastapi import APIRouter, HTTPException
from . import recovery_design
from . import incident_agent as agent, plane, plane_releases as releases, plane_diagnosis as diagnosis

ROOT=pathlib.Path(__file__).resolve().parents[1]
DATA=ROOT/'.runtime/agent-recovery'
LOCK=threading.RLock()
WORKER=threading.Lock()
router=APIRouter()
HELPER='sha256:7efd45553a98be3d940b73600550669831c0be34f30c521159e7030a91fcdee6'
VOLUMES=tuple('bigspark-plane_'+n for n in ('pg-data','nats-1','nats-2','nats-3','prom-data'))
ORDER=('postgres','nats-1','nats-2','nats-3','api-1','api-2','scheduler-1','scheduler-2','worker-1','worker-2','worker-3','ingress','prometheus','control-client')
ACTIVE=('running','paused','interrupted','failed')

def state():return agent.read(DATA/'state.json',{'status':'idle','stage':'Not started','trials':[],'events':[],'completed':0})
def save(s):
    agent.atomic(DATA/'state.json',s)
    if s.get('id'):agent.atomic(DATA/s['id']/'state.json',s)
def update(stage=None,**values):
    with LOCK:
        s=state();s.update(values)
        if stage:
            s['stage']=stage;s['events']=(s['events']+[{'at':time.time(),'stage':stage}])[-100:]
        save(s)
    return s

def reserved():return state()['status'] in ACTIVE

def blocked(path):
    return reserved() and (path.startswith(('/api/system/resources/','/api/system/groups/','/api/system/releases/','/api/system/experiments','/api/system/private-access/')) or path=='/api/system/agent/control')

def agent_allowed():
    s=state()
    return not reserved() or (s['status']=='running' and s.get('stage')=='Agent investigating')

def initialize():
    if state()['status'] in ('running','paused'):
        update('Interrupted — resume saved queue or restore baseline',status='interrupted',error='Controller restarted. Started attempts will be retained as interrupted, never silently repeated.')
        agent.control({'action':'pause'})

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def containers():
    values=json.loads(releases.command(['docker','inspect',*['cp-'+n for n in ORDER]]))
    result={}
    for c in values:
        name=c['Name'].removeprefix('/cp-')
        if c['Config'].get('Labels',{}).get('com.docker.compose.project')!='bigspark-plane' or c['State'].get('Paused'):
            raise RuntimeError('Unexpected container ownership or paused state: '+name)
        config=copy.deepcopy(c['Config']);config.pop('Hostname',None) # Docker-generated identity changes on recreation.
        binds={}
        for m in c['Mounts']:
            if m['Type']=='bind':
                path=pathlib.Path(m['Source'])
                for f in ([path] if path.is_file() else sorted(path.rglob('*'))):
                    if f.is_file():binds[str(f)]=digest(f)
        result[name]={'image':c['Image'],'bindHashes':binds,'configuration':hashlib.sha256(json.dumps({'Config':config,'HostConfig':c['HostConfig']},sort_keys=True).encode()).hexdigest(),
                      'mounts':[{k:m.get(k) for k in ('Type','Name','Source','Destination','RW')} for m in c['Mounts']]}
    return result

def canonical_containers(values):
    values=copy.deepcopy(values)
    for c in values.values():c['mounts']=sorted(c.get('mounts',[]),key=lambda m:(m.get('Destination') or '',m.get('Source') or ''))
    return values

def volume(action,folder,name):
    if name not in VOLUMES or action not in ('capture','restore'):raise ValueError('Invalid fixture operation')
    info=json.loads(releases.command(['docker','volume','inspect',name]))[0]
    if info.get('Labels',{}).get('com.docker.compose.project')!='bigspark-plane':raise RuntimeError('Volume ownership mismatch')
    # Check every container, including stopped consumers, before touching data.
    consumers=releases.command(['docker','ps','-aq','--filter','volume='+name]).split()
    if consumers:
        for c in json.loads(releases.command(['docker','inspect',*consumers])):
            if c['Name'].removeprefix('/cp-') not in ORDER or c['State']['Running']:
                raise RuntimeError('Fixture volume has a running or external consumer')
    code="""import pathlib,tarfile,shutil,sys
root=pathlib.Path('/data');archive='/backup/'+sys.argv[2]+'.tar'
if sys.argv[1]=='capture':
 with tarfile.open(archive,'w') as t:
  for p in root.iterdir():t.add(p,arcname=p.name)
else:
 with tarfile.open(archive,'r') as t:
  members=t.getmembers()
  for m in members:
   if m.name.startswith('/') or '..' in pathlib.PurePosixPath(m.name).parts:raise ValueError('Invalid fixture path')
  for p in root.iterdir():
   if p.is_dir() and not p.is_symlink():shutil.rmtree(p)
   else:p.unlink()
  t.extractall(root,filter='fully_trusted')
"""
    releases.command(['docker','run','--rm','--network','none','--read-only','--mount',f'type=volume,src={name},dst=/data'+(',readonly' if action=='capture' else ''),'--mount',f'type=bind,src={folder},dst=/backup',HELPER,'python','-c',code,action,name],timeout=180)

def power_all(action):
    for n in (reversed(ORDER) if action=='stop' else ORDER):
        releases.command(['docker',action,*(['--time','10'] if action=='stop' else []),'cp-'+n],timeout=30)

def quiet_agent():
    agent.control({'action':'pause'})
    if not agent.STEP_LOCK.acquire(timeout=180):raise RuntimeError('Agent did not finish its in-flight action')
    agent.STEP_LOCK.release()

def action(kind,body=None):
    op=releases.begin_action('job-api',kind,body or {},actor='recovery-study')
    deadline=time.monotonic()+240
    while time.monotonic()<deadline:
        current=releases.snapshot()['operation']
        if current and current['id']==op['id'] and current['status']!='running':
            if current['status']!='completed':raise RuntimeError('Release action failed: '+json.dumps(current))
            return current
        time.sleep(.5)
    raise RuntimeError('Release action timed out; operator inspection required')

def ready():
    deadline=time.monotonic()+90;last=None
    while time.monotonic()<deadline:
        fresh=plane.collect()
        with plane.LOCK:plane.SNAPSHOT=fresh
        found={r['id']:r for r in fresh['resources'] if r['id'] in agent.SCOPE}
        healthy=len(found)==len(agent.SCOPE) and all(r['observed']=='running' and r['measured']['state']=='healthy' for r in found.values())
        if healthy:
            last=releases.business_check()
            if last['ok'] and not [a for a in agent.context()['alarms'] if a['severity']=='critical']:return {'resources':found,'job':last,'at':time.time()}
        time.sleep(2)
    raise RuntimeError('Baseline readiness failed: '+json.dumps(last))

def checkpoint():
    while True:
        s=state()
        if s.get('cancel'):raise InterruptedError('Operator ended study')
        if s['status']!='paused':return
        time.sleep(.3)

def reset(folder,manifest):
    quiet_agent()
    action('deploy',{'release':'r3'})
    diagnosis.reset()
    for name,sha in manifest['volumes'].items():
        if digest(folder/(name+'.tar'))!=sha:raise RuntimeError('Fixture checksum mismatch')
    current=containers()
    if canonical_containers(current)!=canonical_containers(manifest['containers']):raise RuntimeError('Container configuration drift; refusing fixture restore')
    if agent.model_digest()!=agent.read(folder.parent/'protocol.json',{}).get('modelDigest'):raise RuntimeError('Model digest drift')
    if not plane.RUN_LOCK.acquire(timeout=5):raise RuntimeError('Another operation owns the Control Plane')
    try:
        power_all('stop')
        for name in VOLUMES:volume('restore',folder,name)
        power_all('start')
    finally:plane.RUN_LOCK.release()
    with releases.LOCK:
        releases.COUNTS.clear()
    return ready()

def judge(run,baseline):
    # Record evidence before operator cleanup. The model's finish is not a score.
    observed={n:releases.container(n)['Image'] for n in releases.APPS}
    replay=[releases.business_check('incident:queue-lock','http://'+v['address']+':8080') for v in releases.APPS.values()]
    ingress=releases.business_check()
    fresh=plane.collect()
    unhealthy=[r['id'] for r in fresh['resources'] if r['id'] in agent.SCOPE and (r['observed']!='running' or r['measured']['state']!='healthy')]
    missing=sorted(set(agent.SCOPE)-{r['id'] for r in fresh['resources']})
    rollback=any(e.get('kind')=='decision' and e.get('tool')=='rollback' for e in run.get('events',[]))
    return {'agentClaim':run.get('status'),'rollbackSelected':rollback,'images':observed,'replay':replay,'ingress':ingress,'unhealthy':unhealthy,'missing':missing,
            'repairVerified':all(v==baseline for v in observed.values()) and all(x['ok'] for x in replay) and ingress['ok'] and not unhealthy and not missing,
            'diagnosisReview':'pending human review','limitation':'Immediate replay only; no long-duration recurrence or reliability claim.'}


def frozen_protocol(folder):
    protocol=agent.read(folder/'protocol.json',{})
    expected=(folder/'protocol.sha256').read_text().strip()
    if digest(folder/'protocol.json')!=expected:raise RuntimeError('Protocol checksum mismatch')
    for path,sha in protocol['sourceHashes'].items():
        if digest(ROOT/path)!=sha:raise RuntimeError('Frozen source drift: '+path)
    if agent.model_digest()!=protocol['modelDigest']:raise RuntimeError('Model digest drift')
    if diagnosis.catalog()!=protocol['workerImages']:raise RuntimeError('Worker image catalog drift')
    return protocol

def recover_ledger(folder,protocol):
    """Recover atomic results; retain any started-but-unscored attempt, no rerun."""
    trials=[]
    for index,entry in enumerate(protocol['queue'],1):
        resultfile=folder/f'{index}-result.json';started=folder/f'{index}-started.json'
        if resultfile.exists():
            trial=agent.read(resultfile,{})
            if trial.get('number')!=index or any(trial.get(k)!=entry[k] for k in ('scenario','condition')):raise RuntimeError('Trial ledger identity mismatch')
            trials.append(trial)
        elif started.exists():
            assignment=agent.read(started,{})
            current=agent.state().get('run') or {}
            belongs=current.get('experiment',{}).get('study')==folder.name and current.get('experiment',{}).get('trial')==index
            rawfile=folder/f'{index}-agent.json'
            if belongs and not rawfile.exists():agent.atomic(rawfile,current)
            raw=agent.read(rawfile,{})
            trial={'number':index,**entry,'scenarioName':diagnosis.SCENARIOS.get(entry['scenario'],'API deployment regression'),
                   'runId':raw.get('id'),'steps':raw.get('steps',0),'seconds':agent.elapsed(raw) if raw.get('startedAt') else 0,
                   'result':{'agentClaim':raw.get('status','not started'),'repairVerified':None,'outcome':'interrupted','diagnosisReview':'pending human review','reason':'Attempt began but no atomic scored result was committed before interruption. Preserved without replacement.'},'started':assignment}
            agent.atomic(resultfile,trial);trials.append(trial)
    # A hole would mean a corrupted ledger, not permission to reorder a queue.
    if [t['number'] for t in trials]!=list(range(1,len(trials)+1)):raise RuntimeError('Non-contiguous trial ledger')
    update(trials=trials,completed=len(trials))
    if protocol.get('suite')=='repeated':agent.atomic(folder/'summary.json',recovery_design.summary(trials))
    return trials

def run_study():
    with WORKER:
        s=state();folder=DATA/s['id'];fixture=folder/'fixture';fixture.mkdir(exist_ok=True)
        try:
            quiet_agent();protocol=frozen_protocol(folder)
            manifest=agent.read(fixture/'manifest.json',None)
            trials=recover_ledger(folder,protocol)
            if not manifest:
                if trials:raise RuntimeError('Frozen fixture missing for recorded attempts')
                power_all('start')
                update('Preparing healthy baseline')
                action('deploy',{'release':'r3'});diagnosis.reset();action('validate');ready();checkpoint()
                update('Freezing persistent data')
                if not plane.RUN_LOCK.acquire(timeout=5):raise RuntimeError('Control Plane is busy')
                try:
                    power_all('stop')
                    for name in VOLUMES:volume('capture',fixture,name)
                    manifest={'containers':containers(),'volumes':{n:digest(fixture/(n+'.tar')) for n in VOLUMES}}
                    agent.atomic(fixture/'manifest.json',manifest)
                    power_all('start')
                finally:plane.RUN_LOCK.release()
            manifest['sha256']=digest(fixture/'manifest.json');update(fixtureSha256=manifest['sha256'])
            for index,entry in enumerate(protocol['queue']):
                if index<len(trials):continue
                condition=entry['condition'];scenario=entry['scenario']
                if diagnosis.catalog()!=protocol['workerImages']:raise RuntimeError('Worker image catalog drift')
                for path,sha in protocol['sourceHashes'].items():
                    if digest(ROOT/path)!=sha:raise RuntimeError('Frozen source drift: '+path)
                checkpoint();update('Restoring frozen baseline',trial=index+1,condition=condition,scenario=scenario,scenarioName=diagnosis.SCENARIOS.get(scenario,'API deployment regression'))
                baseline=reset(fixture,manifest);agent.atomic(folder/f'{index+1}-baseline.json',baseline);checkpoint()
                if agent.model_digest()!=protocol['modelDigest']:raise RuntimeError('Model digest changed')
                observation_since=time.time()
                agent.atomic(folder/f'{index+1}-started.json',{'at':observation_since,**entry})
                if scenario=='api-regression':
                    update('Deploying faulty image');deployment=action('deploy',{'release':'r4'});checkpoint()
                    update('Injecting failing request');trigger=action('trigger')
                else:
                    update('Injecting '+diagnosis.SCENARIOS[scenario]);trigger=diagnosis.inject(scenario);deployment=trigger.get('deployment',trigger.get('policy'))
                agent.atomic(folder/f'{index+1}-injection.json',{'trigger':trigger,'deployment':deployment})
                checkpoint()
                update('Waiting for alarm');deadline=time.monotonic()+120
                while not any(a['severity']=='critical' for a in agent.context()['alarms']):
                    if time.monotonic()>deadline:raise RuntimeError('No alarm after fault injection')
                    checkpoint();time.sleep(1)
                with agent.LOCK:
                    a=agent.state();a.update(enabled=True,seen=None,run=None,experiment={'study':s['id'],'trial':index+1,'condition':condition,'observationSince':observation_since,'prompt':protocol['prompts'][condition]});agent.save(a)
                update('Agent investigating');run=None;claim_wait=0
                while True:
                    checkpoint()
                    with agent.LOCK:a=agent.state();run=copy.deepcopy(a['run'])
                    if run and run['status']!='running':break
                    if not run and claim_wait>90:raise RuntimeError('Agent did not claim the injected incident')
                    if run and agent.elapsed(run)>agent.MAX_SECONDS+10:
                        with agent.LOCK:
                            a=agent.state();a['run'].update(status='needs attention',phase='expired',finishedAt=time.time());agent.emit(a,'escalated','Study time budget reached.')
                    claim_wait+=.5;time.sleep(.5)
                quiet_agent();agent.atomic(folder/f'{index+1}-agent.json',run)
                checkpoint();update('Independent verification')
                result=judge(run,protocol['releases']['r3']['image']) if scenario=='api-regression' else diagnosis.judge(scenario,trigger,run)
                if protocol['suite']=='repeated':
                    result['withinBudget']=agent.elapsed(run)<=protocol['maxSeconds'] and run['steps']<=protocol['maxSteps']
                    result['behaviorVerified']=result['repairVerified']
                    result['repairVerified']=bool(result['repairVerified'] and result['withinBudget'])
                trial={'number':index+1,'block':entry.get('block'),'scenario':scenario,'scenarioName':diagnosis.SCENARIOS.get(scenario,'API deployment regression'),'condition':condition,'runId':run['id'],'steps':run['steps'],'seconds':agent.elapsed(run),'result':result,'deployment':deployment,'trigger':trigger}
                agent.atomic(folder/f'{index+1}-result.json',trial)
                with LOCK:
                    current=state();current['trials'].append(trial);current['completed']=len(current['trials']);save(current)
                if protocol['suite']=='repeated':agent.atomic(folder/'summary.json',recovery_design.summary(state()['trials']))
                update('Archiving result');checkpoint()
            update('Restoring baseline after study');verification=reset(fixture,manifest);agent.atomic(folder/'cleanup-verification.json',verification)
            with agent.LOCK:
                a=agent.state();a.pop('experiment',None);a['enabled']=False;agent.save(a)
            update('Complete — diagnosis review pending',status='complete',finishedAt=time.time())
        except Exception as exc:
            try:quiet_agent()
            except Exception:pass
            update('Stopped — baseline restoration required',status='interrupted' if isinstance(exc,InterruptedError) else 'failed',error=str(exc),finishedAt=time.time())

@router.get('/api/system/recovery-study')
def status():
    s=state()
    # Full traces remain on disk; small live status keeps canvas updates cheap.
    if s.get('suite')=='repeated':s['statistics']=recovery_design.summary(s['trials'])
    s['trials']=[{**{k:t[k] for k in ('number','condition','runId','steps','seconds','scenario','scenarioName') if k in t},'result':{k:t['result'].get(k) for k in ('agentClaim','repairVerified','diagnosisReview','outcome')}} for t in s['trials'][-16:]]
    return s

@router.post('/api/system/recovery-study/control')
def control(body:dict):
    mode=body.get('action')
    with LOCK:
        s=state()
        if mode=='start':
            if reserved() or WORKER.locked():raise HTTPException(409,'Finish or restore the existing study first')
            a=agent.public_state()
            if not a['connected'] or (a.get('run') or {}).get('status')=='running' or plane.RUN_LOCK.locked():raise HTTPException(409,'Agent unavailable or Control Plane busy')
            suite=body.get('suite','comparison')
            if suite not in ('comparison','diagnosis','operations','resilience','repeated'):raise HTTPException(400,'Unknown study suite')
            if suite=='diagnosis' and not diagnosis.catalog():raise HTTPException(409,'Worker releases are not installed')
            identity=uuid.uuid4().hex;folder=DATA/identity;folder.mkdir(parents=True)
            runbook=(ROOT/'experiments/agent-recovery/runbook.txt').read_text()
            files=[ROOT/'app'/n for n in ('incident_agent.py','recovery_study.py','plane.py','plane_releases.py','plane_diagnosis.py','main.py','recovery_design.py')]
            files+=list((ROOT/'control-plane').rglob('*.tf'))+list((ROOT/'control-plane/network-policy').glob('*.py'))+[ROOT/'control-plane/compose.json',ROOT/'control-plane/service.py']
            protocol={'purpose':'Two development trials; no reliability or causal runbook-effect claim','conditions':['current','runbook'],
                      'model':agent.MODEL,'modelDigest':agent.model_digest(),'options':agent.OPTIONS,'schema':agent.SCHEMA,'maxSteps':agent.MAX_STEPS,'maxSeconds':agent.MAX_SECONDS,
                      'prompts':{'current':agent.PROMPT,'runbook':agent.PROMPT+'\n\n'+runbook},'releases':releases.catalog()['releases'],
                      'sourceHashes':{str(p.relative_to(ROOT)):digest(p) for p in files},'hardware':platform.uname()._asdict(),
                      'fault':'r4 both API replicas; incident:queue-lock','judge':'After one agent attempt: replay original input on both replicas, ordinary ingress job, baseline image identities and scope health. Diagnosis review is human and pending.',
                      'limitations':['One trial per condition; fixed order; known development scenario','Live probes and background jobs introduce timing variation','Fixture captures existing data; wall clocks, container logs and process memory are not restored','No held-out test, long recurrence window, or reliability threshold'],
                      'approved':'User approved two exploratory trials and immediate replay plus normal jobs; diagnosis left to human review.'}
            protocol.update(suite=suite,workerImages=diagnosis.catalog(),networkPolicy='API namespace database /32 blackhole route, reconciled every 3 seconds while configured; restore validated no-blackhole baseline',queue=([{'scenario':'api-regression','condition':c} for c in protocol['conditions']] if suite=='comparison' else [{'scenario':case,'condition':'runbook'} for case in diagnosis.SUITES.get(suite,())]))
            if suite=='diagnosis':
                protocol.update(purpose='Two exploratory diagnosis integration attempts; no reliability estimate',conditions=['runbook'],fault='Worker envelope schema regression; API database route regression',judge='One attempt each. Require baseline worker images or restored routes, correct completion of the three tracked jobs, fresh scope health and a normal ingress job. Human diagnosis review pending.',alarmSetupDeadlineSeconds=120,trackedJobsPerScenario=3,approved='User approved one runbook-assisted attempt per scenario; fault removed and affected jobs correctly completed. Diagnosis review pending.')
            if suite=='operations':
                protocol.update(purpose='Two exploratory operational recovery attempts; no reliability estimate',conditions=['runbook'],fault='Stalled database maintenance transaction; stopped scheduler publishers',judge='One attempt each. Require maintenance lock removal and exact rejected-request replay, or both schedulers running and all three tracked jobs correct. Both require fresh scope health and a normal ingress job. Human diagnosis review pending.',alarmSetupDeadlineSeconds=120,trackedJobsPerScenario={'database-lock':0,'scheduler-outage':3},maintenanceLockExpiresSeconds=1800,approved='Operator initiated this exploratory suite through Run; diagnosis review pending.')
            if suite=='resilience':
                protocol.update(purpose='Three exploratory differential-diagnosis attempts; no reliability estimate',conditions=['runbook'],fault='Stale scheduler advisory lease; loss of two NATS members; loss of one worker',judge='One attempt each. Require injected fault removed, all three tracked jobs correct, fresh full-scope health and a normal ingress job. Record unchanged worker images as diagnostic context, not a success threshold. Human diagnosis review pending.',alarmSetupDeadlineSeconds=120,trackedJobsPerScenario=3,maintenanceLockExpiresSeconds=1800,approved='Operator initiated this exploratory suite through Run; diagnosis review pending.')
            if suite=='repeated':
                protocol.update(purpose='Approved 160-trial comparison of repeatability on known Lab faults',queue=recovery_design.queue(),conditions=['current','runbook'],randomizationSeed=recovery_design.SEED,blocks=10,
                    fault='All eight existing scenarios, ten repetitions per condition',judge='Existing scenario-specific independent repair verification: fault removed, affected work correct, full-scope health and normal ingress job; 24 steps / 600 seconds agent budget.',
                    uncertainty='Per-scenario/condition raw counts and descriptive 95% Wilson intervals. Conditional on evaluated trials and fixed Lab; no population independence or general reliability claim. No significance threshold or p-value stopping rule.',
                    interruptions='Retain started unscored attempts as interrupted with unknown repair outcome; no replacement. Show them separately and report missing-outcome bounds. Resume remaining queue from the frozen fixture.',
                    primaryComparison='Per-scenario current versus runbook recovery proportions; equal-scenario aggregate difference descriptive only. Prior pilots excluded. Human diagnosis review pending.',
                    limitations=['Known development faults; runbook tailored during prior development','Same fixture, fixed temperature zero and seed; repetitions are not independent real-world fault samples','Runtime timing varies; no healthy/no-fault controls or long recurrence test','No tuning or optional stopping after results'],
                    approved='User approved 160 trials: 8 scenarios x 10 repetitions x current/runbook; primary repair within existing budget; human diagnosis review pending.',alarmSetupDeadlineSeconds=120)
            for p in files:
                target=folder/'source'/p.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(p.read_bytes())
            agent.atomic(folder/'protocol.json',protocol);sha=digest(folder/'protocol.json');(folder/'protocol.sha256').write_text(sha+'\n')
            save({'id':identity,'status':'running','stage':'Queued','trials':[],'events':[],'completed':0,'total':len(protocol['queue']),'suite':suite,'startedAt':time.time(),'protocolSha256':sha})
            threading.Thread(target=run_study,daemon=True).start()
        elif mode=='resume' and s['status']=='interrupted' and s.get('suite')=='repeated':
            if WORKER.locked():raise HTTPException(409,'Runner is still stopping')
            frozen_protocol(DATA/s['id'])
            s.update(status='running',stage='Resuming saved queue',error=None,cancel=False);save(s)
            threading.Thread(target=run_study,daemon=True).start()
        elif mode in ('pause','resume'):

            if s['status'] not in ('running','paused'):raise HTTPException(409,'No resumable live study')
            with agent.LOCK:
                a=agent.state();r=a.get('run')
                if r and r['status']=='running':
                    if mode=='pause':r.setdefault('pauseStarted',time.time())
                    elif r.get('pauseStarted'):r['pausedSeconds']=r.get('pausedSeconds',0)+time.time()-r.pop('pauseStarted')
                    agent.save(a)
            s['status']='paused' if mode=='pause' else 'running';save(s)
        elif mode=='cancel':
            if s['status'] not in ('running','paused'):raise HTTPException(409,'No active study')
            s.update(cancel=True,status='running');save(s)
        elif mode=='restore':
            if s['status'] not in ('failed','interrupted') or WORKER.locked():raise HTTPException(409,'Restore is available after the runner stops')
            def cleanup():
                with WORKER:
                    try:
                        quiet_agent()
                        folder=DATA/s['id'];fixture=folder/'fixture';manifest=agent.read(fixture/'manifest.json',None)
                        agent.atomic(folder/'cleanup-prior-state.json',s)
                        if manifest:verification=reset(fixture,manifest)
                        else:
                            action('deploy',{'release':'r3'});power_all('start');action('validate');verification=ready()
                        agent.atomic(folder/'cleanup-verification.json',verification)
                        with agent.LOCK:
                            a=agent.state();a.pop('experiment',None);a['enabled']=False;agent.save(a)
                        complete=s.get('completed')==s.get('total')
                        update('Complete — diagnosis review pending' if complete else 'Ended — healthy baseline restored',status='complete' if complete else 'cancelled',error=None,finishedAt=time.time())
                    except Exception as exc:update('Restoration failed',status='failed',error=str(exc))
            s.update(status='running',stage='Restoring healthy baseline');save(s);threading.Thread(target=cleanup,daemon=True).start()
        else:raise HTTPException(400,'Unknown study action')
    return status()
