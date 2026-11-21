"""Isolated, durable qualification trials; never admit results to the archive.

Requests use the production builder and existing shared spending ledger. A trial
file commits request/reservation identity before sending. Ambiguous attempts are
never automatically resent; raw responses and failed attempts remain inspectable.
"""

from datetime import datetime, timezone
import fcntl
from functools import wraps
import json
from pathlib import Path
import uuid

import httpx

from toronto_bids.claude import _charge, api_headers, parse_response, request_allowance
from toronto_bids.extract import EXTRACTOR_VERSION
from toronto_bids.llm_budget import BudgetExceeded
from toronto_bids.qualification import digest, identity, load_cohort, request_prompt_prefix


API = 'https://api.anthropic.com/v1/messages'


def _locked(fn):
    @wraps(fn)
    def run(path, **kwargs):
        with Path(str(path) + '.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fn(path, **kwargs)
    return run


def _write(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    temporary.replace(path)


def prepare_trials(cohort_path, output_dir, *, request_factory, campaign_id):
    """Offline preparation. Existing attempts cannot be overwritten or reused as repeats."""
    cohort_path, output_dir = Path(cohort_path), Path(output_dir)
    cohort = load_cohort(cohort_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / (campaign_id + '.cohort')).open('x') as stream:
        stream.write(cohort_path.read_text())
    paths = []
    for mode in ('sync', 'batch'):
        for repeat in (1, 2):
            trial = {'id': f'{campaign_id}-{mode}-{repeat}', 'campaign': campaign_id,
                     'mode': mode, 'repeat': repeat, 'attempts': {},
                     'cohort_sha256': digest(cohort_path.read_bytes())}
            for doc in cohort['documents']:
                if repeat == 2 and not doc.get('difficult'):
                    # Not selected for this repeat: coverage still records the
                    # absent input. Campaign qualification uses explicit required
                    # attempt membership rather than treating it as a failure.
                    continue
                text = (cohort_path.parent / doc['text_path']).read_text()
                request = request_factory(text)
                trial['attempts'][doc['id']] = {
                    'state': 'pending', 'reason': 'prepared; no request sent', 'dispatch': 'prepared',
                    'provenance': {'document_sha256': doc['document_sha256'],
                                   'text_sha256': doc['text_sha256'], 'prompt_version': EXTRACTOR_VERSION,
                                   'model': request['model'], 'effort': request['output_config']['effort'],
                                   'mode': mode, 'request': request, 'request_sha256': identity(request),
                                   'schema_sha256': identity(request['output_config']['format']['schema'])},
                }
                from toronto_bids.extract import build_prompt

                prefix = request['messages'][0]['content'][0]['text']
                if prefix != build_prompt(''):
                    p = trial['attempts'][doc['id']]['provenance']
                    p['experimental_prompt'] = {'base_version': EXTRACTOR_VERSION, 'prefix': prefix,
                                                'sha256': digest(prefix.encode())}
                    p['prompt_version'] = 'eval-' + digest(prefix.encode())[:12]
                    request_prompt_prefix(p)
            trial['selected_documents'] = list(trial['attempts'])
            path = output_dir / (trial['id'] + '.json')
            # Exclusive create prevents replacing any previous paid attempt.
            with path.open('x') as stream:
                stream.write(json.dumps(trial, indent=2, ensure_ascii=False) + '\n')
            paths.append(path)
    return paths


def _load(path, mode):
    trial = json.loads(Path(path).read_text())
    if trial.get('mode') != mode:
        raise ValueError(f'Trial is not {mode}')
    for a in trial['attempts'].values():
        p = a['provenance']
        prefix = request_prompt_prefix(p)
        content = p['request']['messages'][0]['content']
        if (p['request_sha256'] != identity(p['request']) or content[0].get('text') != prefix
                or digest(''.join(b['text'] for b in content[1:]).encode()) != p['text_sha256']):
            raise ValueError('Prepared request changed; create a new trial')
    return trial


def _reserve(attempt, budget):
    p = attempt['provenance']
    allowance = request_allowance(p['request'], batch=p['mode'] == 'batch')
    attempt.update(reservation=budget.reserve(p['model'], allowance), allowance_microdollars=allowance,
                   dispatch='uncertain', reason='request reserved; outcome not yet known')


def _response(attempt, message, budget):
    """Charge valid usage before validating extraction, including truncated outputs."""
    attempt['raw_response'] = message
    p = attempt['provenance']
    try:
        if message.get('model') != p['model']:
            raise ValueError('Response model differs from reserved model')
        usage = message.get('usage')
        charge = _charge(p['model'], usage)
        if p['mode'] == 'batch':
            charge = (charge + 1) // 2
        budget.settle(attempt['reservation'], charge, 'reported', usage)
        attempt.update(usage=usage, charged_microdollars=charge)
        result = parse_response(message)
    except (AttributeError, ValueError, TypeError, KeyError) as exc:
        attempt.update(state='failed', dispatch='received', reason=str(exc))
    else:
        attempt.update(state='success', dispatch='received', reason=None, extraction=result)
        p.update(response_id=message.get('id'), response_sha256=identity(result))


@_locked
def run_sync(path, *, budget, api_key=None, http=httpx, timeout=120):
    headers = api_headers(api_key)
    trial = _load(path, 'sync')
    for a in trial['attempts'].values():
        if a['dispatch'] != 'prepared':
            continue
        _reserve(a, budget)
        _write(path, trial)
        try:
            response = http.post(API, json=a['provenance']['request'], headers=headers, timeout=timeout)
            response.raise_for_status()
            # Preserve the complete response before any local parsing failure.
            a['raw_response'] = response.json()
            _write(path, trial)
            _response(a, a['raw_response'], budget)
        except httpx.HTTPStatusError as exc:
            if 400 <= exc.response.status_code < 500:
                budget.settle(a['reservation'], 0, 'rejected')
                a.update(state='failed', dispatch='rejected', reason=f'HTTP {exc.response.status_code}',
                         charged_microdollars=0)
            else:
                a['reason'] = f'HTTP {exc.response.status_code}; billing outcome unknown'
            _write(path, trial)
            raise
        except (httpx.TransportError, ValueError) as exc:
            a['reason'] = f'{type(exc).__name__}; billing outcome unknown'
            _write(path, trial)
            raise
        _write(path, trial)
    return trial


@_locked
def submit_batch(path, *, budget, api_key=None, http=httpx):
    headers = api_headers(api_key)
    trial = _load(path, 'batch')
    if trial.get('submission'):
        raise ValueError('Batch already has a durable submission; collect or investigate, never resend')
    if any(a['dispatch'] != 'prepared' for a in trial['attempts'].values()):
        raise ValueError('Batch has prior reservations; investigate before submission')
    submission = {'id': uuid.uuid4().hex, 'state': 'reserving',
                  'created_at': datetime.now(timezone.utc).isoformat()}
    trial['submission'] = submission
    _write(path, trial)
    requests = []
    try:
        for doc_id, a in trial['attempts'].items():
            _reserve(a, budget)
            a['custom_id'] = identity([submission['id'], doc_id])
            requests.append({'custom_id': a['custom_id'], 'params': a['provenance']['request']})
            _write(path, trial)
    except BudgetExceeded:
        submission['state'] = 'not_submitted'
        # No POST has been attempted: these allowances are known to be unbilled.
        for a in trial['attempts'].values():
            if a.get('reservation'):
                budget.settle(a['reservation'], 0, 'rejected')
            a.update(state='failed', dispatch='not_submitted', charged_microdollars=0,
                     reason='batch budget unavailable; no POST sent')
        _write(path, trial)
        raise
    submission['state'] = 'uncertain'
    _write(path, trial)
    try:
        response = http.post(API + '/batches', json={'requests': requests}, headers=headers, timeout=120)
        response.raise_for_status()
        data = response.json()
        from toronto_bids.batch_extraction import _remote_id

        submission.update(remote_id=_remote_id(data.get('id')), state='submitted', receipt=data)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (400, 401, 402, 403, 404, 413, 422, 429):
            for a in trial['attempts'].values():
                budget.settle(a['reservation'], 0, 'rejected')
                a.update(state='failed', dispatch='rejected', reason=f'HTTP {exc.response.status_code}',
                         charged_microdollars=0)
            submission['state'] = 'rejected'
        _write(path, trial)
        raise
    except (httpx.TransportError, ValueError):
        _write(path, trial)
        raise
    _write(path, trial)
    return submission


@_locked
def collect_batch(path, *, budget, api_key=None, http=httpx):
    trial = _load(path, 'batch')
    submission = trial['submission']
    if submission['state'] == 'ended':
        return trial
    if not submission.get('remote_id'):
        raise ValueError('Unknown submission receipt; do not resend this trial')
    from toronto_bids.batch_extraction import _remote_id

    remote_id = _remote_id(submission['remote_id'])
    url = API + '/batches/' + remote_id
    response = http.get(url, headers=api_headers(api_key), timeout=120)
    response.raise_for_status()
    data = response.json()
    if data.get('id') != remote_id:
        raise ValueError('Retrieved batch identity differs')
    if data.get('processing_status') in ('in_progress', 'canceling'):
        return trial
    if data.get('processing_status') != 'ended':
        raise ValueError('Unknown batch processing status')
    response = http.get(url + '/results', headers=api_headers(api_key), timeout=120)
    response.raise_for_status()
    submission['raw_results'] = response.text
    _write(path, trial)
    lines = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    manifest = {a['custom_id']: a for a in trial['attempts'].values()}
    ids = [line.get('custom_id') for line in lines]
    if len(ids) != len(set(ids)) or set(ids) != set(manifest):
        raise ValueError('Missing, duplicate or foreign batch receipt membership')
    for line in lines:
        a = manifest[line['custom_id']]
        if a['dispatch'] == 'received':
            continue
        result = line['result']
        if result['type'] == 'succeeded':
            _response(a, result['message'], budget)
        elif result['type'] in ('errored', 'expired', 'canceled'):
            budget.settle(a['reservation'], 0, 'rejected')
            a.update(state='failed', dispatch='received', charged_microdollars=0, reason=result['type'])
        else:
            a.update(state='failed', reason='Unknown result type; billing remains reserved')
        _write(path, trial)
    submission['state'] = 'ended'
    _write(path, trial)
    return trial


def main(argv=None):
    """Explicit paid actions are separate from offline preparation/reporting."""
    import argparse
    import os
    from toronto_bids.claude import DEFAULT_MODEL, extraction_request
    from toronto_bids.llm_budget import UsageBudget

    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    prepare = sub.add_parser('prepare', help='Offline: preserve exact production requests')
    prepare.add_argument('--cohort', required=True)
    prepare.add_argument('--out-dir', required=True)
    prepare.add_argument('--campaign', required=True)
    prepare.add_argument('--model', default=DEFAULT_MODEL)
    prepare.add_argument('--effort', default='high', choices=['low', 'medium', 'high', 'xhigh', 'max'])
    for action in ('sync', 'submit', 'collect'):
        p = sub.add_parser(action)
        p.add_argument('trial')
        p.add_argument('--ledger', required=True, help='Existing shared operational spending ledger')
    args = parser.parse_args(argv)
    if args.action == 'prepare':
        paths = prepare_trials(args.cohort, args.out_dir, campaign_id=args.campaign,
                               request_factory=lambda text: extraction_request(text, model=args.model, effort=args.effort))
        print(json.dumps({'trials': [str(p) for p in paths]}))
        return 0
    if not Path(args.ledger).is_file():
        parser.error('--ledger must identify the existing operational spending ledger')
    budget = UsageBudget(Path(args.ledger), os.environ.get('TB_LLM_MONTHLY_USD', '20'))
    if args.action == 'sync':
        result = run_sync(args.trial, budget=budget)
    elif args.action == 'submit':
        result = submit_batch(args.trial, budget=budget)
        print(json.dumps(result))
        return 2
    else:
        result = collect_batch(args.trial, budget=budget)
    states = [a['state'] for a in result['attempts'].values()]
    print(json.dumps({'id': result['id'], 'states': {s: states.count(s) for s in sorted(set(states))}}))
    return 1 if 'failed' in states else 2 if 'pending' in states else 0


if __name__ == '__main__':
    raise SystemExit(main())
