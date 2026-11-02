"""Read-only typed inventory plus tightly scoped, recoverable fault experiments."""
import concurrent.futures,copy,hashlib,json,pathlib,subprocess,threading,time,urllib.request,urllib.error,urllib.parse,uuid
from fastapi import APIRouter,HTTPException,Request
from . import plane_incident, plane_releases
ROOT=pathlib.Path(__file__).resolve().parents[1];STACK=ROOT/'control-plane';DATA=ROOT/'.runtime/control-plane'
router=APIRouter();LOCK=threading.RLock();RUN_LOCK=threading.Lock();CANCEL=threading.Event();SNAPSHOT={};ACTIVE=None;INCIDENT_MODEL={}
SCENARIOS={
 'api-loss':{'name':'API process loss','target':'api-1','fault':'pause','hypothesis':'The load balancer routes around an unavailable API; accepted work completes.','deadline':60,'duration':8},
 'worker-loss':{'name':'Worker lease expiry','target':'worker-1','fault':'pause','hypothesis':'An expired worker lease is reclaimed and stale completion cannot overwrite its successor.','deadline':75,'duration':20},
 'nats-loss':{'name':'Messaging member loss','target':'nats-1','fault':'pause','hypothesis':'New work completes through the remaining JetStream quorum while one member is unavailable.','deadline':60,'duration':15},
 'scheduler-loss':{'name':'Scheduler leader loss','target':'leader','fault':'stop','hypothesis':'The standby obtains the database leader lock and continues outbox delivery.','deadline':60,'duration':8},
 'database-loss':{'name':'Database outage / recovery','target':'postgres','fault':'pause','hypothesis':'New writes fail visibly during the outage; accepted work is retained and completes after recovery.','deadline':75,'duration':8},
}
def command(args,timeout=12):
    p=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    if p.returncode:raise RuntimeError((p.stderr or p.stdout)[-500:])
    return p.stdout

def fetch(url,data=None,key=None,timeout=2):
    headers={'Content-Type':'application/json'}
    if key:headers['Idempotency-Key']=key
    req=urllib.request.Request(url,data=json.dumps(data).encode() if data is not None else None,headers=headers)
    with urllib.request.urlopen(req,timeout=timeout) as r:
        raw=r.read();return json.loads(raw) if raw[:1] in (b'{',b'[') else {'text':raw.decode()[:300]}

ACCESS_DATA=ROOT/'.runtime/private-access'
def access_policy():return json.loads((ACCESS_DATA/'policy/grants.json').read_text())
def blueprint():return json.loads((STACK/'blueprint.json').read_text())
def health(resource):
    role=resource['type'];ip=resource['primaryIp']
    if resource['observed']!='running':return {'state':'unknown','source':'probe not attempted','detail':'Runtime is '+resource['observed']}
    if role=='database':
        return {'state':'healthy' if resource.get('runtimeHealth')=='healthy' else 'unhealthy','source':'Docker pg_isready','detail':'Accepting connections; this is not a replication or write-durability check.'}
    path='/healthz' if role=='messaging' else '/-/ready' if role=='metrics' else '/health';port=8222 if role=='messaging' else 9090 if role=='metrics' else 8080
    start=time.monotonic()
    try:
        value=fetch(f'http://{ip}:{port}{path}',timeout=1.5)
        return {'state':'healthy','source':'HTTP '+path,'latencyMs':round((time.monotonic()-start)*1000,1),'at':time.time(),'data':value}
    except urllib.error.HTTPError as e:return {'state':'unhealthy','source':'HTTP '+path,'detail':f'HTTP {e.code}','at':time.time()}
    except Exception:return {'state':'unreachable','source':'HTTP '+path,'detail':'No probe response within 1.5s','at':time.time()}

def collect():
    b=blueprint();resources=copy.deepcopy(b['resources'])
    names=command(['docker','ps','-aq','--filter','label=com.docker.compose.project=bigspark-plane']).split()
    actual=json.loads(command(['docker','inspect',*names])) if names else []
    byname={c['Name'].removeprefix('/cp-'):c for c in actual}
    for r in resources:
        r['primaryNetwork']=next((i['network'] for i in r['interfaces'] if i['address']==r['primaryIp']),None)
        r['desiredInterfaces']=copy.deepcopy(r['interfaces'])
        c=byname.get(r['id']);r['observed']='absent';r['runtimeHealth']=None;r['measured']={'state':'unknown'}
        r['provenance']={'desired':'control-plane/compose.json','observed':'Docker inspect','measured':'direct probe'}
        if c:
            r['observed']='paused' if c['State'].get('Paused') else c['State']['Status'];r['runtimeHealth']=c['State'].get('Health',{}).get('Status')
            r['image']=c['Config']['Image'];r['imageId']=c['Image'];r['startedAt']=c['State']['StartedAt'];r['restarts']=c['RestartCount'];r['limits']={'memoryMiB':c['HostConfig']['Memory']//1048576,'cpus':c['HostConfig']['NanoCpus']/1e9}
            r['interfaces']=[{'name':next((i['name'] for i in r['interfaces'] if i['network']==name),'unknown'),'nameSource':'Compose interface_name','network':name,'address':v['IPAddress'],'prefix':v['IPPrefixLen'],'mac':v['MacAddress'],'gateway':v['Gateway'],'source':'Docker inspect'} for name,v in c['NetworkSettings']['Networks'].items()]
    for r in resources:
        if r['id'] in plane_releases.APPS or r['id'] in ('worker-1','worker-2','worker-3'):
            workspace='releases' if r['id'] in plane_releases.APPS else 'workers'
            r['provisioning']='Terraform: control-plane/'+workspace
            r['provenance']['desired']='control-plane/'+workspace+'/main.tf'
            for interface in r['interfaces']:
                interface.update(name='not observed',nameSource='Runtime interface name not collected')
    with concurrent.futures.ThreadPoolExecutor(max_workers=13) as pool:
        for r,h in zip(resources,pool.map(health,resources)):r['measured']=h;r['status']=h['state']
    networks=json.loads(command(['docker','network','inspect',*['cp-'+g['id'] for g in b['groups']]]))
    groups=copy.deepcopy(b['groups'])
    for g,n in zip(groups,networks):
        g.update(observed=True,endpointCount=len(n.get('Containers',{})),driver=n['Driver'],internal=n['Internal'],networkId=n['Id'][:12],ipam=n['IPAM']['Config'],source='Docker network inspect')
    relationships=copy.deepcopy(b['relationships']);byid={r['id']:r for r in resources}
    for edge in relationships:
        source=byid[edge['source']];deps=source['measured'].get('data',{}).get('dependencies',{})
        count=deps.get(edge['target'])
        edge['observation']={'state':'not instrumented','source':'No per-link traffic measurement'}
        if count:edge['observation']={'state':'observed','source':'application dependency counter since process start',**count}
        if edge['target'].startswith('nats') and 'nats-cluster' in deps:edge['clusterActivity']=deps['nats-cluster'];edge['observation']={'state':'cluster activity','source':'cluster aggregate; destination member not attributed'}
    try:summary=fetch('http://10.78.10.10:8080/summary',timeout=2)
    except Exception:summary={'unavailable':True}
    metrics={}
    for name,query in [('submitP99','histogram_quantile(0.99,sum by(le)(rate(plane_http_request_seconds_bucket{path="/jobs"}[5m])))'),('submitRate','sum(rate(plane_http_requests_total{method="POST",outcome="ok"}[1m]))'),('errorRate','sum(rate(plane_http_requests_total{method="POST",outcome="error"}[1m]))')]:
        try:
            values=fetch('http://10.78.0.20:9090/api/v1/query?'+urllib.parse.urlencode({'query':query}),timeout=1)['data']['result'];value=float(values[0]['value'][1]) if values else None
            metrics[name]=value if value is not None and value==value else None
        except Exception:metrics[name]=None
    if b.get('privateAccess'):
        try:b['privateAccess']['policy']=access_policy()
        except Exception:b['privateAccess']['policyError']='Policy unavailable; access fails closed'
        b['privateAccess']['observations']={r['id']:r['measured'].get('data',{}).get('access') for r in resources if r['id'] in b['privateAccess']['consumers']+[b['privateAccess']['endpoint'],b['privateAccess']['producer']]}
    from . import plane_diagnosis
    b['diagnostics']=plane_diagnosis.observe(summary)
    b.update(resources=resources,groups=groups,relationships=relationships,summary=summary,metrics=metrics,capturedAt=time.time(),collectionError=None,
        infrastructure=[{'id':'docker-host','type':'host-router','name':'Bigspark bridge host','ip':'<LOCAL_GATEWAY>','source':'local deployment','description':str(len(groups))+' internal L2 bridges. Multi-homed clients connect to the subnets they need. No dedicated router VM or port-level ACL is deployed.'},{'id':'host-ingress','type':'endpoint','name':'Host-accessible bridge ingress','address':'10.78.10.10:8080','target':'ingress','protocol':'TCP','port':8080,'source':'Docker bridge address; no published host port'}])
    return b

def incident_agent():
    record = {'name': 'Incident agent', 'state': 'not deployed', 'mode': 'scoped incident response',
              'terraformAddress': 'docker_container.incident_agent[0]',
              'access': ['Inspect all 14 Control Plane components', 'Read logs, probes, metrics', 'Start, stop, restart; API rollback; verify real jobs'],
              'modelAccess': 'Qwen via scoped broker', 'recoveryAccess': 'Control Plane service only'}
    try:
        instances = command(['docker', 'ps', '-aq', '--filter', 'name=^/lab-incident-agent$']).split()
        if not instances:return record
        instance = json.loads(command(['docker', 'inspect', instances[0]]))[0]
        record['state'] = instance['State']['Status']
        record['containerId'] = instance['Id'][:12]
        record['interfaces'] = [{'network': name, 'address': n['IPAddress'], 'prefix': n['IPPrefixLen']} for name, n in instance['NetworkSettings']['Networks'].items()]
        record['address'] = next((n['address'] for n in record['interfaces'] if n['address']), None)
        record['networks'] = []
        for interface in record['interfaces']:
            network = json.loads(command(['docker', 'network', 'inspect', interface['network']]))[0]
            ipam = network['IPAM']['Config'][0]
            record['networks'].append({'id': 'agent', 'name': 'Agent', 'bridge': network['Name'],
                'cidr': ipam['Subnet'], 'gateway': ipam.get('Gateway'), 'internal': network['Internal'],
                'observed': True, 'source': 'Docker network inspect',
                'isolation': 'Internal bridge; host gateway reachable' if network['Internal'] else 'Outbound bridge'})
        if record['state'] == 'running' and record['address']:
            record['observation'] = fetch('http://' + record['address'] + ':8080/status', timeout=1)
            record['state'] = 'connected' if record['observation'].get('healthy') else 'waiting for observations'
    except Exception:
        record['state'] = 'unknown'
    return record

def monitor(stop):
    global SNAPSHOT, INCIDENT_MODEL
    recover_interrupted()
    while not stop.is_set():
        try:
            data=collect()
            data['incidentAgent']=incident_agent()
            with LOCK:SNAPSHOT=data
        except Exception as e:
            with LOCK:
                SNAPSHOT={**(SNAPSHOT or blueprint()),'collectionError':str(e)[:300]}
        model = plane_incident.model_status(fetch)
        with LOCK:INCIDENT_MODEL = model
        stop.wait(3)

def runs():
    records=[]
    for p in sorted(DATA.glob('run-*.json'),reverse=True):
        try:records.append(json.loads(p.read_text()))
        except Exception:pass
    return sorted(records,key=lambda r:r['started'],reverse=True)[:30]
def persist(report):
    path=DATA/('run-'+report['id']+'.json');temp=path.with_suffix('.tmp');temp.write_text(json.dumps(report,indent=2));temp.replace(path)

def recover_interrupted():
    allowed={r['id'] for r in blueprint()['resources']}
    for report in runs():
        if report.get('status')!='running':continue
        target=report.get('target')
        try:
            if report.get('faultArmed') and target in allowed:
                status=json.loads(command(['docker','inspect','cp-'+target]))[0]['State']
                if status.get('Paused'):command(['docker','unpause','cp-'+target])
                elif not status.get('Running'):command(['docker','start','cp-'+target])
            report.update(status='interrupted',reason='Controller restarted; fault target restored where needed.',finished=time.time())
        except Exception as e:report.update(status='error',cleanupError=str(e))
        persist(report)

def shutdown():
    CANCEL.set()
    deadline=time.monotonic()+20
    while RUN_LOCK.locked() and time.monotonic()<deadline:time.sleep(.2)

def experiment(kind):
    global ACTIVE
    spec=SCENARIOS[kind];rid=uuid.uuid4().hex
    report={'id':rid,'scenario':kind,'name':spec['name'],'hypothesis':spec['hypothesis'],'started':time.time(),'status':'running','target':spec['target'],'fault':spec['fault'],'samples':[],'events':[],'accepted':[],'submissionErrors':0,'duplicateRequests':0,'seed':int(rid[:8],16),'criteria':{'deadlineSeconds':spec['deadline'],'allAcceptedCompleted':True,'duplicateCompletions':0},'source':'real direct HTTP probes and persisted job states'}
    ACTIVE=report;target=spec['target'];injected=False;restored=False;start=time.monotonic();delays=[]
    try:
        persist(report)
        current=collect()
        if any(r['measured']['state']!='healthy' for r in current['resources']):raise RuntimeError('Baseline must be healthy before injecting faults.')
        report['baseline']={'images':{r['id']:r.get('imageId') for r in current['resources']},'blueprintSha256':hashlib.sha256((STACK/'blueprint.json').read_bytes()).hexdigest(),'jobCounts':current['summary'].get('jobs',{})}
        if target=='leader':
            target=next((r['id'] for r in current['resources'] if r['type']=='scheduler' and r['measured'].get('data',{}).get('leader')),None)
            if not target:raise RuntimeError('No observed scheduler leader')
            report['target']=target
        completed=set();submission=0;fault_at=None;data=current;accepted_during_fault=set()
        sample={'health':{r['id']:r['measured']['state'] for r in current['resources']}}
        if kind=='nats-loss':
            cluster=fetch('http://10.78.30.11:8222/jsz?streams=true&config=true&accounts=true')
            stream=next(s for a in cluster['account_details'] for s in a.get('stream_detail',[]) if s['name']=='JOBS')
            target=stream.get('cluster',{}).get('leader')
            if stream['config']['num_replicas']!=3 or target not in {'nats-1','nats-2','nats-3'}:raise RuntimeError('Expected a three-replica JOBS stream with an observed leader')
            # Replica currency is authoritative on the leader, not a follower's cached view.
            leader_ip=next(r['primaryIp'] for r in current['resources'] if r['id']==target)
            cluster=fetch(f'http://{leader_ip}:8222/jsz?streams=true&config=true&accounts=true')
            stream=next(s for a in cluster['account_details'] for s in a.get('stream_detail',[]) if s['name']=='JOBS')
            if stream.get('cluster',{}).get('leader')!=target or not all(r.get('current') for r in stream['cluster'].get('replicas',[])):raise RuntimeError('Wait for stream replicas to become current before testing')
            report['streamBaseline']={'replicas':stream['config']['num_replicas'],'cluster':stream.get('cluster'),'source':'JOBS stream leader monitoring endpoint'}
            report['target']=target
        if kind=='worker-loss':
            key=rid+':0';payload=f'control-plane-experiment:{report["seed"]}:0'
            accepted=fetch('http://10.78.10.10:8080/jobs',{'payload':payload},key,timeout=7)
            report['accepted'].append(accepted['id']);submission=1
            duplicate=fetch('http://10.78.10.10:8080/jobs',{'payload':payload},key,timeout=7)
            if duplicate['id']!=accepted['id']:raise RuntimeError('Idempotency violation')
            report['duplicateRequests']+=1
            until=time.monotonic()+5
            while time.monotonic()<until and not CANCEL.is_set():
                job=fetch('http://10.78.10.10:8080/jobs/'+accepted['id'])
                if job['state']=='running':
                    target=job['owner']
                    if target not in {'worker-1','worker-2','worker-3'}:raise RuntimeError('Unexpected worker owner')
                    report.update(target=target,leasedJob=job,faultArmed=True);persist(report);injected=True
                    command(['docker','pause','cp-'+target]);fault_at=time.monotonic()-start
                    report['events'].append({'kind':'fault','elapsed':fault_at,'target':target});break
                CANCEL.wait(.02)
            if not injected:raise RuntimeError('Could not observe an active worker lease before injection')
        while time.monotonic()-start<spec['deadline'] and not CANCEL.is_set():
            elapsed=time.monotonic()-start
            if elapsed>=3 and not injected:
                # Mark before invocation so timeout still enters cleanup.
                injected=True;report['faultArmed']=True;persist(report)
                command(['docker',spec['fault'],'cp-'+target],timeout=12)
                fault_at=time.monotonic()-start
                report['events'].append({'kind':'fault','elapsed':fault_at,'target':target})
            if injected and not restored and elapsed>=fault_at+spec['duration']:
                command(['docker','unpause' if spec['fault']=='pause' else 'start','cp-'+target]);restored=True;report['events'].append({'kind':'restore','elapsed':time.monotonic()-start,'target':target})
            # Every job has a unique key. Retry an accepted key once to verify idempotency.
            if submission<24:
                key=rid+':'+str(submission);payload=f'control-plane-experiment:{report["seed"]}:{submission}';stamp=time.monotonic()
                try:
                    accepted=fetch('http://10.78.10.10:8080/jobs',{'payload':payload},key,timeout=7);delays.append(time.monotonic()-stamp);report['accepted'].append(accepted['id']);submission+=1
                    if injected and not restored:accepted_during_fault.add(accepted['id'])
                    duplicate=fetch('http://10.78.10.10:8080/jobs',{'payload':payload},key,timeout=7)
                    if duplicate['id']!=accepted['id']:raise RuntimeError('Idempotency violation')
                    report['duplicateRequests']+=1
                except urllib.error.URLError:report['submissionErrors']+=1
            data=collect()
            def check(identity):
                try:return identity,fetch('http://10.78.10.10:8080/jobs/'+identity,timeout=2)
                except Exception:return identity,{}
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                for identity,job in pool.map(check,[i for i in report['accepted'] if i not in completed]):
                    if job.get('state')=='completed':completed.add(identity)
            sample={'elapsed':round(time.monotonic()-start,2),'health':{r['id']:r['measured']['state'] for r in data['resources']},'jobs':data['summary'].get('jobs'),'outbox':data['summary'].get('outbox_pending'),'completed':len(completed),'accepted':len(report['accepted']),'leader':next((r['id'] for r in data['resources'] if r['type']=='scheduler' and r['measured'].get('data',{}).get('leader')),None)}
            if injected and not restored:report['newJobsCompletedDuringFault']=len(completed & accepted_during_fault)
            report['samples'].append(sample);persist(report)
            if restored and submission==24 and len(completed)==24 and all(s=='healthy' for s in sample['health'].values()):break
            CANCEL.wait(.5)
        checks={'allAcceptedCompleted':len(completed)==24,'idempotency':report['duplicateRequests']==24,'uniqueCompletionRows':data['summary'].get('duplicate_completions')==0,'restored':restored,'finalHealth':all(s=='healthy' for s in sample['health'].values())}
        if kind=='worker-loss':
            recovered=fetch('http://10.78.10.10:8080/jobs/'+report['leasedJob']['id']);report['recoveredJob']=recovered
            checks['leaseReclaimed']=recovered['state']=='completed' and recovered['attempts']>=2 and recovered['lease_token']!=report['leasedJob']['lease_token'] and recovered['owner']!=target
        if kind=='scheduler-loss':checks['standbyElected']=any(s['leader'] and s['leader']!=target for s in report['samples'])
        if kind=='nats-loss':checks['newWorkCompletedDuringMemberLoss']=report.get('newJobsCompletedDuringFault',0)>0
        report.update(checks=checks,status='cancelled' if CANCEL.is_set() else 'passed' if all(checks.values()) else 'failed',completed=len(completed),durationSeconds=round(time.monotonic()-start,2),submitP99=sorted(delays)[max(0,int(len(delays)*.99)-1)] if delays else None)
        report['reason']='All 24 accepted jobs completed; duplicate submission keys returned the same jobs; recovery health verified.' if report['status']=='passed' else 'Cancelled by operator; target restored.' if report['status']=='cancelled' else 'One or more recorded criteria were not met before the deadline.'
    except Exception as e:report.update(status='error',reason=str(e)[:500])
    finally:
        if injected and not restored:
            try:command(['docker','unpause' if spec['fault']=='pause' else 'start','cp-'+target]);report['cleanup']='restored'
            except Exception as e:report.update(cleanupError=str(e),status='error',reason='Fault cleanup failed; inspect the target: '+str(e))
        report['finished']=time.time()
        try:persist(report)
        finally:ACTIVE=None;RUN_LOCK.release()

@router.get('/api/system')
def snapshot():
    with LOCK:
        result = {**SNAPSHOT,'incident':plane_incident.readiness(SNAPSHOT, INCIDENT_MODEL),'experiments':{'active':copy.deepcopy(ACTIVE),'runs':runs(),'scenarios':SCENARIOS}}
    from . import incident_agent as broker
    result['agentActivity'] = broker.public_state()
    result['incident']['automation'] = 'Scoped automatic repair ' + ('enabled' if result['agentActivity']['enabled'] else 'paused')
    result['incident']['model'] = {**result['incident']['model'], 'agentState': 'Connected via broker' if result['agentActivity']['connected'] else 'Agent disconnected', 'recoveryEnabled': result['agentActivity']['enabled']}
    run = result['agentActivity']['run']
    if run and run.get('modelTiming'):
        result['incident']['model']['detail'] = 'Inference connected. Last investigation: ' + run['status'] + '.'
    result['releases'] = plane_releases.snapshot()
    result['releases']['agentMode'] = 'automatic repair enabled' if result['agentActivity']['enabled'] else 'automatic repair paused'
    result['incident']['alarms'] += result['releases']['alarms']
    if result['releases']['alarms'] and result['incident']['health'] == 'healthy':
        result['incident']['health'] = 'degraded'
    result['incident']['scope'] = 'Control Plane API probes plus real job submission and completion through ingress. Release actions and incidents are persisted.'
    return result
@router.get('/api/system/resources/{identity}/logs')
def logs(identity:str,lines:int=200):
    if identity not in {'incident-agent','incident-agent-runtime'}|{r['id'] for r in blueprint()['resources']}:raise HTTPException(404,'Unknown resource')
    if not 1<=lines<=500:raise HTTPException(400,'Tail must be between 1 and 500 lines')
    if identity=='incident-agent':
        from . import incident_agent as broker
        return broker.log_tail(lines)
    container='lab-incident-agent' if identity=='incident-agent-runtime' else 'cp-'+identity
    try:
        p=subprocess.run(['docker','logs','--timestamps','--tail',str(lines),container],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=12)
    except subprocess.TimeoutExpired:raise HTTPException(504,'Container log read timed out')
    if p.returncode:raise HTTPException(502,'Container logs are unavailable: '+p.stdout[-200:])
    return {'text':p.stdout[-128000:],'truncated':len(p.stdout)>128000,'lines':lines,'instance':identity}

@router.post('/api/system/resources/{identity}/power/{action}')
def power(identity:str,action:str):
    resource=next((r for r in blueprint()['resources'] if r['id']==identity and r.get('runtime')=='container'),None)
    if resource is None:raise HTTPException(404,'Unknown container instance')
    if action not in {'start','stop','restart'}:raise HTTPException(400,'Unknown power action')
    if not RUN_LOCK.acquire(blocking=False):raise HTTPException(409,'Wait for the active experiment or instance action to finish')
    try:
        container=json.loads(command(['docker','inspect','cp-'+identity]))[0]
        if container.get('Config',{}).get('Labels',{}).get('com.docker.compose.project')!='bigspark-plane':
            raise HTTPException(409,'Container does not belong to this lab')
        if container['State'].get('Paused'):raise HTTPException(409,'Container is paused; restore the fault before changing power')
        args=['docker',action]
        if action in {'stop','restart'}:args+=['--time','10']
        command([*args,container['Id']],timeout=30)
        state=json.loads(command(['docker','inspect',container['Id']]))[0]['State']
        return {'id':identity,'action':action,'observed':state['Status'],'startedAt':state.get('StartedAt'),'source':'Docker inspect'}
    except HTTPException:raise
    except Exception as e:raise HTTPException(502,'Instance action could not be verified: '+str(e)[:250])
    finally:RUN_LOCK.release()
@router.post('/api/system/experiments')
async def start_experiment(request:Request):
    body=await request.json()
    if not isinstance(body,dict):raise HTTPException(400,'Expected an experiment object')
    kind=body.get('scenario')
    if kind not in SCENARIOS:raise HTTPException(400,'Unknown experiment')
    if not RUN_LOCK.acquire(blocking=False):raise HTTPException(409,'An experiment is running')
    CANCEL.clear()
    threading.Thread(target=experiment,args=(kind,),daemon=True).start();return {'accepted':True}
@router.post('/api/system/experiments/cancel')
def cancel():CANCEL.set();return {'accepted':True}
@router.get('/api/system/experiments/{identity}')
def report(identity:str):
    if not len(identity)==32 or any(c not in '0123456789abcdef' for c in identity):raise HTTPException(404,'Unknown experiment')
    p=DATA/('run-'+identity+'.json')
    if not p.exists():raise HTTPException(404,'Unknown experiment')
    return json.loads(p.read_text())

@router.post('/api/system/private-access/{consumer}/{action}')
def access_grant(consumer:str,action:str):
    publication=blueprint().get('privateAccess',{})
    if consumer not in publication.get('consumers',[]):raise HTTPException(404,'Unknown consumer')
    if action not in {'grant','revoke'}:raise HTTPException(400,'Unknown access action')
    if not RUN_LOCK.acquire(blocking=False):raise HTTPException(409,'An experiment or instance action is running')
    try:
        policy=access_policy();policy['grants'][consumer]['catalog']=action=='grant';policy['revision']+=1;policy['updatedAt']=time.time()
        path=ACCESS_DATA/'policy/grants.json';temp=path.with_suffix('.tmp');temp.write_text(json.dumps(policy,indent=2));temp.chmod(0o600);temp.replace(path)
        return {'consumer':consumer,'granted':action=='grant','revision':policy['revision'],'effective':'Next request admission; already admitted requests may finish'}
    except Exception:raise HTTPException(503,'Access policy could not be updated; inspect the current policy state')
    finally:RUN_LOCK.release()
