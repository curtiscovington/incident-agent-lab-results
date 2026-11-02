from __future__ import annotations
import sys
import concurrent.futures, contextlib, datetime, grp, hashlib, json, os, pathlib, secrets, shlex, shutil, subprocess, threading, time, urllib.request
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from . import plane, studio, lab_system, lab_mcp, steering, discovery, neuron_loop

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNTIME = ROOT / '.runtime'
RUNTIME.mkdir(exist_ok=True)
TF = RUNTIME / 'bin/terraform'
TOKEN = secrets.token_urlsafe(32)
LOCK = threading.RLock()
OP_LOCK = threading.Lock()
EVENTS = []
NODES = {}
BOOT_IDS = {}
QUEUE = {}
OP = {'running': False, 'name': None, 'log': '', 'error': None}
LOAD = {'enabled': False, 'sent': 0, 'failed': 0, 'rate': 1}
EXPERIMENT = None
MONITOR_ERROR = None
VM_READY = False
if (RUNTIME / 'events.jsonl').exists():
    for line in (RUNTIME / 'events.jsonl').read_text().splitlines()[-200:]:
        try: EVENTS.append(json.loads(line))
        except ValueError: pass
reports = sorted(RUNTIME.glob('trial-*.json'))
if reports:
    try: EXPERIMENT = json.loads(reports[-1].read_text())
    except ValueError: pass
SETTINGS = {'api_count': 3, 'worker_count': 3, 'vcn_cidr': '10.77.0.0/16'}
if (RUNTIME / 'settings.json').exists(): SETTINGS.update(json.loads((RUNTIME / 'settings.json').read_text()))
TOPOLOGY = None
SHUTDOWN = threading.Event()

def event(kind, message, **extra):
    row = {'time': time.time(), 'kind': kind, 'message': message, **extra}
    with LOCK:
        EVENTS.append(row)
        del EVENTS[:-200]
        with (RUNTIME / 'events.jsonl').open('a') as f: f.write(json.dumps(row) + '\n')
    return row

def privileged_group(args):
    """Use authorized group membership without requiring a desktop logout."""
    try:
        group = grp.getgrnam('incus-admin')
        if group.gr_gid not in os.getgroups():
            return ['sg', 'incus-admin', '-c', shlex.join([str(a) for a in args])]
    except KeyError: pass
    return [str(a) for a in args]

def run(args, timeout=30, group=False):
    return subprocess.run(privileged_group(args) if group else [str(x) for x in args], cwd=ROOT / 'infra',
                          capture_output=True, text=True, timeout=timeout)

def incus(*args, timeout=20):
    if not shutil.which('incus'): raise RuntimeError('Incus is not installed. Complete host setup first.')
    p = run(['incus', *args], timeout=timeout, group=True)
    if p.returncode: raise RuntimeError((p.stderr or p.stdout).strip()[-1800:])
    return p.stdout

def http(ip, path='/health', data=None, timeout=1.2):
    req = urllib.request.Request(f'http://{ip}:8080{path}', data=json.dumps(data).encode() if data is not None else None,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r: return json.load(r)

def desired():
    import ipaddress
    base = ipaddress.ip_network(SETTINGS['vcn_cidr'])
    subnets = {k: str(ipaddress.ip_network((int(base.network_address) + i*256, 24))) for k, i in [('api', 10), ('worker', 20), ('management', 30)]}
    nodes = {}
    for role in ['api', 'worker']:
        for i in range(SETTINGS[f'{role}_count']):
            nodes[f'{role}-{i+1}'] = {'role': role, 'subnet': role, 'ip': str(ipaddress.ip_network(subnets[role]).network_address + 11 + i)}
    nodes['broker-1'] = {'role': 'broker', 'subnet': 'management', 'ip': str(ipaddress.ip_network(subnets['management']).network_address + 10)}
    gateway = {'name':'service-gateway', 'role':'service-gateway', 'status':'planned', 'ip':str(base.network_address + 40*256 + 10),
               'port':8080, 'protocol':'tcp', 'backend':'broker-1', 'backend_ip':nodes['broker-1']['ip'],
               'backend_port':8080, 'implementation':'incus-network-forward', 'description':'Private endpoint for the mock queue service'}
    return {'nodes': nodes, 'subnets': subnets, 'vcn': SETTINGS['vcn_cidr'], 'project': 'datacenter-lab', 'gateways':{'service-gateway':gateway}}

def inventory(): return TOPOLOGY or desired()

def refresh_topology():
    global TOPOLOGY
    if not TF.exists(): return
    p = run([TF, 'output', '-json', 'topology'], group=True)
    if p.returncode == 0:
        try:
            value = json.loads(p.stdout)
            if 'nodes' in value:
                TOPOLOGY = value
                (RUNTIME / 'topology.json').write_text(json.dumps(value, indent=2))
        except ValueError: pass

if (RUNTIME / 'topology.json').exists(): TOPOLOGY = json.loads((RUNTIME / 'topology.json').read_text())

def ensure_vars():
    key = RUNTIME / 'id_ed25519'
    if not key.exists():
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)], check=True)
    variables = dict(SETTINGS, ssh_public_key=key.with_suffix('.pub').read_text().strip())
    (ROOT / 'infra/lab.auto.tfvars.json').write_text(json.dumps(variables, indent=2))

def ssh(name, command, timeout=20):
    node = inventory()['nodes'].get(name)
    if not node: raise ValueError('Unknown node')
    args = ['ssh', '-i', str(RUNTIME / 'id_ed25519'), '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=4',
            '-o', 'StrictHostKeyChecking=accept-new', '-o', f'UserKnownHostsFile={RUNTIME / "known_hosts"}',
            f'lab@{node["ip"]}', command]
    p = run(args, timeout=timeout)
    if p.returncode: raise RuntimeError((p.stderr or p.stdout).strip()[-1800:])
    return p.stdout

COMMANDS = {
    'pause-service': 'sudo systemctl kill --kill-who=main --signal=SIGSTOP lab-workload',
    'stop-service': 'sudo systemctl stop lab-workload',
    'restart-service': 'sudo systemctl restart lab-workload',
    'resume-service': 'sudo systemctl kill --kill-who=main --signal=SIGCONT lab-workload',
}

def action(name, command, actor='human'):
    if name not in inventory()['nodes']: raise ValueError('Unknown node')
    if command in COMMANDS:
        out = ssh(name, COMMANDS[command])
    elif command in ['reboot', 'start', 'stop']:
        op = {'reboot': 'restart', 'start': 'start', 'stop': 'stop'}[command]
        out = incus(op, name, '--project', 'datacenter-lab', timeout=90)
    else: raise ValueError('Unknown action')
    event('action', f'{name}: {command}', node=name, action=command, actor=actor)
    return out

def background(name, fn):
    if not OP_LOCK.acquire(blocking=False): raise HTTPException(409, 'Another operation is running')
    with LOCK: OP.update(running=True, name=name, log='', error=None)
    def execute():
        try:
            event('operation', f'{name} started')
            fn()
            event('operation', f'{name} finished')
        except Exception as e:
            with LOCK: OP['error'] = str(e)
            event('error', f'{name}: {e}')
        finally:
            with LOCK: OP['running'] = False
            OP_LOCK.release()
    threading.Thread(target=execute, daemon=True).start()

def tf_command(args):
    with subprocess.Popen(privileged_group([TF, *args]), cwd=ROOT/'infra', stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as p:
        for line in p.stdout:
            with LOCK: OP['log'] = (OP['log'] + line)[-45000:]
        if p.wait(): raise RuntimeError(f'Terraform {args[0]} failed; see operation log')

def refresh_ssh_host_keys():
    """Trust guest host keys obtained through the local Incus management socket."""
    entries = []
    for name, node in inventory()['nodes'].items():
        key = incus('exec', name, '--project', 'datacenter-lab', '--',
                    'cat', '/etc/ssh/ssh_host_ed25519_key.pub').split()
        if len(key) < 2 or key[0] != 'ssh-ed25519':
            raise RuntimeError(f'{name}: missing Ed25519 SSH host key')
        entries.append(f"{node['ip']} {key[0]} {key[1]}\n")
    temporary = RUNTIME / 'known_hosts.tmp'
    temporary.write_text(''.join(entries))
    temporary.replace(RUNTIME / 'known_hosts')
    for name in inventory()['nodes']:
        ssh(name, 'true')

def record_deployment():
    rows = json.loads(incus('list', '--project', 'datacenter-lab', '--format=json'))
    manifest = {'created': time.time(), 'topology': inventory(), 'nodes': {row['name']: {
        'base_image': row['config'].get('volatile.base_image'),
        'cpu': row['expanded_config'].get('limits.cpu'),
        'memory': row['expanded_config'].get('limits.memory'),
        'architecture': row['architecture']} for row in rows},
        'source_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [ROOT/'infra/main.tf', ROOT/'guest/service.py', ROOT/'app/main.py', ROOT/'infra/.terraform.lock.hcl']}}
    (RUNTIME/'deployment.json').write_text(json.dumps(manifest, indent=2))

def provision(plan_only=False):
    if not shutil.which('incus'): raise RuntimeError('Host setup is required before deployment')
    server = json.loads(incus('query', '/1.0'))
    if not plan_only and 'qemu' not in server['environment']['driver']:
        raise RuntimeError('VM support is unavailable. Complete the ARM firmware setup before deploying.')
    ensure_vars()
    tf_command(['init', '-input=false', '-no-color'])
    tf_command(['plan', '-input=false', '-no-color', '-out=lab.tfplan'])
    if plan_only: return
    try: tf_command(['apply', '-input=false', '-no-color', 'lab.tfplan'])
    finally: refresh_topology()
    event('operation', 'VMs created; waiting for cloud-init and workload health')
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        with LOCK:
            ready = len(NODES) == len(inventory()['nodes']) and all(n['status'] == 'healthy' for n in NODES.values())
        if ready:
            refresh_ssh_host_keys()
            record_deployment()
            event('operation', 'All VM workloads are healthy; SSH and services are ready')
            return
        time.sleep(3)
    raise RuntimeError('VMs exist, but workloads did not all become healthy within 10 minutes. Check cloud-init logs.')

def sample_node(item, statuses):
    name, node = item
    result = dict(node, name=name, status='not-deployed', vm_state=statuses.get(name, 'Absent'), metrics=None)
    if name in statuses:
        result['status'] = 'offline'
        if statuses[name] == 'Running':
            try: result.update(status='healthy', metrics=http(node['ip']))
            except Exception: result['status'] = 'unreachable'
    return name, result

def observe_boot(name, metrics):
    new_boot = (metrics or {}).get('boot_id')
    old_boot = BOOT_IDS.get(name)
    if old_boot and new_boot and old_boot != new_boot:
        event('reboot-observed', f'{name}: guest boot ID changed', node=name)
    if new_boot: BOOT_IDS[name] = new_boot

def monitor():
    global NODES, QUEUE, MONITOR_ERROR, VM_READY
    failures = {}
    while not SHUTDOWN.is_set():
        statuses = {}
        try:
            VM_READY = 'qemu' in json.loads(incus('query', '/1.0'))['environment']['driver']
        except Exception:
            VM_READY = False
        try:
            rows = json.loads(incus('list', '--project', 'datacenter-lab', '--format=json'))
            statuses = {x['name']: x['status'] for x in rows}
            MONITOR_ERROR = None
        except Exception as e:
            MONITOR_ERROR = str(e)
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            fresh = dict(pool.map(lambda item: sample_node(item, statuses), inventory()['nodes'].items()))
        if MONITOR_ERROR and TOPOLOGY:
            for node in fresh.values(): node.update(status='unknown', vm_state='Unknown')
        for name, node in fresh.items():
            old = NODES.get(name, {})
            if node['status'] == 'unreachable' and OP['running'] and OP['name'] == 'Deploy infrastructure' and name not in BOOT_IDS:
                node['status'] = 'provisioning'
            failures[name] = failures.get(name, 0) + 1 if node['status'] == 'unreachable' else 0
            if failures[name] == 3: event('alarm', f'{name}: service health check failed three times', node=name)
            if node['status'] == 'healthy' and old.get('status') == 'unreachable': event('recovery', f'{name}: service responding again', node=name)
            observe_boot(name, node.get('metrics'))
        with LOCK: NODES = fresh
        try:
            q = http(inventory()['nodes']['broker-1']['ip'], '/queue')
            with LOCK: QUEUE = dict(q, available=True, sampled_at=time.time())
        except Exception:
            with LOCK: QUEUE = dict(QUEUE, available=False)
        SHUTDOWN.wait(2)

def traffic():
    cursor = 0
    while not SHUTDOWN.is_set():
        if LOAD['enabled']:
            apis = [n for n in inventory()['nodes'].values() if n['role'] == 'api']
            try:
                http(apis[cursor % len(apis)]['ip'], '/jobs', {}, timeout=2)
                LOAD['sent'] += 1
            except Exception: LOAD['failed'] += 1
            cursor += 1
        SHUTDOWN.wait(1 / LOAD['rate'])

def experiment(mode):
    global EXPERIMENT
    with LOCK:
        if not NODES or not all(n['status'] == 'healthy' for n in NODES.values()): raise RuntimeError('All nodes must be healthy before a trial')
    target = 'worker-1'
    start = time.time()
    run_id = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    boot_ids_before = dict(BOOT_IDS)
    before = http(inventory()['nodes']['broker-1']['ip'], '/queue')
    EXPERIMENT = {'id': run_id, 'status': 'running', 'mode': mode, 'target': target, 'started': start}
    submitted = 0
    prior_load = LOAD['enabled']
    LOAD['enabled'] = False
    repaired = False
    verification_sent = False
    report = {}
    try:
        action(target, 'pause-service', 'injector')
        for i in range(12):
            apis = [n for n in inventory()['nodes'].values() if n['role'] == 'api']
            http(apis[i % len(apis)]['ip'], '/jobs', {})
            submitted += 1
        event('experiment', f'Trial {run_id}: worker paused, {submitted} jobs accepted; mode={mode}')
        deadline = time.monotonic() + 65
        consecutive_healthy = 0
        while time.monotonic() < deadline:
            time.sleep(2)
            # A scripted baseline responds to observed alarms; it has no hidden fault label.
            alarm = any(e['kind'] == 'alarm' and e.get('node') == target and e['time'] >= start for e in EVENTS)
            if mode == 'scripted' and alarm and not repaired:
                evidence = ssh(target, 'systemctl is-active lab-workload; ps -eo stat,comm | head -20')
                event('agent', 'Scripted baseline: service is unresponsive; restarting only the service', evidence=evidence, node=target)
                action(target, 'restart-service', 'scripted-baseline')
                repaired = True
            try:
                health = http(inventory()['nodes'][target]['ip'])
                if not verification_sent:
                    for i in range(18):
                        http(apis[i % len(apis)]['ip'], '/jobs', {})
                        submitted += 1
                    verification_sent = True
                queue = http(inventory()['nodes']['broker-1']['ip'], '/queue')
                recovered = queue['completed'] >= before['total'] + submitted and health['processed'] > 0
                consecutive_healthy = consecutive_healthy + 1 if recovered else 0
            except Exception: consecutive_healthy = 0
            if consecutive_healthy >= 5:
                report = {'status': 'passed', 'recovery_seconds': round(time.time()-start, 2), 'durable_health_seconds': 10}
                break
        else: report = {'status': 'failed', 'reason': 'Worker health, progress, and queue completion did not recover before timeout'}
    except Exception as e:
        report = {'status': 'error', 'reason': str(e)}
        raise
    finally:
        relevant = [e for e in EVENTS if e['time'] >= start]
        result = {**EXPERIMENT, **report, 'finished': time.time(), 'accepted_jobs': submitted,
                  'observed_reboots': sum(e['kind']=='reboot-observed' for e in relevant),
                  'service_restart_actions': sum(e.get('action')=='restart-service' for e in relevant),
                  'topology': inventory(), 'events': relevant, 'queue_before': before, 'queue_after': dict(QUEUE),
                  'boot_ids_before': boot_ids_before, 'boot_ids_after': dict(BOOT_IDS),
                  'deployment': json.loads((RUNTIME/'deployment.json').read_text()) if (RUNTIME/'deployment.json').exists() else None,
                  'terraform_version': '1.13.5', 'provider_version': '1.2.0'}
        EXPERIMENT = result
        LOAD['enabled'] = prior_load
        (RUNTIME / f'trial-{run_id}.json').write_text(json.dumps(result, indent=2))
        event('experiment', f'Trial {run_id}: {result["status"]}')
        # Cleanup happens after grading and is recorded separately, never counted as recovery.
        try: action(target, 'resume-service', 'cleanup')
        except Exception as e: event('error', f'Trial cleanup: {e}')

from app.research import Research, AgentSpec, Scenario
research = Research(sys.modules[__name__])

@contextlib.asynccontextmanager
async def lifespan(app):
    studio.STOP.clear()
    studio.ensure()
    steering.register(studio)
    discovery.recover()
    ensure_vars()
    refresh_topology()
    for fn in [monitor, traffic]: threading.Thread(target=fn, daemon=True).start()
    threading.Thread(target=plane.monitor,args=(SHUTDOWN,),daemon=True).start()
    threading.Thread(target=plane_releases.monitor,args=(SHUTDOWN,),daemon=True).start()
    incident_agent.initialize()
    recovery_study.initialize()
    threading.Thread(target=plane_diagnosis.monitor,args=(SHUTDOWN,),daemon=True).start()
    lab_mcp.token()
    async with lab_mcp.server.session_manager.run():
        yield
    SHUTDOWN.set()
    studio.STOP.set()
    plane.shutdown()
    research.shutdown()

app = FastAPI(lifespan=lifespan)
app.include_router(steering.router)
app.include_router(discovery.router)
app.include_router(neuron_loop.router)
app.include_router(plane.router)
from . import terraform_inventory, plane_releases, incident_agent, recovery_study, plane_diagnosis
app.include_router(terraform_inventory.router)
app.include_router(plane_releases.router)
app.include_router(incident_agent.router)
app.include_router(recovery_study.router)
app.include_router(plane_diagnosis.router)
app.include_router(studio.router)
app.include_router(lab_system.router)
app.include_router(lab_mcp.router)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['lab.local', 'localhost', '127.0.0.1', 'testserver'])

@app.middleware('http')
async def guard(request: Request, call_next):
    if request.url.path.startswith('/api/incident-agent/'):
        from fastapi.responses import JSONResponse
        if not incident_agent.authenticated(request):return JSONResponse({'detail':'Scoped agent credential required'},status_code=401)
    elif (request.client and request.client.host=='10.78.80.11') or request.headers.get('x-real-ip')=='10.78.80.11' or (request.headers.get('authorization','').startswith('Bearer ') and incident_agent.authenticated(request)):
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail':'Agent access is limited to the Control Plane broker'},status_code=403)
    elif request.url.path == '/mcp' or request.url.path.startswith('/mcp/'):
        from fastapi.responses import JSONResponse
        origin=request.headers.get('origin')
        if origin and origin not in [f'http://{request.headers.get("host")}',f'https://{request.headers.get("host")}']:
            return JSONResponse({'detail':'Untrusted MCP origin'},status_code=403)
        if not secrets.compare_digest(request.headers.get('authorization','').encode(),('Bearer '+lab_mcp.token()).encode()):
            return JSONResponse({'detail':'MCP bearer authentication required'},status_code=401,headers={'WWW-Authenticate':'Bearer'})
    elif request.method == 'POST':
        if request.headers.get('x-lab-token') != TOKEN:
            from fastapi.responses import JSONResponse
            return JSONResponse({'detail': 'Reload the lab to obtain a current action token'}, status_code=403)
        origin = request.headers.get('origin')
        if origin and origin not in [f'http://{request.headers.get("host")}', f'https://{request.headers.get("host")}']:
            from fastapi.responses import JSONResponse
            return JSONResponse({'detail': 'Cross-origin actions are not allowed'}, status_code=403)
    if request.method == 'POST' and recovery_study.blocked(request.url.path):
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail':'Recovery study owns the Control Plane. Pause or end the study from its canvas panel.'},status_code=409)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
    return response

@app.get('/api/state')
def state():
    with LOCK:
        return {'token': TOKEN, 'topology': inventory(), 'nodes': list(NODES.values()), 'queue': dict(QUEUE), 'operation': dict(OP),
                'events': list(EVENTS[-80:]), 'load': dict(LOAD), 'experiment': EXPERIMENT, 'settings': SETTINGS,
                'research': {'agents': research.public_agents(), 'active': research.active, 'runs': research.reports(), 'model_configured': (RUNTIME/'model.json').exists()},
                'host': {'incus_installed': bool(shutil.which('incus')), 'terraform_installed': TF.exists(), 'architecture': os.uname().machine, 'monitor_error': MONITOR_ERROR, 'vm_ready': VM_READY}}

@app.post('/api/deploy')
def deploy():
    background('Deploy infrastructure', provision)
    return {'accepted': True}

@app.post('/api/plan')
def plan():
    background('Terraform plan', lambda: provision(True))
    return {'accepted': True}

@app.post('/api/settings')
async def settings(request: Request):
    if OP['running']: raise HTTPException(409, 'Wait for the active operation')
    data = await request.json()
    for key in ['api_count', 'worker_count']:
        value = data.get(key)
        if type(value) is not int or not 1 <= value <= 12: raise HTTPException(400, 'Node counts must be integers between 1 and 12')
    SETTINGS.update({k: data[k] for k in ['api_count', 'worker_count']})
    (RUNTIME/'settings.json').write_text(json.dumps(SETTINGS))
    ensure_vars()
    event('configuration', 'Node counts saved; plan and deploy to apply changes')
    return SETTINGS

@app.post('/api/action')
async def node_action(request: Request):
    data = await request.json()
    if data.get('node') not in inventory()['nodes'] or data.get('action') not in [*COMMANDS, 'start', 'stop', 'reboot']:
        raise HTTPException(400, 'Unknown node or action')
    if research.active: raise HTTPException(409, 'Manual actions are disabled during controlled experiments; cancel the run first')
    # During observe trials an external agent/human must remain able to repair.
    if OP['running'] and not (OP['name'] == 'Recovery trial' and data['action'] in COMMANDS):
        raise HTTPException(409, 'Wait for the active operation')
    def execute(): action(data['node'], data['action'])
    if OP['running']:
        try: execute()
        except Exception as e: raise HTTPException(502, str(e))
    else: background(f'{data["action"]}: {data["node"]}', execute)
    return {'accepted': True}

@app.get('/api/logs/{name}')
def logs(name: str):
    if name not in inventory()['nodes']: raise HTTPException(404)
    try: return {'text': ssh(name, 'sudo journalctl -u lab-workload -n 45 --no-pager', timeout=10)}
    except Exception as e: return {'text': str(e)}

@app.post('/api/load')
async def load(request: Request):
    if OP['running'] and OP['name']=='Controlled experiment': raise HTTPException(409, 'Traffic is controlled by the active experiment')
    data = await request.json()
    if type(data.get('enabled')) is not bool: raise HTTPException(400)
    LOAD['enabled'] = data['enabled']
    event('traffic', 'Traffic generator ' + ('started' if LOAD['enabled'] else 'stopped'))
    return LOAD

@app.post('/api/experiment')
async def trial(request: Request):
    data = await request.json()
    if data.get('mode') not in ['scripted', 'observe']: raise HTTPException(400)
    background('Recovery trial', lambda: experiment(data['mode']))
    return {'accepted': True}

@app.get('/api/report')
def report(): return EXPERIMENT or {'status': 'No trial has run'}

@app.get('/api/agents/{identity}/details')
def agent_details(identity: str, session: str | None = None):
    return research.agent_detail(identity, session)

@app.get('/api/model')
def model_settings(): return research.model_settings()

@app.post('/api/model')
async def save_model(request: Request):
    from urllib.parse import urlsplit
    from app.research import atomic
    if OP['running'] or any(a['status'] in ['running','starting','stopping'] for a in research.public_agents()):
        raise HTTPException(409,'Stop running agents and experiments before changing the model connection')
    data=await request.json()
    url=data.get('url','');model=data.get('model','');limit=data.get('max_tokens',512);key=data.get('api_key','')
    if not isinstance(url,str) or not isinstance(model,str) or not isinstance(key,str):raise HTTPException(400,'Invalid model settings')
    try: parsed=urlsplit(url)
    except ValueError:raise HTTPException(400,'Invalid endpoint URL')
    if parsed.scheme not in ['http','https'] or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.query:
        raise HTTPException(400,'Use an HTTP(S) chat-completions URL without embedded credentials, query parameters, or fragments')
    if not model.strip() or len(model)>200 or len(url)>2000 or len(key)>4096 or type(limit) is not int or not 1<=limit<=2048:
        raise HTTPException(400,'Provide a model name and an output limit from 1 to 2048')
    path=RUNTIME/'model.json'
    prior=json.loads(path.read_text()) if path.exists() else {}
    config={'url':url,'model':model.strip(),'max_tokens':limit}
    if key:config['api_key']=key
    elif not data.get('clear_key') and prior.get('api_key') and prior.get('url')==url:config['api_key']=prior['api_key']
    temporary=path.with_suffix('.tmp')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as output: json.dump(config,output)
    temporary.chmod(0o600);temporary.replace(path)
    return research.model_settings()

@app.get('/api/agent-guide')
def agent_guide():
    return FileResponse(ROOT/'AGENTS-GUIDE.md',media_type='text/plain')

@app.post('/api/agents')
def create_agent(spec: AgentSpec):
    return {'id': research.save_agent(spec)}

@app.post('/api/agents/{identity}/start')
def start_agent(identity: str):
    if OP['running']: raise HTTPException(409, 'Wait for the active operation')
    try: return {'session': research.start(identity)}
    except HTTPException: raise
    except Exception as e: raise HTTPException(400, str(e))

@app.post('/api/agents/{identity}/stop')
def stop_agent(identity: str):
    if research.active: raise HTTPException(409, 'Cancel the experiment to stop its participants')
    research.stop(identity)
    return {'stopped': True}

@app.post('/api/agents/{identity}/delete')
def delete_agent(identity: str):
    if OP['running']: raise HTTPException(409, 'Wait for the active operation')
    if research.sessions.get(identity,{}).get('status') in ['running','starting','stopping']:
        raise HTTPException(409, 'Stop the agent first')
    from app.research import atomic
    with research.lock:
        if identity not in research.agents: raise HTTPException(404)
        del research.agents[identity]
        atomic(research.root/'agents.json',research.agents)
    return {'removed': True}

@app.get('/api/agents/{identity}/trace')
def agent_trace(identity: str):
    session = research.sessions.get(identity)
    if not session: raise HTTPException(404, 'No session for this agent')
    return FileResponse(session['directory']/'trace.jsonl',media_type='application/x-ndjson',filename='agent-trace.jsonl')

@app.post('/api/studies')
def study(spec: Scenario):
    research.validate_scenario(spec)
    background('Controlled experiment', lambda: research.study(spec))
    return {'accepted': True}

@app.post('/api/studies/cancel')
def cancel_study():
    research.cancel.set()
    return {'accepted': True}

@app.get('/api/studies/export.csv')
def studies_csv():
    from fastapi.responses import Response
    return Response(research.csv(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="lab-runs.csv"'})

@app.get('/api/studies/{identity}/{kind}')
def study_artifact(identity: str, kind: str):
    return FileResponse(research.artifact(identity,kind),media_type='application/json' if kind=='report' else 'application/x-ndjson',filename=f'{identity}-{kind}.json' if kind=='report' else f'{identity}-trace.jsonl')

@app.get('/control-plane')
def plane_index(): return FileResponse(ROOT/'web/plane.html')

@app.get('/sandbox')
def sandbox_index(): return FileResponse(ROOT/'web/index.html')

@app.get('/')
def index(): return FileResponse(ROOT/'web/lab.html')

@app.get('/studio')
def studio_editor(): return FileResponse(ROOT/'web/studio.html')

@app.get('/api/lab/design-notes')
def design_notes(): return FileResponse(ROOT/'DESIGN.md',media_type='text/plain')

@app.get('/api/lab/mcp-guide')
def mcp_guide(): return FileResponse(ROOT/'MCP.md',media_type='text/plain')

app.mount('/static', StaticFiles(directory=ROOT/'web'), name='static')

# Final mount: the SDK handles /mcp after the application and static routes.
app.mount('/',lab_mcp.http_app)
