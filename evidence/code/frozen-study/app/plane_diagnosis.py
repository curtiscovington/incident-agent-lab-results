"""Worker release and API/database route scenarios, with allowlisted operations."""
import concurrent.futures,copy,hashlib,json,pathlib,subprocess,threading,time,uuid
from fastapi import APIRouter,HTTPException
from . import plane_releases as releases
ROOT=pathlib.Path(__file__).resolve().parents[1];DATA=ROOT/'.runtime/plane-diagnosis'
WORKERS=('worker-1','worker-2','worker-3');SOURCES=('api-1','api-2','worker-1','scheduler-1')
LOCK=threading.RLock();OBSERVE_LOCK=threading.Lock();OBSERVATION={};router=APIRouter()
SCENARIOS={'worker-backlog':'Worker message-format regression','database-route':'API–database route regression','database-lock':'Stalled database maintenance','scheduler-outage':'Scheduler publisher outage','scheduler-lease':'Stuck scheduler leadership','broker-quorum':'Message-broker quorum loss','worker-loss':'Single worker loss'}
SUITES={'diagnosis':('worker-backlog','database-route'),'operations':('database-lock','scheduler-outage'),'resilience':('scheduler-lease','broker-quorum','worker-loss')}

def state():return releases.read(DATA/'state.json',{'workerRelease':'w1','networkDenied':False,'events':[]})
def save(s):releases.atomic(DATA/'state.json',s)
def catalog():return releases.read(DATA/'images.json',{})
def inspect(name):
    if name not in WORKERS+SOURCES:raise HTTPException(403,'Resource outside diagnosis scope')
    c=json.loads(releases.command(['docker','inspect','cp-'+name]))[0]
    if c['Config'].get('Labels',{}).get('com.docker.compose.project')!='bigspark-plane':raise RuntimeError('Container ownership mismatch')
    return c

def terraform(workspace,allowed):
    folder=ROOT/'control-plane'/workspace;record=DATA/'operations'/uuid.uuid4().hex;record.mkdir(parents=True)
    plan=record/'plan';output=releases.command([str(releases.TF),'-chdir='+str(folder),'plan','-input=false','-no-color','-out='+str(plan)],timeout=120)
    doc=json.loads(releases.command([str(releases.TF),'-chdir='+str(folder),'show','-json',str(plan)],timeout=30))
    if any(c['address'] not in allowed for c in doc.get('resource_changes',[])):raise RuntimeError('Unexpected Terraform scope')
    (record/'plan.txt').write_text(output)
    try:output=releases.command([str(releases.TF),'-chdir='+str(folder),'apply','-input=false','-no-color',str(plan)],timeout=180)
    except Exception as exc:(record/'error.txt').write_text(str(exc));raise
    (record/'apply.txt').write_text(output)
    from .terraform_inventory import normalize
    doc=json.loads(releases.command([str(releases.TF),'-chdir='+str(folder),'show','-json'],timeout=30))
    releases.atomic(DATA/(workspace+'-inventory.json'),normalize(doc))
    return {'workspace':workspace,'evidence':str(record.relative_to(ROOT)),'at':time.time()}

def deploy_workers(release,actor='operator'):
    from . import plane
    if release not in ('w1','w2') or release not in catalog():raise HTTPException(400,'Unknown worker release')
    if not plane.RUN_LOCK.acquire(blocking=False):raise HTTPException(409,'Control Plane busy')
    try:
        config={'apps':{n:{'image':catalog()[release]['image'],'release':release} for n in WORKERS}}
        releases.atomic(ROOT/'control-plane/workers/deployment.auto.tfvars.json',config)
        result=terraform('workers',{'docker_container.worker["'+n+'"]' for n in WORKERS})
        with LOCK:
            s=state();s['workerRelease']=release;s['events'].append({'kind':'worker deployment','release':release,'actor':actor,**result});save(s)
        return {**result,'release':release,'image':catalog()[release]['image']}
    finally:plane.RUN_LOCK.release()

def set_network(denied,actor='operator'):
    from . import plane
    if type(denied) is not bool:raise HTTPException(400,'Expected a policy boolean')
    if not plane.RUN_LOCK.acquire(blocking=False):raise HTTPException(409,'Control Plane busy')
    try:
        identities={n:inspect(n)['Id'] for n in ('api-1','api-2')}
        releases.atomic(ROOT/'control-plane/network-policy/policy.auto.tfvars.json',{'denied':denied,'api_ids':identities})
        result=terraform('network-policy',{'terraform_data.database_route'})
        actual=network_policy()
        if any(v.get('blocked')!=denied for v in actual['observed'].values()):
            # Terraform's helper completion is not proof of current kernel state.
            raise RuntimeError('Observed route differs from applied policy')
        with LOCK:
            s=state();s['networkDenied']=denied;s['events'].append({'kind':'network policy','denied':denied,'actor':actor,**result});save(s)
        return {**result,'observed':actual['observed']}
    finally:plane.RUN_LOCK.release()

def monitor(stop):
    # A deployed network policy survives process restarts. This reconciler only
    # reapplies the configured denial; it never chooses a repair for the agent.
    from . import plane
    import importlib.util
    spec=importlib.util.spec_from_file_location('lab_route_apply',ROOT/'control-plane/network-policy/apply.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    while not stop.wait(3):
        if not state()['networkDenied'] or not plane.RUN_LOCK.acquire(blocking=False):continue
        try:
            if not state()['networkDenied']:continue
            policy=network_policy()
            if any(v.get('blocked') is False for v in policy['observed'].values()):
                module.apply('deny',{n:inspect(n)['Id'] for n in ('api-1','api-2')})
        except Exception:pass # Runtime observations expose unavailable namespaces.
        finally:plane.RUN_LOCK.release()

def network_policy():
    script=(ROOT/'control-plane/network-policy/route.py').read_text();observed={}
    for n in ('api-1','api-2'):
        try:observed[n]=json.loads(releases.command(['docker','exec',inspect(n)['Id'],'python','-c',script,'inspect'],timeout=5))
        except Exception as exc:observed[n]={'error':str(exc)[:300],'blocked':None}
    return {'destination':'10.78.50.11/32','scope':['api-1','api-2'],'observed':observed,'validatedBaseline':'No blackhole route to PostgreSQL','changes':[e for e in state()['events'][-8:] if e['kind']=='network policy']}

def connectivity(target='control-plane'):
    names=SOURCES if target=='control-plane' else ('api-1','api-2') if target=='job-api' else (target,)
    if any(n not in SOURCES+WORKERS for n in names):raise HTTPException(400,'Choose an API, scheduler, worker or control-plane')
    code="import socket,json,time;t=time.monotonic()\ntry:\n s=socket.create_connection(('10.78.50.11',5432),2);s.close();print(json.dumps({'reachable':True,'milliseconds':round((time.monotonic()-t)*1000,1)}))\nexcept OSError as e:print(json.dumps({'reachable':False,'error':str(e),'milliseconds':round((time.monotonic()-t)*1000,1)}))"
    def probe(n):
        try:return {'source':n,'destination':'postgres','port':5432,**json.loads(releases.command(['docker','exec',inspect(n)['Id'],'python','-c',code],timeout=5))}
        except Exception as exc:return {'source':n,'destination':'postgres','port':5432,'reachable':None,'error':str(exc)[:300]}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(probe,names))
    return {'at':time.time(),'checks':results,'source':'TCP connect from actual component network namespaces'}

def worker_releases():
    items=[]
    for n in WORKERS:
        c=inspect(n);items.append({'app':n,'image':c['Image'],'release':c['Config'].get('Labels',{}).get('lab.release','legacy'),'knownGood':{'release':'w1','image':catalog().get('w1',{}).get('image')}})
    return items

def observe(summary):
    global OBSERVATION
    if not catalog():return {}
    if time.time()-OBSERVATION.get('at',0)<5 or not OBSERVE_LOCK.acquire(blocking=False):return copy.deepcopy(OBSERVATION)
    try:
        jobs=summary.get('jobs',{});oldest=None
        try:
            oldest=float(releases.command(['docker','exec','cp-postgres','psql','-U','plane','-d','plane','-At','-c',"SELECT COALESCE(max(extract(epoch FROM clock_timestamp()-created_at)),0) FROM jobs WHERE state IN ('queued','running')"],timeout=5).strip())
        except Exception:pass
        try:database=database_activity()
        except Exception as exc:database={'unavailable':True,'error':str(exc)[:300]}
        OBSERVATION={'at':time.time(),'queue':{'queued':jobs.get('queued',0),'running':jobs.get('running',0),'failed':jobs.get('failed',0),'outboxPending':summary.get('outbox_pending'),'oldestPendingSeconds':oldest,'unavailable':summary.get('unavailable',False)},'connectivity':connectivity(),'workerReleases':worker_releases(),'database':database}
        releases.atomic(DATA/'observations.json',OBSERVATION)
        return copy.deepcopy(OBSERVATION)
    finally:OBSERVE_LOCK.release()

def submit_jobs(count=3):
    jobs=[]
    for i in range(count):
        payload='recovery-diagnosis-'+uuid.uuid4().hex
        accepted=releases.http('http://10.78.10.10:8080/jobs',{'payload':payload},key=uuid.uuid4().hex)
        jobs.append({'id':accepted['id'],'payload':payload,'accepted':accepted})
    return jobs

def check_jobs(jobs):
    results=[]
    for job in jobs:
        try:
            row=releases.http('http://10.78.10.10:8080/jobs/'+job['id'])
            expected=hashlib.pbkdf2_hmac('sha256',job['payload'].encode(),b'control-plane-v1',40000).hex()
            results.append({'id':job['id'],'ok':row.get('state')=='completed' and row.get('result')==expected,'observed':row})
        except Exception as exc:results.append({'id':job['id'],'ok':False,'error':str(exc)[:300]})
    return results


def database_sql(sql):
    # Only broker-owned constant statements reach this helper, never model SQL.
    c=json.loads(releases.command(['docker','inspect','cp-postgres']))[0]
    if c['Config'].get('Labels',{}).get('com.docker.compose.project')!='bigspark-plane':raise RuntimeError('Database ownership mismatch')
    return releases.command(['docker','exec',c['Id'],'psql','-X','-v','ON_ERROR_STOP=1','-U','plane','-d','plane','-At','-c',sql],timeout=8).strip()

BLOCKERS_SQL="""SELECT a.pid FROM pg_stat_activity a JOIN pg_locks l ON l.pid=a.pid
WHERE a.datname=current_database() AND a.application_name='lab-maintenance'
AND l.relation='public.jobs'::regclass AND l.mode='ShareLock' AND l.granted"""

LEASE_SQL="""SELECT a.pid FROM pg_stat_activity a JOIN pg_locks l ON l.pid=a.pid
WHERE a.datname=current_database() AND a.application_name='lab-leadership-maintenance'
AND l.locktype='advisory' AND l.classid=0 AND l.objid=78001 AND l.objsubid=1 AND l.granted"""

def lease_holders():
    return json.loads(database_sql("SELECT COALESCE(json_agg(pid),'[]') FROM ("+LEASE_SQL+") x"))

def release_scheduler_lease(actor='operator'):
    from . import plane
    if not plane.RUN_LOCK.acquire(blocking=False):raise HTTPException(409,'Control Plane busy')
    try:
        result=database_sql("SELECT COALESCE(json_agg(x),'[]') FROM (SELECT pid,pg_terminate_backend(pid) AS terminated FROM ("+LEASE_SQL+") holders) x")
        return {'terminated':json.loads(result),'actor':actor,'remainingHolders':lease_holders()}
    finally:plane.RUN_LOCK.release()

def hold_scheduler_lease():
    from . import incident_agent as agent,plane
    agent.power('schedulers','stop')
    try:
        if not plane.RUN_LOCK.acquire(blocking=False):raise HTTPException(409,'Control Plane busy')
        try:
            database_sql('SELECT 1')
            releases.command(['docker','exec','-d','-e','PGAPPNAME=lab-leadership-maintenance','cp-postgres','psql','-X','-v','ON_ERROR_STOP=1','-U','plane','-d','plane','-c',
                "SET lock_timeout='5s'; SET statement_timeout='31min'; SELECT pg_advisory_lock(78001); SELECT pg_sleep(1800);"],timeout=8)
            deadline=time.monotonic()+8
            while not lease_holders():
                if time.monotonic()>deadline:raise RuntimeError('Leadership lock not acquired')
                time.sleep(.2)
        finally:plane.RUN_LOCK.release()
    finally:agent.power('schedulers','start')
    return database_activity()

def runtime_status(names):
    if any(n not in WORKERS+('nats-1','nats-2','nats-3') for n in names):raise HTTPException(403,'Resource outside diagnostic runtime scope')
    result=[]
    for name in names:
        c=json.loads(releases.command(['docker','inspect','cp-'+name]))[0]
        if c['Config'].get('Labels',{}).get('com.docker.compose.project')!='bigspark-plane':raise RuntimeError('Container ownership mismatch')
        result.append({'id':name,'status':c['State']['Status'],'image':c['Image']})
    return result

def messaging_status():
    def inspect_member(number):
        name='nats-'+str(number)
        try:
            doc=releases.http('http://10.78.30.'+str(10+number)+':8222/jsz?streams=true&consumers=true&config=true',timeout=2)
            streams=[{'name':stream['name'],'cluster':stream.get('cluster'),'replicas':stream.get('config',{}).get('num_replicas'),'state':stream.get('state'),'consumers':[{'name':c['name'],'pending':c.get('num_pending'),'ackPending':c.get('num_ack_pending'),'cluster':c.get('cluster')} for c in stream.get('consumer_detail',[])]} for account in doc.get('account_details',[]) for stream in account.get('stream_detail',[]) if stream['name']=='JOBS']
            return {'id':name,'reachable':True,'metaCluster':doc.get('meta_cluster'),'streams':streams}
        except Exception as exc:return {'id':name,'reachable':False,'error':str(exc)[:250]}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:members=list(pool.map(inspect_member,(1,2,3)))
    return {'at':time.time(),'members':members,'source':'NATS monitoring /jsz from Lab host; server reachability alone does not prove quorum or job completion'}

def database_activity():
    rows=json.loads(database_sql("""SELECT COALESCE(json_agg(x),'[]') FROM (
SELECT pid,application_name,state,wait_event_type,wait_event,
round(extract(epoch FROM clock_timestamp()-xact_start)) AS transaction_seconds,
pg_blocking_pids(pid) AS blocked_by,left(query,300) AS query
FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()
ORDER BY cardinality(pg_blocking_pids(pid)) DESC,xact_start LIMIT 40) x"""))
    holders=json.loads(database_sql("SELECT COALESCE(json_agg(pid),'[]') FROM ("+BLOCKERS_SQL+") x"))
    return {'at':time.time(),'sessions':rows,'maintenanceLockHolders':holders,
            'schedulerLeaseHolders':lease_holders(),'waitingSessions':sum(bool(r['blocked_by']) for r in rows),'source':'PostgreSQL activity and lock tables'}

def release_database_lock(actor='operator'):
    from . import plane
    if not plane.RUN_LOCK.acquire(blocking=False):raise HTTPException(409,'Control Plane busy')
    try:
        # Recheck identity and held lock in the terminating statement: no arbitrary PID input.
        result=database_sql("SELECT COALESCE(json_agg(x),'[]') FROM (SELECT pid,pg_terminate_backend(pid) AS terminated FROM ("+BLOCKERS_SQL+") holders) x")
        return {'terminated':json.loads(result),'actor':actor,'activity':database_activity()}
    finally:plane.RUN_LOCK.release()

def hold_database_lock():
    from . import plane
    if not plane.RUN_LOCK.acquire(blocking=False):raise HTTPException(409,'Control Plane busy')
    try:
        if database_activity()['maintenanceLockHolders']:raise RuntimeError('Maintenance lock already exists')
        # Bounded maintenance transaction: SHARE permits reads but blocks job writes.
        database_sql('SELECT 1') # ownership check before the detached command
        releases.command(['docker','exec','-d','-e','PGAPPNAME=lab-maintenance','cp-postgres','psql','-X','-v','ON_ERROR_STOP=1','-U','plane','-d','plane','-c',
            "SET lock_timeout='5s'; SET statement_timeout='31min'; BEGIN; LOCK TABLE public.jobs IN SHARE MODE; SELECT pg_sleep(1800); ROLLBACK;"],timeout=8)
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            activity=database_activity()
            if activity['maintenanceLockHolders']:return activity
            time.sleep(.2)
        raise RuntimeError('Maintenance transaction did not acquire its lock')
    finally:plane.RUN_LOCK.release()

def scheduler_status():
    result=[]
    for name in ('scheduler-1','scheduler-2'):
        c=json.loads(releases.command(['docker','inspect','cp-'+name]))[0]
        if c['Config'].get('Labels',{}).get('com.docker.compose.project')!='bigspark-plane':raise RuntimeError('Scheduler ownership mismatch')
        result.append({'id':name,'status':c['State']['Status'],'image':c['Image']})
    return result


def reset():
    release_database_lock(actor='study-reset')
    release_scheduler_lease(actor='study-reset')
    from . import incident_agent
    if any(r['status']!='running' for r in scheduler_status()):incident_agent.power('schedulers','start')
    if any(r['status']!='running' for r in runtime_status(('nats-1','nats-2','nats-3'))):incident_agent.power('messaging','start')
    if catalog():deploy_workers('w1',actor='study-reset');set_network(False,actor='study-reset')

def inject(scenario):
    if scenario=='scheduler-lease':
        database=hold_scheduler_lease();jobs=submit_jobs()
        return {'scenario':scenario,'jobs':jobs,'database':database,'schedulers':scheduler_status()}
    if scenario=='broker-quorum':
        from . import incident_agent as agent
        actions=[agent.power(name,'stop') for name in ('nats-2','nats-3')]
        jobs=submit_jobs()
        return {'scenario':scenario,'jobs':jobs,'actions':actions,'messaging':messaging_status()}
    if scenario=='worker-loss':
        from . import incident_agent as agent
        baseline=worker_releases();action=agent.power('worker-1','stop');jobs=submit_jobs()
        return {'scenario':scenario,'jobs':jobs,'action':action,'baselineWorkers':baseline}
    if scenario=='scheduler-outage':
        from . import incident_agent
        deployment=incident_agent.power('schedulers','stop');jobs=submit_jobs()
        return {'scenario':scenario,'deployment':deployment,'jobs':jobs,'schedulers':scheduler_status()}
    if scenario=='database-lock':
        activity=hold_database_lock()
        request={'payload':'database-lock-'+uuid.uuid4().hex,'key':uuid.uuid4().hex}
        try:
            request['unexpectedAcceptance']=releases.http('http://10.78.10.10:8080/jobs',{'payload':request['payload']},key=request['key'],timeout=6)
            raise RuntimeError('Database lock did not block submission')
        except (OSError,ValueError) as exc:request['error']=str(exc)
        return {'scenario':scenario,'jobs':[],'failedRequest':request,'database':activity,'connectivity':connectivity()}
    if scenario=='worker-backlog':
        deployment=deploy_workers('w2',actor='study-injection');jobs=submit_jobs()
        return {'scenario':scenario,'deployment':deployment,'jobs':jobs,'apiHealth':[releases.probe(n) for n in ('api-1','api-2')]}
    if scenario=='database-route':
        # Accept work before breaking submission so both paths have retained jobs.
        jobs=submit_jobs();policy=set_network(True,actor='study-injection')
        request={'payload':'database-route-'+uuid.uuid4().hex,'key':uuid.uuid4().hex}
        try:
            request['unexpectedAcceptance']=releases.http('http://10.78.10.10:8080/jobs',{'payload':request['payload']},key=request['key'])
            raise RuntimeError('Network fault did not block job submission')
        except (OSError,ValueError) as exc:request['error']=str(exc)
        return {'scenario':scenario,'policy':policy,'jobs':jobs,'failedRequest':request,'connectivity':connectivity(),'routes':network_policy()}
    raise ValueError('Unknown diagnosis scenario')

def judge(scenario,injection,run):
    from . import incident_agent as agent
    verified=agent.dispatch('verify','control-plane','')
    jobs=check_jobs(injection['jobs']);replay=None
    if scenario in ('database-route','database-lock'):
        request=injection['failedRequest']
        try:
            accepted=releases.http('http://10.78.10.10:8080/jobs',{'payload':request['payload']},key=request['key'])
            deadline=time.monotonic()+12
            while True:
                replay=check_jobs([{'id':accepted['id'],'payload':request['payload']}])[0]
                if replay['ok'] or time.monotonic()>deadline:break
                time.sleep(.5)
        except Exception as exc:replay={'ok':False,'error':str(exc)}
    workers=worker_releases();network=network_policy();connections=connectivity()
    removed=(all(w['image']==catalog()['w1']['image'] for w in workers) if scenario=='worker-backlog' else not state()['networkDenied'] and all(v.get('blocked') is False for v in network['observed'].values()) and all(v['reachable'] is True for v in connections['checks']))
    extra={}
    if scenario=='database-lock':
        extra['database']=database_activity();removed=not extra['database']['maintenanceLockHolders']
    elif scenario=='scheduler-outage':
        extra['schedulers']=scheduler_status();removed=all(r['status']=='running' for r in extra['schedulers'])
    elif scenario=='scheduler-lease':
        extra['database']=database_activity();removed=not extra['database']['schedulerLeaseHolders']
    elif scenario=='broker-quorum':
        extra['brokers']=runtime_status(('nats-1','nats-2','nats-3'));extra['messaging']=messaging_status()
        removed=all(r['status']=='running' for r in extra['brokers']) and all(m['reachable'] for m in extra['messaging']['members'])
    elif scenario=='worker-loss':
        extra['workers']=runtime_status(WORKERS)
        removed=all(w['status']=='running' for w in extra['workers'])
        extra['workerImagesUnchanged']={w['app']:w['image'] for w in workers}=={w['app']:w['image'] for w in injection['baselineWorkers']}
    elif scenario not in ('worker-backlog','database-route'):raise ValueError('Unknown diagnosis scenario')
    return {**extra,'agentClaim':run['status'],'repairVerified':removed and verified['passed'] and all(j['ok'] for j in jobs) and (replay is None or replay['ok']),'faultRemoved':removed,'jobs':jobs,'requestReplay':replay,'verification':verified,'workerReleases':workers,'network':network,'connectivity':connections,'diagnosisReview':'pending human review','limitation':'One development attempt; no reliability estimate or long recurrence window.'}

@router.get('/api/system/diagnosis')
def status():return {'scenarios':SCENARIOS,'observations':copy.deepcopy(OBSERVATION),'state':state()}
