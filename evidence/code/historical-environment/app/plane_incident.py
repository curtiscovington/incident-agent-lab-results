"""Read-only incident readiness: metadata probes, never inference or recovery."""
import os
import time

MODEL = os.environ.get('LAB_INCIDENT_MODEL', 'qwen3.8:27b-q4_K_M')
ENDPOINT = os.environ.get('LAB_INCIDENT_MODEL_URL', 'http://127.0.0.1:11438').rstrip('/')


def model_status(fetch):
    result = {'name': MODEL, 'endpoint': ENDPOINT, 'state': 'unknown',
              'capturedAt': time.time(), 'source': 'Ollama /api/tags + /api/ps',
              'agentState': 'not connected', 'recoveryEnabled': False}
    try:
        tags = fetch(ENDPOINT + '/api/tags', timeout=1.5)['models']
        installed = next((m for m in tags if m.get('name') == MODEL), None)
        if installed:
            result.update(digest=installed.get('digest'), details=installed.get('details'),
                          sizeBytes=installed.get('size'), state='installed')
        else:
            result['state'] = 'not installed'
        loaded = fetch(ENDPOINT + '/api/ps', timeout=1.5)['models']
        resident = next((m for m in loaded if m.get('name') == MODEL), None)
        if resident:
            result.update(state='loaded', expiresAt=resident.get('expires_at'),
                          memoryBytes=resident.get('size_vram'))
        result['detail'] = ('Installed on disk; currently unloaded. ' if result['state'] == 'installed' else '') + 'Metadata only; inference readiness has not been tested.'
    except Exception:
        result.update(state='unknown', detail='Model metadata probe failed; availability is unknown.')
    return result


def readiness(snapshot, model, now=None):
    now = time.time() if now is None else now
    age = now - snapshot.get('capturedAt', 0)
    stale = bool(snapshot.get('collectionError')) or age > 30
    resources = snapshot.get('resources', [])
    alarms = []
    if stale or not resources:
        alarms.append({'id': 'telemetry', 'severity': 'warning', 'resource': 'Health monitor',
                       'detail': 'Health observations unavailable or stale; last readings are not current.'})
    else:
        for r in resources:
            state = r.get('measured', {}).get('state', 'unknown')
            if r.get('observed') != 'running' or state != 'healthy':
                alarms.append({'id': 'health:' + r['id'], 'severity': 'warning' if state == 'unknown' and r.get('observed') == 'running' else 'critical',
                               'resource': r['name'], 'detail': f"{r.get('observed', 'unknown')} · {state}",
                               'source': r.get('measured', {}).get('source', 'No probe')})
    model = dict(model or {'name': MODEL, 'endpoint': ENDPOINT, 'state': 'unknown', 'agentState': 'not connected'})
    if now - model.get('capturedAt', 0) > 30:
        model.update(state='unknown', detail='Model observations are stale.')
    return {'model': model, 'agent': snapshot.get('incidentAgent'), 'alarms': alarms, 'stale': stale,
            'health': 'unknown' if stale or not resources else 'degraded' if alarms else 'healthy',
            'healthyCount': sum(r.get('measured', {}).get('state') == 'healthy' for r in resources),
            'totalCount': len(resources), 'capturedAt': snapshot.get('capturedAt'),
            'alarmRule': 'One current failed health probe or non-running container raises a health alarm. Unknown probes raise a warning. Stale means no successful collection for 30 seconds.',
            'automation': 'Not connected · investigation and rollback are not enabled',
            'scope': 'Live health alarms only; deployment regression rules and incident history are not configured.'}
