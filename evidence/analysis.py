"""Offline evidence reconciliation. Standard library only; no inference or tools."""
import ast,collections,copy,csv,hashlib,json,pathlib,sys
ROOT=pathlib.Path(__file__).resolve().parent
def read(path):return json.loads(path.read_text())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def main():
 manifest=read(ROOT/'manifest.json')
 expected={item['path'] for item in manifest['files']}
 actual={str(path.relative_to(ROOT)) for path in ROOT.rglob('*') if path.is_file()}-{'manifest.json','SHA256SUMS'}
 assert actual==expected,{'unexpected':sorted(actual-expected),'missing':sorted(expected-actual)}
 for item in manifest['files']:
  path=ROOT/item['path'];assert sha(path)==item['sha256'],item['path']
 design={};exec(compile((ROOT/'code/frozen-study/app/recovery_design.py').read_text(),'frozen-recovery-design','exec'),design)
 trials=[json.loads(line) for line in (ROOT/'data/study/trials.jsonl').read_text().splitlines()]
 assert len(trials)==160 and len({r['number'] for r in trials})==160
 summary=design['summary'](trials);assert summary==read(ROOT/'data/study/summary.json')
 totals={condition:sum(row['passed'] for row in summary['cells'] if row['condition']==condition) for condition in ('current','runbook')}
 # Compile only the frozen pure validator/scorer functions, never imports or
 # runner entry points. This also works on systems without Linux fcntl.
 module=ast.parse((ROOT/'code/historical-replay/replay.py').read_text())
 names={'match','validate_decision','score','arm_messages'};pure=ast.Module(body=[n for n in module.body if isinstance(n,ast.FunctionDef) and n.name in names],type_ignores=[])
 namespace={'json':json,'copy':copy};exec(compile(pure,'historical-pure-functions','exec'),namespace)
 reports={}
 for version in ('v1','v2'):
  folder=ROOT/'data'/('replay-'+version);protocol=read(folder/'protocol.json');cases={c['id']:c for c in read(folder/'cases.json')};rows=[read(p) for p in sorted((folder/'results').glob('*.json'))]
  assert len(cases)==20 and len(rows)==80 and {(r['case'],r['arm']) for r in rows}=={(r['case'],r['arm']) for r in protocol['queue']}
  assert len({(r['case'],r['arm']) for r in rows})==80
  for row in rows:
   case=cases[row['case']]
   for attempt in row['attempts']:
    response=read(folder/attempt['responseFile']);decision,error=namespace['validate_decision'](response,protocol['schema'])
    assert (decision is not None)==attempt['schemaValid']
    assert response['message']['content']==attempt['responseText']
    assert response['prompt_eval_count']==attempt['promptTokens'] and response['eval_count']==attempt['outputTokens']
    assert response['total_duration']==attempt['totalDurationNs']
    request=read(folder/attempt['responseFile'].replace('.response.json','.request.json'))
    assert request['options']==protocol['options'] and request['format']==protocol['schema']
    if attempt['attempt']==0:
     expected=namespace['arm_messages'](case,row['arm'],protocol) if version=='v1' else case['messagesByArm'][row['arm']]
     assert request['messages']==expected
   final,error=namespace['validate_decision'](read(folder/row['attempts'][-1]['responseFile']),protocol['schema'])
   assert final==row['decision'] and (final is not None)==row['finalValid'] and namespace['score'](case,final)==row['score']
   if version=='v2' and row['budgeted']:assert row['actualPromptTokens']<=7002 and abs(row['tokenCountDelta'])<=2 and row['reservedHeadroom']>=1190
  arms=[];original=read(folder/'analysis.json')
  for arm in protocol['arms']:
   group=[r for r in rows if r['arm']==arm];assert len(group)==20
   categories=dict(collections.Counter(r['score']['category'] for r in group));valid=sum(r['finalValid'] for r in group)
   old=next(x for x in original['arms'] if x['arm']==arm)
   assert valid==old['finalValid' if version=='v1' else 'valid']
   assert categories==old['actionCategories' if version=='v1' else 'categories']
   strata={s:sum(r['score']['category']=='preferred' for r in group if r['stratum']==s) for s in ('db_lock','deployment','truncation','success_control')}
   arms.append({'arm':arm,'n':20,'valid':valid,'categories':categories,'preferredByStratum':strata,'calls':sum(len(r['attempts']) for r in group)})
  calls=sum(a['calls'] for a in arms);assert calls==len(list((folder/'attempts').glob('*.response.json')))==read(folder/'completion.json')['inferenceCalls']
  if version=='v2':
   for c in cases.values():
    a=c['messagesByArm']['facts_time'];b=c['messagesByArm']['facts_entity'];assert a[:2]==b[:2]
    facts=lambda m:sorted(json.dumps(x,sort_keys=True) for x in json.loads(m[-1]['content'].split('\n',1)[1])['prior_observations'])
    assert facts(a)==facts(b)
    pair={r['arm']:r for r in rows if r['case']==c['id']};assert abs(pair['facts_time']['actualPromptTokens']-pair['facts_entity']['actualPromptTokens'])<=8
  reports[version]={'decisions':len(rows),'modelCalls':calls,'arms':arms}
 cutoff=read(ROOT/'evidence/t158-s07/response.json');row=read(ROOT/'evidence/t158-s07/result.json')
 assert cutoff['prompt_eval_count']==8173 and cutoff['eval_count']==19 and cutoff['done_reason']=='length'
 assert cutoff['message']['content']==row['attempts'][0]['responseText'] and 8173+19==8192 and row['offlinePromptTokens']==10397
 result={'integrity':'all manifest files verified','study':{'trials':160,'currentVerified':totals['current'],'runbookVerified':totals['runbook'],'denominatorPerCondition':80},'replays':reports,'cutoff':{'recordedPromptTokens':8173,'recordedOutputTokens':19,'contextTokens':8192,'configuredOutputCap':384,'offlinePromptEstimate':10397,'doneReason':'length','exactResponse':cutoff['message']['content']},'scope':'Re-analysis of saved data only; no inference, repair tools or live incident rerun.'}
 # Default is read-only. An explicit destination can be supplied for comparison.
 if len(sys.argv)>1:pathlib.Path(sys.argv[1]).write_text(json.dumps(result,indent=2))
 print(json.dumps(result,indent=2))
if __name__=='__main__':main()
