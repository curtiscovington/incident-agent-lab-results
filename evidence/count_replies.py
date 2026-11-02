"""Recompute original-study reply/truncation counts from saved provider records."""
import collections,json,pathlib
ROOT=pathlib.Path(__file__).resolve().parent
def count():
 trials={r['number']:r for r in map(json.loads,(ROOT/'data/study/trials.jsonl').read_text().splitlines())}
 rows=[json.loads(line) for line in (ROOT/'data/study/model-responses.jsonl').read_text().splitlines()]
 grouped=collections.defaultdict(list);out=collections.defaultdict(collections.Counter)
 for row in rows:
  t=trials[row['trial']];assert row['condition']==t['condition'];grouped[row['trial']].append(row)
 assert set(grouped)==set(trials) and len(rows)==1696
 for trial,records in grouped.items():
  records.sort(key=lambda r:r['responseIndex']);assert [r['responseIndex'] for r in records]==list(range(1,len(records)+1))
  t=trials[trial];c=out[t['condition']];limited=[r for r in records if r['response'].get('done_reason')=='length'];failed=not t['result']['repairVerified']
  c.update(trials=1,replies=len(records),lengthReplies=len(limited),trialsWithLengthReply=int(bool(limited)),failedTrials=int(failed),failedWithTerminalLength=int(failed and records[-1]['response'].get('done_reason')=='length'))
  for r in limited:assert r['response']['prompt_eval_count']+r['response']['eval_count']==8192
 expected={'current':{'trials':80,'replies':916,'lengthReplies':5,'trialsWithLengthReply':5,'failedTrials':23,'failedWithTerminalLength':5},'runbook':{'trials':80,'replies':780,'lengthReplies':5,'trialsWithLengthReply':5,'failedTrials':13,'failedWithTerminalLength':4}}
 result={k:dict(v) for k,v in out.items()};assert result==expected;return result
if __name__=='__main__':print(json.dumps(count(),indent=2))
