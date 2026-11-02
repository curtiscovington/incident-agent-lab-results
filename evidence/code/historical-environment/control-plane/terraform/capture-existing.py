"""Capture identities for inventory-only adoption; never changes Docker objects."""
import json
import pathlib
import subprocess
ROOT=pathlib.Path(__file__).resolve().parents[2]
compose=json.loads((ROOT/'control-plane/compose.json').read_text())
def inspect(kind, name):
    return json.loads(subprocess.check_output(['docker',kind,'inspect',name]))[0]
containers={}
for name, service in compose['services'].items():
    if name in {'api-1','api-2'}:continue  # Owned by control-plane/releases.
    value=inspect('container',service['container_name'])
    containers[name]={'id':value['Id'],'image':value['Image']}
networks={}
for name, network in compose['networks'].items():
    value=inspect('network',network['name'])
    networks[name]={'id':value['Id'],'name':value['Name']}
volumes={}
for name in compose['volumes']:
    value=inspect('volume',compose['name']+'_'+name)
    volumes[name]=value['Name']
path=pathlib.Path(__file__).with_name('existing.auto.tfvars.json')
path.write_text(json.dumps({'existing_containers':containers,'existing_networks':networks,'existing_volumes':volumes},indent=2))
path.chmod(0o600)
print('Captured container, network and volume identities; no resources changed.')
