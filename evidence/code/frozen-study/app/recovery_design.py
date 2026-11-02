"""Frozen repeated-study ordering and descriptive, conditional uncertainty."""
import math,random
SCENARIOS=('api-regression','worker-backlog','database-route','database-lock','scheduler-outage','scheduler-lease','broker-quorum','worker-loss')
SEED=20261028

def queue():
    rng=random.Random(SEED);result=[]
    # Each scenario has five blocks with each condition first. Both members of
    # a pair use the same persistent fixture; each gets a separate restoration.
    orders={s:rng.sample(range(10),5) for s in SCENARIOS}
    for block in range(10):
        cases=list(SCENARIOS);rng.shuffle(cases)
        for scenario in cases:
            conditions=('current','runbook') if block in orders[scenario] else ('runbook','current')
            for condition in conditions:result.append({'block':block+1,'scenario':scenario,'condition':condition})
    return result

def wilson(successes,n):
    if not n:return None
    z=1.959963984540054;p=successes/n;den=1+z*z/n
    center=(p+z*z/(2*n))/den;half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return [max(0,center-half),min(1,center+half)]

def summary(trials):
    rows=[]
    for scenario in SCENARIOS:
        for condition in ('current','runbook'):
            group=[t for t in trials if t['scenario']==scenario and t['condition']==condition]
            known=[t for t in group if type(t['result'].get('repairVerified')) is bool]
            passed=sum(t['result']['repairVerified'] for t in known)
            rows.append({'scenario':scenario,'condition':condition,'planned':10,'recorded':len(group),'evaluated':len(known),'passed':passed,'failed':len(known)-passed,'interrupted':len(group)-len(known),'remaining':10-len(group),'rate':passed/len(known) if known else None,'wilson95':wilson(passed,len(known)),'fullScheduleRateBounds':[passed/10,(passed+10-len(known))/10]})
    return {'cells':rows,'note':'Descriptive 95% Wilson intervals conditional on evaluated trials and this fixed Lab setup. Repetitions share faults, fixture and temperature-zero model; these are not bounds on general incident reliability. Interrupted trials are shown separately; partial results are not final comparisons.'}
