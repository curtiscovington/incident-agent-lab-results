"""Local deployment adapter: Terraform owns resources; the adapter owns release intent.

No arbitrary Docker commands, image names, paths or Terraform arguments enter via API.
Observation is independent of deployments. All actions and incident transitions persist.
"""
import concurrent.futures
import copy
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import urllib.request
import uuid
from fastapi import APIRouter, HTTPException

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / '.runtime/control-plane-releases'
WORKSPACE = ROOT / 'control-plane/releases'
TF = ROOT / '.runtime/bin/terraform'
APPS = {name: {'name':name, 'address':'10.78.40.'+str(21+i)} for i,name in enumerate(('api-1','api-2'))}
SERVICE = {}
LOCK = threading.RLock()
ACTION_LOCK = threading.Lock()
OBSERVATIONS = {}
COUNTS = {}
router = APIRouter()


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    with tmp.open('w') as f:
        json.dump(value, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def read(path, default):
    return json.loads(path.read_text()) if path.exists() else copy.deepcopy(default)


def state():
    return read(DATA/'state.json', {'schemaVersion': 1, 'desired': {}, 'knownGood': {}, 'incidents': [], 'operation': None})


def catalog():
    return read(DATA/'releases.json', {'releases': {}})


def event(kind, **details):
    DATA.mkdir(parents=True, exist_ok=True)
    with (DATA/'events.jsonl').open('a') as f:
        f.write(json.dumps({'at': time.time(), 'kind': kind, **details})+'\n')
        f.flush()
        os.fsync(f.fileno())


def command(args, timeout=15):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout)[-1200:])
    return result.stdout


def container(app):
    ids = command(['docker', 'ps', '-aq', '--filter', 'name=^/cp-'+app+'$']).split()
    if not ids:
        return None
    c = json.loads(command(['docker', 'inspect', ids[0]]))[0]
    if c['Config'].get('Labels', {}).get('com.docker.compose.project') != 'bigspark-plane' or c['Config']['Labels'].get('lab.role') != 'api':
        raise RuntimeError('Container ownership mismatch')
    return c


def http(url, data=None, key=None, timeout=2):
    headers={'Content-Type':'application/json'}
    if key:headers['Idempotency-Key']=key
    req=urllib.request.Request(url,data=json.dumps(data).encode() if data is not None else None,headers=headers)
    with urllib.request.urlopen(req,timeout=timeout) as r:return json.load(r)


def probe(app, path='/health'):
    started=time.monotonic()
    try:
        body=http('http://'+APPS[app]['address']+':8080'+path)
        valid=body.get('healthy') is True and body.get('name')==app if path=='/health' else 'jobs' in body
        return {'ok':valid,'body':body,'at':time.time(),'latencyMs':round((time.monotonic()-started)*1000,1),'path':path}
    except Exception as exc:
        return {'ok':False,'error':type(exc).__name__,'at':time.time(),'path':path}


def business_check(payload='control-plane availability canary', base='http://10.78.10.10:8080'):
    import hashlib
    result={'at':time.time(),'endpoint':base,'payload':payload,'ok':False,'key':'lab-canary-'+uuid.uuid4().hex}
    try:
        accepted=http(base+'/jobs',{'payload':payload},key=result['key'])
        result['accepted']=accepted
        duplicate=http(base+'/jobs',{'payload':payload},key=result['key'])
        if accepted.get('id') != duplicate.get('id'):raise RuntimeError('Idempotency mismatch')
        deadline=time.monotonic()+12
        while time.monotonic()<deadline:
            job=http(base+'/jobs/'+accepted['id']);result['job']=job
            if job['state'] in ('completed','failed'):break
            time.sleep(.3)
        expected=hashlib.pbkdf2_hmac('sha256',payload.encode(),b'control-plane-v1',40000).hex()
        result['ok']=job['state']=='completed' and job['result']==expected
    except Exception as exc:result['error']=type(exc).__name__+': '+str(exc)[:180]
    result['finishedAt']=time.time()
    return result


def observe(app):
    c=container(app)
    result={'id':app,'app':app,**APPS[app],'capturedAt':time.time(),'observed':'absent','state':'not deployed','interfaces':[],'image':None}
    if c:
        result.update(containerId=c['Id'],observed='paused' if c['State'].get('Paused') else c['State']['Status'],image=c['Image'],release=c['Config']['Labels'].get('lab.release','legacy'),startedAt=c['State']['StartedAt'],dockerHealth=c['State'].get('Health',{}).get('Status'),restarts=c['RestartCount'],interfaces=[{'network':n,'address':i['IPAddress'],'prefix':i['IPPrefixLen']} for n,i in c['NetworkSettings']['Networks'].items()])
        health=probe(app);summary=probe(app,'/summary')
        result.update(health=health,business=summary,state='healthy' if result['observed']=='running' and health['ok'] and summary['ok'] else 'unhealthy')
    return result


def record_observation(app, observation):
    with LOCK:
        OBSERVATIONS[app] = observation
        s = state()
        if app not in s['desired'] and app != 'control-plane':
            return
        good = observation['state'] == 'healthy'
        last, count = COUNTS.get(app, (None, 0))
        COUNTS[app] = (good, count+1 if last == good else 1)
        active = next((i for i in s['incidents'] if i['app']==app and not i.get('resolvedAt')), None)
        if COUNTS[app][1] < (1 if app == 'control-plane' else 2):
            return
        if not good and not active:
            active = {'id': uuid.uuid4().hex, 'app': app, 'openedAt': time.time(), 'release': observation.get('release'),
                      'image': observation.get('image'), 'reason': 'Two consecutive failed API probes' if app != 'control-plane' else 'Job submission or completion through ingress failed', 'resolvedAt': None}
            active['observation'] = copy.deepcopy(observation)
            try:
                active['logs'] = logs(app)['text'] if app in APPS else {name:logs(name)['text'] for name in APPS}
            except Exception as exc:
                active['logsError'] = type(exc).__name__
            s['incidents'].append(active)
            event('alarm.opened', incident=active)
            atomic(DATA/'state.json', s)
        elif good and active:
            active['resolvedAt'] = time.time()
            active['recoveredImage'] = observation.get('image')
            event('alarm.resolved', incident=active)
            atomic(DATA/'state.json', s)


def monitor(stop):
    global SERVICE
    last_canary = 0
    with LOCK:
        s = state()
        if s['operation'] and s['operation']['status']=='running':
            s['operation'].update(status='interrupted', finishedAt=time.time(), error='Controller restarted; inspect Terraform state before retrying.')
            atomic(DATA/'state.json', s)
            atomic(DATA/'operations'/f"{s['operation']['id']}.json", s['operation'])
            event('operation.interrupted', operation=s['operation'])
    while not stop.is_set():
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = {app: pool.submit(observe, app) for app in APPS}
            for app, future in futures.items():
                try:
                    record_observation(app, future.result())
                except Exception as exc:
                    with LOCK:
                        COUNTS.pop(app, None)
                        previous = OBSERVATIONS.get(app, {'id': 'workload-'+app, 'app': app, **APPS[app]})
                        OBSERVATIONS[app] = {**previous, 'collectionError': str(exc)[:300], 'state': 'unknown'}
        if time.monotonic()-last_canary >= 15:
            check=business_check()
            with LOCK:
                SERVICE=check
                atomic(DATA/'business.json', check)
                event('service.probe', observation=check)
            record_observation('control-plane',{'app':'control-plane','state':'healthy' if check['ok'] else 'unhealthy','capturedAt':check['finishedAt'],'business':check})
            last_canary=time.monotonic()
        stop.wait(3)


def snapshot():
    with LOCK:
        s = state()
        result = []
        for app, config in APPS.items():
            r = copy.deepcopy(OBSERVATIONS.get(app, {'id':app, 'app': app, **config, 'state': 'unknown'}))
            r.update(desired=s['desired'].get(app), knownGood=s['knownGood'].get(app))
            r['stale'] = bool(r.get('collectionError')) or time.time()-r.get('capturedAt', 0)>20
            if r['stale']:
                r['state'] = 'unknown'
            r['drift'] = bool(r.get('desired') and r.get('image') != r['desired']['image'])
            result.append(r)
        alarms = [{'id':'release:'+i['id'], 'severity':'critical', 'resource':APPS.get(i['app'],{'name':'Control Plane service'})['name'], 'detail':i['reason'], 'source':'Control Plane: API probes and real job completion', 'incidentId':i['id']} for i in s['incidents'] if not i.get('resolvedAt')]
        alarms += [{'id':'release-stale:'+r['app'], 'severity':'warning', 'resource':r['name'], 'detail':'API telemetry stale or unavailable'} for r in result if r['stale'] and r.get('desired')]
        alarms += [{'id':'release-drift:'+r['app'], 'severity':'warning', 'resource':r['name'], 'detail':'Observed image differs from desired release'} for r in result if r['drift'] and not r['stale']]
        if not SERVICE or time.time()-SERVICE.get('finishedAt',0)>45:
            alarms.append({'id':'service-telemetry','severity':'warning','resource':'Control Plane service','detail':'End-to-end job observations unavailable or stale'})
        return {'schemaVersion':1, 'adapter':'Control Plane Job API releases · Terraform', 'apps':result, 'alarms':alarms,
                'business':{**SERVICE,'stale':not SERVICE or time.time()-SERVICE.get('finishedAt',0)>45}, 'operation':s['operation'], 'incidents':s['incidents'][-30:], 'releases':catalog()['releases'],
                'available':catalog().get('available',list(catalog()['releases'])), 'baseline':catalog().get('baseline'), 'candidate':catalog().get('candidate'), 'agentMode':'observe only', 'rule':'Two failed API samples raise an alarm. An ingress job failure raises a service alarm. Successful fresh checks resolve alarms; stale observations are unknown.'}


def terraform_apply(desired, operation):
    variables = {'apps': desired}
    atomic(WORKSPACE/'deployment.auto.tfvars.json', variables)
    plan = DATA/(operation['id']+'.tfplan')
    output = command([str(TF), '-chdir='+str(WORKSPACE), 'plan', '-no-color', '-input=false', '-out='+str(plan)], timeout=120)
    # Save sanitized inventory, including intended resources before deployment.
    from .terraform_inventory import normalize
    document = json.loads(command([str(TF), '-chdir='+str(WORKSPACE), 'show', '-json', str(plan)], timeout=30))
    allowed={f'docker_container.api["{app}"]' for app in APPS}
    if any(r['address'] not in allowed for r in document.get('resource_changes',[])):
        raise RuntimeError('Plan contains resources outside the API release boundary')
    atomic(DATA/'inventory.json', normalize(document))
    output += command([str(TF), '-chdir='+str(WORKSPACE), 'apply', '-no-color', '-input=false', str(plan)], timeout=180)
    (DATA/'operations'/ (operation['id']+'.log')).write_text(output)
    document = json.loads(command([str(TF), '-chdir='+str(WORKSPACE), 'show', '-json'], timeout=30))
    atomic(DATA/'inventory.json', normalize(document))


def run_action(operation):
    from . import plane
    targets=list(APPS) if operation['app']=='job-api' else [operation['app']]
    action=operation['action']
    try:
        operation['before']={}
        for app in targets:
            c=container(app)
            operation['before'][app]={'image':c['Image'],'logs':logs(app)['text']} if c else {'observed':'absent'}
        with LOCK:atomic(DATA/'operations'/(operation['id']+'.json'),operation)
        if action in ('deploy','rollback'):
            with LOCK:
                s=state()
                for app in targets:
                    release=operation.get('release') if action=='deploy' else s['knownGood'].get(app,{}).get('release')
                    if release not in catalog()['releases']:raise RuntimeError('No validated rollback release')
                    target={'release':release,'image':catalog()['releases'][release]['image']}
                    if action=='rollback' and target['image']!=s['knownGood'][app]['image']:raise RuntimeError('Rollback image catalog mismatch')
                    s['desired'][app]=target
                atomic(DATA/'state.json',s);desired=copy.deepcopy(s['desired'])
            terraform_apply(desired,operation)
            operation['result']={'note':'API release applied; health and job completion are measured separately.'}
        elif action=='restart':
            for app in targets:command(['docker','restart','-t','1',container(app)['Id']],timeout=20)
            operation['result']={'note':'API processes restarted; release unchanged.'}
        elif action=='trigger':
            results={}
            for app in targets:
                try:results[app]=http('http://'+APPS[app]['address']+':8080/jobs',{'payload':'incident:queue-lock'},key='lab-trigger-'+uuid.uuid4().hex)
                except Exception as exc:results[app]={'error':type(exc).__name__}
            operation['result']={'requests':results,'note':'Sent a real job request to the selected API instances. Check service alarms and ingress job completion.'}
        elif action=='validate':
            with LOCK:desired=state()['desired']
            before={app:container(app) for app in targets}
            for app in targets:
                if before[app]['Image']!=desired[app]['image']:raise RuntimeError('Observed image differs from desired release')
                deadline=time.monotonic()+20
                while not probe(app)['ok']:
                    if time.monotonic()>deadline:raise RuntimeError('API did not become ready')
                    time.sleep(1)
            checks=[];operation['result']={'checks':checks}
            for app in targets:
                checks.append(business_check('incident:queue-lock','http://'+APPS[app]['address']+':8080'))
            if not all(c['ok'] for c in checks):raise RuntimeError('Direct API job check failed; baseline not promoted')
            # Nginx keeps failed upstreams out of rotation for fail_timeout=5s.
            # Preserve every convergence attempt rather than treating apply as ready.
            ingress_attempts=[];operation['result']['ingressReadiness']=ingress_attempts
            deadline=time.monotonic()+15;successes=0
            while time.monotonic()<deadline:
                check=business_check();ingress_attempts.append(check)
                successes=successes+1 if check['ok'] else 0
                if successes==2:break
                time.sleep(1)
            if successes<2:raise RuntimeError('Ingress did not complete two consecutive jobs within readiness window; baseline not promoted')
            checks.extend(ingress_attempts[-2:])
            for app in targets:
                after=container(app)
                if after['Id']!=before[app]['Id'] or after['Image']!=desired[app]['image']:raise RuntimeError('API changed during validation')
            with LOCK:
                s=state()
                for app in targets:s['knownGood'][app]={**desired[app],'validatedAt':time.time(),'checks':checks}
                atomic(DATA/'state.json',s)
            operation['result']['note']='Real jobs completed with correct results, including the triggering payload. Known-good API images recorded.'
        operation['status']='completed'
    except Exception as exc:operation.update(status='failed',error=str(exc)[:1500])
    finally:
        operation['finishedAt']=time.time()
        with LOCK:
            s=state();s['operation']=operation;atomic(DATA/'state.json',s)
            atomic(DATA/'operations'/(operation['id']+'.json'),operation)
            event('operation.'+operation['status'],operation=operation)
        ACTION_LOCK.release()
        plane.RUN_LOCK.release()


@router.get('/api/system/releases')
def get_releases():
    return snapshot()


@router.get('/api/system/releases/inventory')
def get_inventory():
    return read(DATA/'inventory.json', {'resources':[], 'source':'Not deployed'})


@router.get('/api/system/releases/{app}/logs')
def logs(app: str):
    if app not in APPS:
        raise HTTPException(404, 'Unknown API component')
    c = container(app)
    if not c:
        return {'text':'Not deployed'}
    # Docker emits application logs to stdout or stderr. Neither is an error here.
    p = subprocess.run(['docker','logs','--tail','60',c['Id']], capture_output=True, text=True, timeout=5)
    if p.returncode:
        raise HTTPException(502, 'Container logs unavailable')
    return {'text':(p.stdout+p.stderr)[-12000:]}


@router.post('/api/system/releases/{app}/{action}')
def act(app: str, action: str, body: dict):
    return begin_action(app,action,body)

def begin_action(app: str, action: str, body: dict, actor='operator'):
    if app not in [*APPS,'job-api'] or action not in ('deploy','rollback','restart','trigger','validate'):
        raise HTTPException(404, 'Unknown API component or action')
    if action == 'deploy' and body.get('release') not in catalog()['releases']:
        raise HTTPException(400, 'Choose a built release from the image catalog')
    from . import plane
    if not plane.RUN_LOCK.acquire(blocking=False):raise HTTPException(409,'A Control Plane experiment or instance action is running')
    if not ACTION_LOCK.acquire(blocking=False):
        plane.RUN_LOCK.release()
        raise HTTPException(409, 'An API release operation is already running')
    operation = {'id':uuid.uuid4().hex, 'app':app, 'action':action, 'release':body.get('release') if action=='deploy' else None,
                 'actor':actor, 'startedAt':time.time(), 'status':'running'}
    try:
        with LOCK:
            s = state()
            s['operation'] = operation
            atomic(DATA/'state.json', s)
            atomic(DATA/'operations'/(operation['id']+'.json'), operation)
            event('operation.started', operation=operation)
        threading.Thread(target=run_action, args=(copy.deepcopy(operation),), daemon=True).start()
    except Exception:
        ACTION_LOCK.release()
        plane.RUN_LOCK.release()
        raise
    return operation
