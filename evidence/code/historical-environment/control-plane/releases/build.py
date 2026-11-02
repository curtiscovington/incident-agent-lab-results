"""Build release images for the actual job API, using its installed dependencies."""
import hashlib,json,pathlib,subprocess
ROOT=pathlib.Path(__file__).resolve().parents[2]
DATA=ROOT/'.runtime/control-plane-releases';DATA.mkdir(parents=True,exist_ok=True)
basefile=DATA/'build-base.json'
if basefile.exists():base=json.loads(basefile.read_text())['image']
else:
    base=subprocess.check_output(['docker','inspect','cp-api-1','--format','{{.Image}}'],text=True).strip()
    basefile.write_text(json.dumps({'image':base}))
subprocess.run(['docker','tag',base,'lab-plane-base:pinned'],check=True)
releases={}
for release,fault in [('r3','0'),('r4','1')]:
    tag='lab-control-plane-api:'+release
    subprocess.run(['docker','build','-f',str(ROOT/'control-plane/releases/Dockerfile'),'--build-arg','RELEASE='+release,'--build-arg','DEADLOCK='+fault,'-t',tag,str(ROOT/'control-plane')],check=True)
    image=subprocess.check_output(['docker','image','inspect',tag,'--format','{{.Id}}'],text=True).strip()
    releases[release]={'image':image,'tag':tag}
file=DATA/'releases.json'
previous=json.loads(file.read_text())['releases'] if file.exists() else {}
for name,value in releases.items():
    if name in previous and previous[name]!=value:raise SystemExit('Existing release IDs changed; use new release names')
previous.update(releases)
file.write_text(json.dumps({'releases':previous,'available':['r3','r4'],'baseline':'r3','candidate':'r4','base':base,'sourceSha256':hashlib.sha256((ROOT/'control-plane/service.py').read_bytes()).hexdigest()},indent=2))
print(file)
