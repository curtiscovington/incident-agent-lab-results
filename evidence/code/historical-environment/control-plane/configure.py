"""Generate deployable Compose and the desired graph from the same definitions."""
import json,pathlib,secrets,runpy
ROOT=pathlib.Path(__file__).parent
state=ROOT.parent/'.runtime/control-plane';state.mkdir(parents=True,exist_ok=True)
key=state/'db-password'
if not key.exists():key.write_text(secrets.token_urlsafe(32));key.chmod(0o600)
zones={'edge':10,'control':40,'messaging':30,'compute':20,'data':50,'observe':0}
networks={z:{'name':'cp-'+z,'driver':'bridge','internal':True,'driver_opts':{'com.docker.network.bridge.name':'cp-'+z},'ipam':{'config':[{'subnet':f'10.78.{n}.0/24','gateway':f'10.78.{n}.1','ip_range':f'10.78.{n}.128/25'}]}} for z,n in zones.items()}
services={};resources=[]
def add(name,role,zone,number,ports,extra=None,image=None):
    ip=f'10.78.{zones[zone]}.{number}';members={zone:{'ipv4_address':ip,'interface_name':'eth0'}}
    for i,(z,num) in enumerate((extra or {}).items(),1):members[z]={'ipv4_address':f'10.78.{zones[z]}.{num}','interface_name':f'eth{i}'}
    domain='a' if number%2 else 'b'
    cfg={'container_name':'cp-'+name,'image':image or 'bigspark-control-plane:1','networks':members,'restart':'unless-stopped','mem_limit':'384m','cpus':.75,'security_opt':['no-new-privileges:true'],'labels':{'lab.system':'control-plane','lab.role':role,'lab.zone':zone,'lab.domain':domain},'logging':{'driver':'json-file','options':{'max-size':'5m','max-file':'2'}}}
    if not image:cfg.update(build='.',environment={'NODE_NAME':name,'ROLE':role},secrets=['db_password'],read_only=True,tmpfs=['/tmp'],cap_drop=['ALL'],healthcheck={'test':['CMD','python','-c',"import urllib.request;urllib.request.urlopen('http://localhost:8080/health',timeout=3)"],'interval':'5s','timeout':'4s','retries':3,'start_period':'20s'},depends_on={'postgres':{'condition':'service_healthy'}})
    services[name]=cfg
    resources.append({'id':name,'name':name,'type':role,'group':zone,'primaryIp':ip,'interfaces':[{'name':v['interface_name'],'address':v['ipv4_address'],'prefix':24,'network':'cp-'+z,'gateway':f'10.78.{zones[z]}.1','allocation':'static'} for z,v in members.items()],'ports':ports,'image':cfg['image'],'domain':domain,'host':'bigspark','runtime':'container','provisioning':'Compose: control-plane/compose.json#services.'+name,'desired':'running'})
    return cfg
p=add('postgres','database','data',11,[5432],image='postgres:17-alpine');p.update(environment={'POSTGRES_DB':'plane','POSTGRES_USER':'plane','POSTGRES_PASSWORD_FILE':'/run/secrets/db_password'},secrets=['db_password'],volumes=['pg-data:/var/lib/postgresql/data','./schema.sql:/docker-entrypoint-initdb.d/01-schema.sql:ro'],healthcheck={'test':['CMD-SHELL','pg_isready -U plane -d plane'],'interval':'3s','timeout':'3s','retries':20},mem_limit='768m')
for i in range(1,4):
    n=add(f'nats-{i}','messaging','messaging',10+i,[4222,6222,8222],image='nats:2.11-alpine');n.update(command=['-c','/etc/nats/nats.conf'],volumes=[f'./nats-{i}.conf:/etc/nats/nats.conf:ro',f'nats-{i}:/data'])
    (ROOT/f'nats-{i}.conf').write_text(f'''server_name: nats-{i}
port: 4222
http_port: 8222
jetstream {{ store_dir: /data; max_file_store: 1GB; max_memory_store: 64MB }}
cluster {{ name: cp; port: 6222; routes: [nats://10.78.30.11:6222,nats://10.78.30.12:6222,nats://10.78.30.13:6222] }}
''')
for i in range(1,3):
    cfg=add(f'api-{i}','api','control',20+i,[8080],{'data':20+i})
    cfg['profiles']=['bootstrap-api']
    resources[-1]['provisioning']='Terraform: control-plane/releases#docker_container.api' 
for i in range(1,3):add(f'scheduler-{i}','scheduler','control',30+i,[8080],{'data':30+i,'messaging':30+i})
for i in range(1,4):add(f'worker-{i}','worker','compute',20+i,[8080],{'data':40+i,'messaging':40+i})
e=add('ingress','load-balancer','edge',10,[8080],{'control':10},image='nginx:1.28-alpine');e.update(volumes=['./nginx.conf:/etc/nginx/nginx.conf:ro'])
m=add('prometheus','metrics','observe',20,[9090],{z:90 for z in ['control','compute','messaging','data']},image='prom/prometheus:v3.5.0');m.update(volumes=['./prometheus.json:/etc/prometheus/prometheus.yml:ro','prom-data:/prometheus'],command=['--config.file=/etc/prometheus/prometheus.yml','--storage.tsdb.retention.time=3d'],mem_limit='512m')
(ROOT/'nginx.conf').write_text('''events { worker_connections 256; }
http {
  access_log /dev/stdout;
  upstream api_pool { server 10.78.40.21:8080 max_fails=1 fail_timeout=5s; server 10.78.40.22:8080 max_fails=1 fail_timeout=5s; }
  server { listen 8080;
    location = /health { return 200 '{"healthy":true,"role":"load-balancer"}'; add_header Content-Type application/json; }
    location / { proxy_pass http://api_pool; proxy_connect_timeout 1s; proxy_read_timeout 5s; proxy_next_upstream error timeout http_502 http_503 http_504 non_idempotent; proxy_next_upstream_tries 2; }
  }
}
''')
(ROOT/'prometheus.json').write_text(json.dumps({'global':{'scrape_interval':'5s'},'scrape_configs':[{'job_name':'plane','static_configs':[{'targets':[r['primaryIp']+':8080'],'labels':{'service':r['id'],'domain':r['domain']}} for r in resources if r['type'] in ['api','scheduler','worker']]}]},indent=2))
publication=runpy.run_path(str(ROOT/'private-access/configure.py'))['configure'](ROOT,services,resources,networks,zones,add)
compose={'name':'bigspark-plane','services':services,'networks':networks,'volumes':{n:{} for n in ['pg-data','nats-1','nats-2','nats-3','prom-data']},'secrets':{'db_password':{'file':str(key)}}}
(ROOT/'compose.json').write_text(json.dumps(compose,indent=2))
links=[]
def link(a,b,port,purpose,protocol='TCP'):
 links.append({'id':a+'>'+b+':'+str(port),'source':a,'target':b,'protocol':protocol,'port':port,'purpose':purpose,'provenance':'service configuration','policy':'not a per-port ACL; network membership permits reachability'})
for i in range(1,3):
 link('ingress',f'api-{i}',8080,'HTTP job submission / idempotency key');link(f'api-{i}','postgres',5432,'Job + outbox transaction');link(f'scheduler-{i}','postgres',5432,'Leader lock / lease recovery / outbox')
 for j in range(1,4):link(f'scheduler-{i}',f'nats-{j}',4222,'JetStream publish')
for i in range(1,4):
 link(f'worker-{i}','postgres',5432,'Claim / fenced completion')
 for j in range(1,4):link(f'worker-{i}',f'nats-{j}',4222,'Durable pull / acknowledgement')
 for j in range(i+1,4):link(f'nats-{i}',f'nats-{j}',6222,'Raft replication / cluster route')
for r in resources:
 if r['type'] in ['api','scheduler','worker']:link('prometheus',r['id'],8080,'Prometheus scrape')
for consumer in publication['consumers']:
 link(consumer,'private-access',8443,'Authenticated catalog access','HTTPS');links[-1]['policy']='Explicit consumer grant required for catalog';links[-1]['accessConnection']=consumer
link('private-access','catalog',8443,'Published catalog API','HTTPS');links[-1]['policy']='Producer credential required; fixed upstream and path only'
blueprint={'privateAccess':publication,'schemaVersion':2,'id':'control-plane','name':'Job control plane','cidr':'10.78.0.0/16','resources':resources,'groups':[{'id':z,'name':z.title(),'cidr':f'10.78.{n}.0/24','gateway':f'10.78.{n}.1','bridge':'cp-'+z,'allocation':'Static endpoints · dynamic pool .128–.254','isolation':'Internal Docker bridge; membership isolation, no per-port ACL'} for z,n in zones.items()],'relationships':links,'limits':['Single physical host; a/b are labels, not independent fault domains.','PostgreSQL and ingress are single points of failure. NATS has three replicas.','No per-port firewall or packet-level telemetry. Multi-homed clients join the networks they need.','Work can execute more than once; completion is fenced and idempotent.','Legacy sandbox agents are not authorized to control this source.']}
(ROOT/'blueprint.json').write_text(json.dumps(blueprint,indent=2))
print(f'Generated {len(resources)} services, {len(networks)} internal bridges, {len(links)} explicit dependencies.')
