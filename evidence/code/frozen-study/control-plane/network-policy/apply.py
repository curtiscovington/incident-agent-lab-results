"""Terraform provisioner: fixed API namespaces, never host routing."""
import json,pathlib,subprocess,sys
ROOT=pathlib.Path(__file__).resolve().parents[2]
HELPER='sha256:7efd45553a98be3d940b73600550669831c0be34f30c521159e7030a91fcdee6'
def apply(mode,identities):
    if mode not in ('deny','allow') or set(identities)!={'api-1','api-2'}:raise ValueError('Invalid policy scope')
    results={}
    for name,identity in identities.items():
        c=json.loads(subprocess.check_output(['docker','inspect','cp-'+name],text=True))[0]
        if c['Id']!=identity or c['Config'].get('Labels',{}).get('com.docker.compose.project')!='bigspark-plane':raise RuntimeError('API namespace changed')
        results[name]=json.loads(subprocess.check_output(['docker','run','--rm','--network','container:'+identity,'--cap-drop','ALL','--cap-add','NET_ADMIN','--security-opt','no-new-privileges:true','--read-only','--pids-limit','32','--memory','64m','--mount','type=bind,src='+str(ROOT/'control-plane/network-policy/route.py')+',dst=/route.py,readonly',HELPER,'python','/route.py',mode],text=True,timeout=20))
    return results
if __name__=='__main__':
    config=json.loads((pathlib.Path(__file__).parent/'policy.auto.tfvars.json').read_text())
    print(json.dumps(apply('deny' if config['denied'] else 'allow',config['api_ids'])))
