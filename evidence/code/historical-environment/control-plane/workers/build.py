import json,pathlib,subprocess
root=pathlib.Path(__file__).resolve().parents[2];data=root/'.runtime/plane-diagnosis';data.mkdir(parents=True,exist_ok=True)
basefile=data/'build-base.json'
if basefile.exists():base=json.loads(basefile.read_text())['image']
else:
 base=subprocess.check_output(['docker','inspect','cp-worker-1','--format','{{.Image}}'],text=True).strip();basefile.write_text(json.dumps({'image':base}))
images={}
subprocess.run(['docker','tag',base,'lab-worker-base:pinned'],check=True)
for name,bug in [('w1','0'),('w2','1')]:
 tag='lab-control-plane-worker:'+name
 subprocess.run(['docker','build','-f',str(root/'control-plane/workers/Dockerfile'),'--build-arg','BASE=lab-worker-base:pinned','--build-arg','RELEASE='+name,'--build-arg','SCHEMA_BUG='+bug,'-t',tag,str(root/'control-plane')],check=True,stdout=subprocess.DEVNULL)
 images[name]={'image':subprocess.check_output(['docker','image','inspect',tag,'--format','{{.Id}}'],text=True).strip(),'tag':tag}
previous=json.loads((data/'images.json').read_text()) if (data/'images.json').exists() else {}
if any(name in previous and previous[name]!=value for name,value in images.items()):
 for value in previous.values():subprocess.run(['docker','tag',value['image'],value['tag']],check=True)
 raise SystemExit('Existing worker release IDs would change; use new version names.')
(data/'images.json').write_text(json.dumps(images,indent=2))
(root/'control-plane/workers/deployment.auto.tfvars.json').write_text(json.dumps({'apps':{n:{'image':images['w1']['image'],'release':'w1'} for n in ('worker-1','worker-2','worker-3')}},indent=2))
print(json.dumps(images))
