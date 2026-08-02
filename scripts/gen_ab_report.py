"""Generate P3 A/B loop report as markdown."""
import json, os

def extract(path):
    with open(path) as f: data = json.load(f)
    trace = data.get('retrieval_trace', {})
    events = trace.get('events', [])
    funnel = data.get('evidence_role_funnel', {})
    
    focuses = []; primary_qs = []; fallback_qs = []; replan_qs = []; loop_info = []
    
    for e in events:
        if e.get('event') == 'planner_focus':
            focuses.append({'id': e['focus'], 'text': e['objective']})
        if e.get('event') == 'query':
            qtext = e.get('api_query') or e.get('query','')
            q = {'text': qtext, 'type': e.get('query_type',''), 'loop': e.get('loop','-')}
            if e.get('replan'): replan_qs.append(q)
            elif e.get('fallback'):
                q['origin'] = e.get('fallback_origin','')
                fallback_qs.append(q)
            else: primary_qs.append(q)
        if e.get('event') == 'plan_eval':
            loop_info.append({'loop': e.get('loop'), 'raw': e.get('raw_decision','?'),
                'dec': e.get('decision','?'), 'forced': e.get('forced_stop_by_io_gate'),
                'io_sat': e.get('io_saturated'), 'replan_count': e.get('replan_query_count',0)})
        if e.get('event') == 'plan_eval_started':
            loop_info.append({'loop': e.get('loop'), 'phase': 'started'})
    
    roles = []
    for r in funnel.get('roles', []):
        roles.append({'name': r.get('role','')[:65], 'passed': r.get('source_retained', False),
                       'missing': r.get('first_missing_stage','') or 'final'})
    
    pfbs = sum(1 for e in events if e.get('event')=='profile_fallback')
    cands = sum(1 for e in events if e.get('event')=='candidate')
    return {'focuses':focuses, 'primary_queries':primary_qs, 'fallback_queries':fallback_qs,
            'replan_queries':replan_qs, 'loop_info':loop_info, 'roles':roles, 'pfbs':pfbs, 'cands':cands}

d_on = 'tests/artifacts/p3_scope_first_v2_runs_final_ab_20260726'
d_off = 'tests/artifacts/p3_scope_first_v2_runs_full_20260725'

L = []
L.append('# P3 Case A/B Report — Profile ON vs OFF')
L.append('')
L.append('> Generated: 2026-07-26 | All deep_dive max_loops=2')
L.append('')

for cid in ['v2_pilot_0'+str(i) for i in range(1,9)]:
    on_fn = os.path.join(d_on, f'{cid}__run1__deep_dive.json')
    off_fn = os.path.join(d_off, f'{cid}__run1__deep_dive.json')
    on_data = extract(on_fn) if os.path.exists(on_fn) else None
    off_data = extract(off_fn) if os.path.exists(off_fn) else None
    
    L.append(f'## {cid}')
    L.append('')
    
    for label, data in [('OFF (baseline)', off_data), ('ON (Profile + eval)', on_data)]:
        if not data:
            L.append(f'### {label}: MISSING')
            L.append('')
            continue
        
        n_focus = len(data['focuses'])
        n_pq = len(data['primary_queries'])
        passed = sum(1 for r in data['roles'] if r['passed'])
        total = len(data['roles'])
        
        L.append(f'### {label}')
        L.append('')
        L.append(f'Focuses: {n_focus} | Primary queries: {n_pq} | Fallbacks: {data["pfbs"]} | Candidates: {data["cands"]} | Roles: {passed}/{total}')
        L.append('')
        for f in data['focuses']:
            L.append(f'- **{f["id"]}**: {f["text"][:200]}')
        L.append('')
        L.append('| # | Query | Type |')
        L.append('|---|-------|------|')
        for i, q in enumerate(data['primary_queries'], 1):
            L.append(f'| {i} | `{q["text"]}` | {q["type"]} |')
        if data['fallback_queries']:
            L.append('')
            L.append('**Fallback queries:**')
            for q in data['fallback_queries']:
                L.append(f'- `{q["text"]}` [{q.get("origin","")}]')
        if data['replan_queries']:
            L.append('')
            L.append('**Replan queries (not executed):**')
            for q in data['replan_queries']:
                L.append(f'- `{q["text"]}` ({q["type"]})')
        L.append('')
        L.append('| Loop | Phase | raw | enforced | forced_stop | io_sat | replan_count |')
        L.append('|------|-------|-----|----------|-------------|--------|-------------|')
        for li in data['loop_info']:
            if 'phase' in li:
                L.append(f'| {li["loop"]} | started | — | — | — | — | — |')
            else:
                L.append(f'| {li["loop"]} | plan_eval | {li["raw"]} | {li["dec"]} | {li["forced"]} | {li["io_sat"]} | {li["replan_count"]} |')
        L.append('')
        L.append('| Role | Result | First Missing |')
        L.append('|------|--------|---------------|')
        for r in data['roles']:
            L.append(f'| {r["name"]} | {"PASS" if r["passed"] else "FAIL"} | {r["missing"]} |')
        L.append('')
    
    L.append('---')
    L.append('')

with open('docs/p3_ab_loop_report.md', 'w', encoding='utf-8') as f:
    f.write('\n'.join(L))
print(f'Written to docs/p3_ab_loop_report.md ({len(L)} lines)')
