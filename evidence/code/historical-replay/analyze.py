"""Descriptive post-hoc summaries; never calls an inference endpoint."""
import collections,json,pathlib,statistics
ROOT=pathlib.Path(__file__).resolve().parent
RUN=ROOT/'runs/replay-20261101-v1'
def analyze():
 state=json.loads((RUN/'state.json').read_text());rows=[json.loads(p.read_text()) for p in sorted((RUN/'results').glob('*.json'))]
 arms=[];strata=[]
 for arm in ('baseline','evidence_replan','format_retry','both'):
  g=[r for r in rows if r['arm']==arm];attempts=[a for r in g for a in r['attempts']]
  arms.append({'arm':arm,'decisions':len(g),'initialValid':sum(r['initialValid'] for r in g),'finalValid':sum(r['finalValid'] for r in g),'actionCategories':dict(collections.Counter(r['score']['category'] for r in g)),'repeatedToolTarget':sum(r['score']['repeatToolTarget'] for r in g),'modelCalls':len(attempts),'conditionalRetries':sum(len(r['attempts'])-1 for r in g),'totalModelSeconds':sum(a['totalDurationNs'] for a in attempts)/1e9,'promptTokens':sum(a['promptTokens'] for a in attempts),'outputTokens':sum(a['outputTokens'] for a in attempts),'lengthLimitedResponses':sum(a['doneReason']=='length' for a in attempts),'contextBoundaryStops':sum(a['doneReason']=='length' and a['promptTokens']+a['outputTokens']==8192 for a in attempts)})
  for stratum in ('db_lock','deployment','truncation','success_control'):
   s=[r for r in g if r['stratum']==stratum];strata.append({'arm':arm,'stratum':stratum,'n':len(s),'categories':dict(collections.Counter(r['score']['category'] for r in s))})
 paired=[]
 for case in sorted({r['case'] for r in rows}):
  g={r['arm']:r for r in rows if r['case']==case}
  if len(g)==4:paired.append({'case':case,'stratum':next(iter(g.values()))['stratum'],'actions':{k:v['decision'] for k,v in g.items()},'categories':{k:v['score']['category'] for k,v in g.items()},'initialPromptTokens':{k:v['attempts'][0]['promptTokens'] for k,v in g.items()}})
 result={'id':state['id'],'status':state['status'],'savedDecisions':len(rows),'inferenceCallsFromSavedResponses':sum(a['modelCalls'] for a in arms),'inferenceCallsCounter':state['inferenceCalls'],'arms':arms,'strata':strata,'pairedCases':paired,'limitations':['Descriptive development action rubric, not human diagnosis or live recovery.','Outcome-aware selected cases and teacher-forced histories; no independent generalization.','Evidence/replanning intervention also changes retained context and prompt length; no mechanism attribution.','Initial format_retry and baseline requests are identical, as are both/evidence_replan requests; cache/order affects timing.','Unlisted actions require review and are not automatically wrong; repeated checks can be legitimate.','All ten original length-limited responses hit prompt+output=8192; response capacity and history policy need explicit testing.']}
 assert result['inferenceCallsFromSavedResponses']<=120
 if state['status']=='complete':
  assert len(rows)==80 and all(a['decisions']==20 for a in arms)
  assert result['inferenceCallsCounter']==result['inferenceCallsFromSavedResponses']
 (RUN/'analysis.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k not in ('pairedCases','strata')},indent=2))
if __name__=='__main__':analyze()
