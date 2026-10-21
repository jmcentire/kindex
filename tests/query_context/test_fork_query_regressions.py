"""Mixed unknown explicit IDs must refuse handoff while preserving baseline.

Independent regression under the accepted query-context bugfix scope.
"""
import json
from uuid import uuid4

import pytest

AT = '2030-01-02T12:00:00Z'
QUERY = 'auroracable'


def prepare(api, env, **changes):
    request = dict(query=QUERY, project_path=env.project,
        client_scope=api.TrustedClientScope(mode='client', client='codex'),
        retrieval_max_bytes=8192, top_k=6, evaluation_time=AT)
    request.update(changes)
    return api.prepare_query_context(env.ro(), **request)



@pytest.mark.parametrize('unknown_first', [False, True])
def test_mixed_known_unknown_explicit_ids_refuse_entire_handoff(api, env, unknown_first):
    env.source(QUERY + ' independent ordinary baseline', title=QUERY)
    literal = 'INVENTED_SELECTED_ONLY_CONTENT keep the brass latch closed.'
    selected_source = env.source(literal, title='Separate selected fixture')
    known = env.pin(selected_source, literal)
    missing = str(uuid4())
    before = tuple(env.rw.conn.iterdump())
    baseline = prepare(api, env)
    positive = prepare(api, env, include_handoff=True, evidence_session_id=env.session,
                       handoff_selection='explicit_selected', evidence_ids=[known],
                       handoff_max_bytes=16384)
    assert literal in positive['handoff']['text'], 'The valid selected record must be independently usable'
    requested = [missing, known] if unknown_first else [known, missing]
    result = prepare(api, env, include_handoff=True, evidence_session_id=env.session,
                     handoff_selection='explicit_selected', evidence_ids=requested,
                     handoff_max_bytes=16384)
    assert result['retrieval'] == baseline['retrieval']
    assert result['status'] == 'partial'
    handoff = result['handoff']
    assert handoff['status'] == 'unavailable'
    assert handoff['text'] == '' and handoff['included'] == []
    assert literal not in json.dumps(handoff)
    assert tuple(env.rw.conn.iterdump()) == before


