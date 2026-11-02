"""Real durable job control plane. PostgreSQL is authoritative; JetStream is delivery.
At-least-once execution, fenced completion, transactional outbox, bounded retries.
"""
import asyncio, hashlib, json, os, pathlib, threading, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import psycopg
from psycopg.rows import dict_row
import nats
from nats.js.api import StreamConfig, ConsumerConfig, RetentionPolicy, AckPolicy
from prometheus_client import Counter, Histogram, generate_latest
NAME=os.environ['NODE_NAME'];ROLE=os.environ['ROLE']
RELEASE=os.environ.get('PLANE_RELEASE','legacy')
FAULT=os.environ.get('PLANE_REQUEST_DEADLOCK')=='1'
WORKER_SCHEMA_BUG=os.environ.get('PLANE_WORKER_SCHEMA_BUG')=='1'
FAULT_LATCH=threading.Event();NEVER=threading.Event()
DSN=dict(host='10.78.50.11',dbname='plane',user='plane',password=pathlib.Path('/run/secrets/db_password').read_text().strip(),connect_timeout=2,options='-c statement_timeout=3000',tcp_user_timeout=5000,keepalives_idle=5,keepalives_interval=2,keepalives_count=2)
START=time.time();LAST_TICK=0.;LEADER=False;LAST_ERROR=None;DEPENDENCIES={};MUTEX=threading.Lock()
CALLS=Counter('plane_dependency_calls_total','Application calls',['target','outcome'])
LATENCY=Histogram('plane_http_request_seconds','HTTP latency',['path'],buckets=(.005,.01,.025,.05,.1,.25,.5,1,2,5))
REQUESTS=Counter('plane_http_requests_total','HTTP requests',['method','outcome'])
def record(target,ok=True):
    CALLS.labels(target,'ok' if ok else 'error').inc()
    with MUTEX:
        d=DEPENDENCIES.setdefault(target,{'calls':0,'errors':0,'last_seen':None});d['calls']+=1;d['errors']+=not ok;d['last_seen']=time.time()
def db():
    try:c=psycopg.connect(**DSN,row_factory=dict_row);record('postgres');return c
    except Exception:record('postgres',False);raise

def heartbeat():
    with db() as c:c.execute("INSERT INTO heartbeats(name,role,leader) VALUES(%s,%s,%s) ON CONFLICT(name) DO UPDATE SET leader=excluded.leader,updated_at=clock_timestamp()",(NAME,ROLE,LEADER))

def summary(c):
    counts=c.execute("SELECT state,count(*) AS count FROM jobs GROUP BY state").fetchall()
    leaders=c.execute("SELECT name,role,leader,extract(epoch FROM clock_timestamp()-updated_at) AS age FROM heartbeats ORDER BY name").fetchall()
    outbox=c.execute("SELECT count(*) AS count FROM outbox WHERE NOT published").fetchone()['count']
    duplicate=c.execute("SELECT count(*) AS count FROM (SELECT job_id,count(*) FROM completions GROUP BY job_id HAVING count(*)>1) q").fetchone()['count']
    return {'jobs':{r['state']:r['count'] for r in counts},'outbox_pending':outbox,'heartbeats':leaders,'duplicate_completions':duplicate}

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*a):pass
    def reply(self,status,body,ctype='application/json'):
        content=json.dumps(body,default=str).encode() if ctype=='application/json' else body
        self.send_response(status);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(content)));self.end_headers()
        try:self.wfile.write(content)
        except (BrokenPipeError,ConnectionResetError):pass
    def do_GET(self):
        if ROLE=='api' and FAULT_LATCH.is_set():NEVER.wait()
        if self.path=='/metrics':return self.reply(200,generate_latest(),'text/plain; version=0.0.4')
        if self.path=='/health':
            try:
                with db() as c:c.execute('SELECT 1')
                healthy=ROLE=='api' or time.time()-LAST_TICK<12
                with MUTEX:deps={k:dict(v) for k,v in DEPENDENCIES.items()}
                return self.reply(200 if healthy else 503,{'name':NAME,'role':ROLE,'release':RELEASE,'healthy':healthy,'leader':LEADER,'loop_age':round(time.time()-LAST_TICK,2),'uptime':time.time()-START,'dependencies':deps,'last_error':LAST_ERROR})
            except Exception:return self.reply(503,{'name':NAME,'healthy':False,'reason':'database unavailable'})
        if ROLE!='api':return self.reply(404,{'error':'unknown endpoint'})
        try:
            with db() as c:
                if self.path=='/summary':return self.reply(200,summary(c))
                if self.path.startswith('/jobs/'):
                    identity=str(uuid.UUID(self.path.split('/')[-1]));row=c.execute('SELECT * FROM jobs WHERE id=%s',(identity,)).fetchone()
                    return self.reply(200 if row else 404,row or {'error':'not found'})
            self.reply(404,{'error':'unknown endpoint'})
        except ValueError:self.reply(400,{'error':'invalid job id'})
        except Exception:self.reply(503,{'error':'database unavailable'})
    def do_POST(self):
        if ROLE=='api' and FAULT_LATCH.is_set():NEVER.wait()
        if ROLE!='api' or self.path!='/jobs':return self.reply(404,{'error':'unknown endpoint'})
        start=time.monotonic()
        try:
            size=int(self.headers.get('Content-Length','0'));key=self.headers.get('Idempotency-Key','')
            if not 0<size<=8192 or not 1<=len(key)<=128:raise ValueError('A bounded body and Idempotency-Key are required')
            body=json.loads(self.rfile.read(size));payload=body.get('payload')
            if not isinstance(payload,str) or len(payload)>4096:raise ValueError('payload must be text, at most 4096 characters')
            if FAULT and payload == 'incident:queue-lock':
                print(json.dumps({'event':'request_dispatch_stalled','node':NAME,'release':RELEASE}),flush=True)
                FAULT_LATCH.set();NEVER.wait()
            with db() as c:
                row=c.execute("INSERT INTO jobs(id,idempotency_key,payload) VALUES(%s,%s,%s) ON CONFLICT(idempotency_key) DO NOTHING RETURNING id",(str(uuid.uuid4()),key,payload)).fetchone()
                if row:c.execute('INSERT INTO outbox(job_id) VALUES(%s)',(row['id'],))
                else:
                    row=c.execute('SELECT id,payload FROM jobs WHERE idempotency_key=%s',(key,)).fetchone()
                    if row['payload']!=payload:return self.reply(409,{'error':'Idempotency-Key already used with a different payload'})
            REQUESTS.labels('POST','ok').inc();self.reply(202,{'id':str(row['id']),'accepted_by':NAME})
        except (ValueError,TypeError,AttributeError) as e:REQUESTS.labels('POST','error').inc();self.reply(400,{'error':str(e)})
        except Exception:REQUESTS.labels('POST','error').inc();self.reply(503,{'error':'job not accepted: database unavailable'})
        finally:LATENCY.labels('/jobs').observe(time.monotonic()-start)

async def connect_nats():
    return await nats.connect(servers=['nats://10.78.30.11:4222','nats://10.78.30.12:4222','nats://10.78.30.13:4222'],connect_timeout=2,reconnect_time_wait=1,max_reconnect_attempts=-1,ping_interval=1,max_outstanding_pings=2)

async def scheduler():
    global LEADER,LAST_TICK,LAST_ERROR
    nc=None;c=None
    while True:
        try:
            if c is None or c.closed:c=psycopg.connect(**DSN,autocommit=True,row_factory=dict_row)
            if not LEADER:LEADER=c.execute('SELECT pg_try_advisory_lock(78001) AS leader').fetchone()['leader']
            if LEADER:
                if nc is None or nc.is_closed:nc=await connect_nats()
                js=nc.jetstream(timeout=3)
                try:await js.stream_info('JOBS')
                except nats.js.errors.NotFoundError:await js.add_stream(config=StreamConfig(name='JOBS',subjects=['jobs.execute'],num_replicas=3,retention=RetentionPolicy.WORK_QUEUE,max_age=86400))
                # Expired work is fenced: the next claim receives a new token.
                with c.transaction():
                    expired=c.execute("UPDATE jobs SET state=CASE WHEN attempts>=max_attempts THEN 'failed' ELSE 'queued' END,lease_token=NULL,lease_until=NULL WHERE state='running' AND lease_until<clock_timestamp() RETURNING id,state").fetchall()
                    for row in expired:
                        if row['state']=='queued':c.execute('INSERT INTO outbox(job_id) VALUES(%s)',(row['id'],))
                rows=c.execute('SELECT id,job_id FROM outbox WHERE NOT published ORDER BY id LIMIT 20').fetchall()
                for row in rows:
                    try:
                        await js.publish('jobs.execute',json.dumps({'id':str(row['job_id'])}).encode(),headers={'Nats-Msg-Id':str(row['id'])});record('nats-cluster')
                        c.execute('UPDATE outbox SET published=true WHERE id=%s',(row['id'],))
                    except Exception:record('nats-cluster',False);raise
            heartbeat();LAST_TICK=time.time();LAST_ERROR=None
        except Exception as e:
            LAST_ERROR=type(e).__name__;LEADER=False
            if c:
                try:c.close()
                except Exception:pass
            c=None
        await asyncio.sleep(.5)

async def worker():
    global LAST_TICK,LAST_ERROR
    nc=None
    while True:
        try:
            if nc is None or nc.is_closed:nc=await connect_nats()
            js=nc.jetstream(timeout=3)
            sub=await js.pull_subscribe('jobs.execute','workers',config=ConsumerConfig(durable_name='workers',ack_policy=AckPolicy.EXPLICIT,ack_wait=20,max_ack_pending=64))
            while True:
                heartbeat();LAST_TICK=time.time();LAST_ERROR=None
                try:messages=await sub.fetch(1,timeout=1)
                except nats.errors.TimeoutError:continue
                for msg in messages:
                    if WORKER_SCHEMA_BUG:
                        # This release expects a renamed envelope field; existing
                        # producers still publish id. Preserve delivery for retry.
                        try:json.loads(msg.data)['job_id']
                        except KeyError:
                            print(json.dumps({'event':'job_decode_failed','error':'KeyError: job_id','received_fields':list(json.loads(msg.data)),'node':NAME,'release':RELEASE}),flush=True)
                            await msg.nak(delay=2)
                            continue
                    record('nats-cluster');identity=str(uuid.UUID(json.loads(msg.data)['id']));token=str(uuid.uuid4())
                    with db() as c:
                        row=c.execute("UPDATE jobs SET state='running',owner=%s,lease_token=%s,lease_until=clock_timestamp()+interval '15 seconds',attempts=attempts+1 WHERE id=%s AND state='queued' RETURNING payload",(NAME,token,identity)).fetchone()
                        if not row:status=c.execute('SELECT state FROM jobs WHERE id=%s',(identity,)).fetchone()
                    if not row:
                        if status and status['state'] in ('completed','failed'):await msg.ack_sync()
                        else:await msg.nak(delay=2)
                        continue
                    # Real bounded CPU work; no external side effects claimed exactly-once.
                    result=await asyncio.to_thread(lambda:hashlib.pbkdf2_hmac('sha256',row['payload'].encode(),b'control-plane-v1',40000).hex())
                    await asyncio.sleep(.4)
                    with db() as c:
                        won=c.execute("UPDATE jobs SET state='completed',completed_at=clock_timestamp(),result=%s WHERE id=%s AND lease_token=%s AND state='running' AND lease_until>clock_timestamp() RETURNING id",(result,identity,token)).fetchone()
                        if won:c.execute('INSERT INTO completions(job_id,worker,result) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',(identity,NAME,result))
                    if won:await msg.ack_sync()
                    else:await msg.nak(delay=1)
        except Exception as e:
            LAST_ERROR=type(e).__name__;record('nats-cluster',False)
            await asyncio.sleep(1)

def api_heartbeat():
    global LAST_TICK
    while True:
        try:heartbeat();LAST_TICK=time.time()
        except Exception:pass
        time.sleep(2)
class BoundedHTTPServer(ThreadingHTTPServer):
    # Bound requests even when the deliberate fault holds every worker.
    slots=threading.BoundedSemaphore(16)
    def process_request(self,request,address):
        request.settimeout(5)
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:super().process_request(request,address)
        except BaseException:
            self.slots.release()
            raise
    def process_request_thread(self,request,address):
        try:super().process_request_thread(request,address)
        finally:self.slots.release()

if __name__=='__main__':
    target=lambda:asyncio.run(scheduler() if ROLE=='scheduler' else worker())
    threading.Thread(target=api_heartbeat if ROLE=='api' else target,daemon=True).start()
    BoundedHTTPServer(('0.0.0.0',8080),Handler).serve_forever()
