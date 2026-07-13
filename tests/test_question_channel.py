"""Offline question channel + the Claude question fence.

The bug this covers: the system prompt promised every agent an `AskUserQuestion`
tool that does not exist headless, so questions from unattended runs (schedules,
steward cycles, the night review) died in prose where nobody read them.

Two halves:
  1. Claude can now ask at all — the ```mc:question``` fence is parsed out of a
     finished turn into the same session state the native tool used to produce.
  2. If nobody is watching, the question is delivered to the user's channel and
     their emailed reply resumes the agent through the normal follow-up path.
"""
from __future__ import annotations

import time

import pytest

import agent_runtime
from mc import question_channel as qc


@pytest.fixture(autouse=True)
def _clean():
    qc._delivered.clear()
    qc._answered.clear()
    qc._outbox.clear()
    yield
    qc._delivered.clear()
    qc._answered.clear()
    qc._outbox.clear()


QUESTION_TURN = '''Here's what I found. I need you to choose.

```mc:question
{"questions": [{"header": "Deploy", "question": "Ship the rebuilt demo?",
"options": [{"label": "Ship it", "description": "deploy to clayrune.io now"},
            {"label": "Hold", "description": "wait for the screenshots"}],
"multiSelect": false}]}
```
'''


def _session(**kw):
    s = {
        'project_id': 'mission_control',
        'session_id': 'sess-1',
        'task': 'Rebuild the demo',
        'log_lines': [],
    }
    s.update(kw)
    return s


# ─── 1. Claude can ask a question at all ─────────────────────────────────────


def test_a_question_fence_becomes_a_pending_question():
    """This is the whole fix. Before it, a Claude agent had no way to ask."""
    s = _session()
    res = agent_runtime.apply_mc_tool_blocks(s, QUESTION_TURN)

    assert res['paused'] is True, "the turn must park, not complete"
    assert s['waiting_for_question'] is True
    assert len(s['pending_questions']) == 1

    q = s['pending_questions'][0]
    assert q['question_id']
    assert q['questions'][0]['question'] == 'Ship the rebuilt demo?'
    assert [o['label'] for o in q['questions'][0]['options']] == ['Ship it', 'Hold']


def test_a_normal_turn_raises_nothing():
    s = _session()
    res = agent_runtime.apply_mc_tool_blocks(s, 'Done. No questions here.')
    assert res['paused'] is False
    assert not s.get('pending_questions')


def test_the_module_level_function_and_the_method_agree():
    """agent_routes has no runtime instance to call, so the logic had to move to
    module level. Gemini still calls the method — they must not drift."""
    rt = agent_runtime.ClaudeRuntime()
    a, b = _session(), _session()
    assert rt.apply_mc_tool_blocks(a, QUESTION_TURN) == \
        agent_runtime.apply_mc_tool_blocks(b, QUESTION_TURN)
    assert a['pending_questions'][0]['questions'] == b['pending_questions'][0]['questions']


# ─── 2. Attended vs unattended ───────────────────────────────────────────────


def test_a_watched_session_is_attended():
    s = _session(_last_sse_poll_time=time.time())
    assert qc.is_attended(s, grace=45) is True


def test_a_session_nobody_has_polled_is_unattended():
    s = _session(_last_sse_poll_time=time.time() - 600)
    assert qc.is_attended(s, grace=45) is False


def test_a_session_with_no_viewer_ever_is_unattended():
    """A scheduled run at 3am. No browser has ever touched it."""
    assert qc.is_attended(_session(), grace=45) is False


def test_a_manual_run_with_a_closed_tab_is_also_unattended():
    """trigger_type alone would call this 'manual' and stay quiet. The viewer
    heartbeat is the signal that actually matters."""
    s = _session(trigger_type='manual', _last_sse_poll_time=time.time() - 300)
    assert qc.is_attended(s, grace=45) is False


# ─── 3. Delivery: once, and only when nobody is there ────────────────────────


def test_delivery_is_skipped_when_someone_is_watching(monkeypatch):
    sent = []
    monkeypatch.setattr(qc, '_send_email', lambda *a: sent.append(a) or True)

    s = _session(_last_sse_poll_time=time.time(), waiting_for_question=True)
    agent_runtime.apply_mc_tool_blocks(s, QUESTION_TURN)
    qid = s['pending_questions'][0]['question_id']
    qc._outbox[qid] = {'project_id': 'p', 'project_name': 'P', 'session_id': 's',
                       'questions': s['pending_questions'][0]['questions'], 'to': None}

    qc._deliver_if_still_unattended(qid, s, grace=45)
    assert sent == [], "a watched question must be answered in the chat, not emailed"


def test_delivery_is_skipped_if_answered_during_the_grace_window(monkeypatch):
    """The user opened the tab and clicked an option while we waited. Sending now
    would be a pointless email — which is how a channel earns an ignore rule."""
    sent = []
    monkeypatch.setattr(qc, '_send_email', lambda *a: sent.append(a) or True)

    s = _session(waiting_for_question=False)  # already answered
    qc._outbox['abc'] = {'project_id': 'p', 'project_name': 'P', 'session_id': 's',
                         'questions': [], 'to': None}
    qc._deliver_if_still_unattended('abc', s, grace=45)
    assert sent == []


def test_an_unattended_question_is_delivered(monkeypatch):
    sent = []
    monkeypatch.setattr(qc, '_send_email',
                        lambda subj, body, to: sent.append((subj, body)) or True)

    s = _session(waiting_for_question=True)  # no viewer, ever
    agent_runtime.apply_mc_tool_blocks(s, QUESTION_TURN)
    q = s['pending_questions'][0]
    qid = q['question_id']
    qc._outbox[qid] = {'project_id': 'p', 'project_name': 'Mission Control',
                       'session_id': 's', 'questions': q['questions'], 'to': None}

    qc._deliver_if_still_unattended(qid, s, grace=45)

    assert len(sent) == 1
    subj, body = sent[0]
    assert f'q:{qid[:8]}' in subj, "the subject must carry the reply key"
    assert 'Ship the rebuilt demo?' in body
    assert '1. Ship it' in body and '2. Hold' in body, "options must be numbered to reply by number"
    # The user must be told the agent is parked and how to answer.
    assert 'reply' in body.lower()


def test_a_question_is_delivered_at_most_once(monkeypatch):
    """Alert fatigue kills the channel. The turn scanner can run more than once
    (mode B re-reads, reconnects); the mail must not."""
    monkeypatch.setattr(qc, '_send_email', lambda *a: True)
    s = _session(waiting_for_question=True)
    agent_runtime.apply_mc_tool_blocks(s, QUESTION_TURN)

    timers = []
    monkeypatch.setattr(qc.threading, 'Timer',
                        lambda *a, **k: timers.append(a) or type(
                            'T', (), {'start': lambda self: None, 'daemon': True})())

    qc.on_question_raised(s)
    qc.on_question_raised(s)
    qc.on_question_raised(s)
    assert len(timers) == 1, "one question, one delivery"


def test_channel_off_delivers_nothing(monkeypatch):
    monkeypatch.setattr(qc, 'channel_for', lambda _p: 'off')
    timers = []
    monkeypatch.setattr(qc.threading, 'Timer', lambda *a, **k: timers.append(a))

    s = _session(waiting_for_question=True)
    agent_runtime.apply_mc_tool_blocks(s, QUESTION_TURN)
    qc.on_question_raised(s)
    assert timers == []


# ─── 4. Reply → answer ───────────────────────────────────────────────────────


OPTS = [{'question': 'Ship it?',
         'options': [{'label': 'Ship it'}, {'label': 'Hold'}]}]


@pytest.mark.parametrize('reply,expected', [
    ('1', 'Ship it'),            # by number
    ('2', 'Hold'),
    ('Ship it', 'Ship it'),      # exact label
    ('hold', 'Hold'),            # case-insensitive
    ('ship', 'Ship it'),         # loose match
])
def test_replies_map_onto_options(reply, expected):
    assert qc.match_answer(reply, OPTS) == expected


def test_an_unrecognised_reply_is_passed_through_verbatim():
    """We never GUESS an option from a vague reply. Passing the words to the agent
    is always safe; silently picking the wrong option is not."""
    assert qc.match_answer('do neither, wait for me', OPTS) == 'do neither, wait for me'


def test_an_out_of_range_number_is_not_an_option():
    assert qc.match_answer('9', OPTS) == '9'


def test_a_reply_answers_the_right_question(monkeypatch):
    answered = []
    monkeypatch.setattr(qc, '_answer', lambda qid, ans: answered.append((qid, ans)) or True)

    qc._outbox['deadbeefcafe'] = {'project_id': 'p', 'project_name': 'P',
                                  'session_id': 's', 'questions': OPTS, 'to': None}
    ok = qc.handle_reply('Re: [Clayrune question] P · q:deadbeef', '2\n\n> quoted junk')
    assert ok is True
    assert answered == [('deadbeefcafe', 'Hold')]


def test_a_reply_is_acted_on_only_once(monkeypatch):
    """A poller re-reads the inbox. The same reply must not resume the agent twice."""
    calls = []
    monkeypatch.setattr(qc, '_answer', lambda qid, ans: calls.append(qid) or True)
    qc._outbox['deadbeefcafe'] = {'project_id': 'p', 'project_name': 'P',
                                  'session_id': 's', 'questions': OPTS, 'to': None}

    subj, body = 'Re: [Clayrune question] P · q:deadbeef', '1'
    assert qc.handle_reply(subj, body) is True
    assert qc.handle_reply(subj, body) is False
    assert qc.handle_reply(subj, body) is False
    assert len(calls) == 1


def test_a_reply_for_an_unknown_question_is_ignored(monkeypatch):
    monkeypatch.setattr(qc, '_answer', lambda *a: pytest.fail("must not answer"))
    assert qc.handle_reply('Re: [Clayrune question] P · q:00000000', '1') is False


def test_mail_without_a_question_id_is_ignored(monkeypatch):
    monkeypatch.setattr(qc, '_answer', lambda *a: pytest.fail("must not answer"))
    assert qc.handle_reply('Re: your night review', '1') is False


def test_quoted_reply_text_is_skipped(monkeypatch):
    """Mail clients quote the original beneath the reply. The answer is the first
    line the human actually typed."""
    got = []
    monkeypatch.setattr(qc, '_answer', lambda qid, ans: got.append(ans) or True)
    qc._outbox['deadbeefcafe'] = {'project_id': 'p', 'project_name': 'P',
                                  'session_id': 's', 'questions': OPTS, 'to': None}

    body = "\n\nHold\n\nOn Sun, Jul 13, Clayrune wrote:\n> An agent is waiting\n> 1. Ship it\n"
    qc.handle_reply('Re: [Clayrune question] P · q:deadbeef', body)
    assert got == ['Hold']


def test_the_poller_does_nothing_when_no_question_is_outstanding():
    """An idle Clayrune must not log into a mailbox every two minutes for nothing."""
    assert qc.poll_replies() == 0


# ─── 5. The wiring inside the stream reader ──────────────────────────────────
#
# The unit tests above prove the pieces. These prove agent_routes actually calls
# them — the buffer→scan→notify chain that runs at a real turn boundary.


def test_the_turn_boundary_hook_parses_the_fence_and_notifies():
    from mc.blueprints import agent_routes

    fired = []
    orig = agent_routes._question_channel_notify
    agent_routes._question_channel_notify = lambda s: fired.append(s)
    try:
        s = _session()
        # What the reader accumulates from `assistant` text blocks.
        s['_mc_turn_buf'] = [QUESTION_TURN]
        agent_routes._apply_mc_tool_blocks_for_turn(s)
    finally:
        agent_routes._question_channel_notify = orig

    assert s.get('waiting_for_question') is True
    assert len(s.get('pending_questions') or []) == 1
    assert len(fired) == 1, "an unattended question must reach the channel"
    assert '_mc_turn_buf' not in s, "the buffer must be drained, or the next turn re-fires it"


def test_the_turn_boundary_hook_is_a_no_op_on_a_normal_turn():
    from mc.blueprints import agent_routes

    fired = []
    orig = agent_routes._question_channel_notify
    agent_routes._question_channel_notify = lambda s: fired.append(s)
    try:
        s = _session(_mc_turn_buf=['All done, no questions.'])
        agent_routes._apply_mc_tool_blocks_for_turn(s)
    finally:
        agent_routes._question_channel_notify = orig

    assert not s.get('pending_questions')
    assert fired == []


def test_the_turn_boundary_hook_never_raises():
    """Best-effort: this runs inside the stream reader. A throw here would kill
    the turn — the one thing a notification feature must never do."""
    from mc.blueprints import agent_routes

    s = _session(_mc_turn_buf=['```mc:question\n{not json at all\n```'])
    agent_routes._apply_mc_tool_blocks_for_turn(s)   # must not raise
    assert not s.get('pending_questions')
