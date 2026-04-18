"""Interactive Agent — Mode C: SDK-based conversational sessions.

Uses the Claude Agent SDK (claude-agent-sdk) for multi-turn interactive
conversations. Unlike Mode A/B which spawn CLI subprocesses, Mode C uses
the SDK's ClaudeSDKClient for structured message streaming with proper
session management.

Architecture:
  - One asyncio event loop runs in a daemon thread (_sdk_loop)
  - Each interactive session has a ClaudeSDKClient + output queue
  - Flask endpoints dispatch async work via run_coroutine_threadsafe()
  - SSE endpoints drain the output queue synchronously for Flask
"""

import asyncio
import json
import queue
import threading
import time
import uuid
from pathlib import Path

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    StreamEvent,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    ToolResultBlock,
    query,
)

# ── Async event loop (single thread for all SDK work) ────────────────────────

_sdk_loop = asyncio.new_event_loop()
_sdk_thread = threading.Thread(target=_sdk_loop.run_forever, daemon=True,
                                name='sdk-event-loop')
_sdk_thread.start()


def _run_async(coro, timeout=300):
    """Run an async coroutine on the SDK event loop, blocking until done."""
    future = asyncio.run_coroutine_threadsafe(coro, _sdk_loop)
    return future.result(timeout=timeout)


# ── Session state ────────────────────────────────────────────────────────────

# session_id → InteractiveSession
interactive_sessions = {}
_sessions_lock = threading.Lock()


class InteractiveSession:
    """State for one interactive (Mode C) conversation."""

    def __init__(self, session_id, project_id, project_path, options):
        self.session_id = session_id
        self.project_id = project_id
        self.project_path = project_path
        self.options = options
        self.client = None          # ClaudeSDKClient (set after async init)
        self.output_queue = queue.Queue()  # sync queue for SSE consumer
        self.log_lines = []         # formatted output for agent tab
        self.status = 'starting'    # starting | running | idle | error | stopped
        self.started_at = time.time()
        self.claude_session_id = None
        self.cost_usd = 0
        self.usage = {}
        self.num_turns = 0
        self._reader_task = None    # asyncio.Task for the response reader
        self._stop_event = asyncio.Event()
        self._client_lock = asyncio.Lock()  # guards client.query()

    def append_line(self, line):
        self.log_lines.append(line)
        if len(self.log_lines) > 2000:
            self.log_lines = self.log_lines[-1500:]


# ── Message formatting ───────────────────────────────────────────────────────

def _format_content_block(block):
    """Convert a content block to a display-friendly dict for SSE."""
    if isinstance(block, TextBlock):
        return {'type': 'text', 'text': block.text}
    elif isinstance(block, ThinkingBlock):
        return {'type': 'thinking', 'thinking': block.thinking}
    elif isinstance(block, ToolUseBlock):
        return {'type': 'tool_use', 'name': block.name, 'input': block.input,
                'id': block.id}
    elif isinstance(block, ToolResultBlock):
        return {'type': 'tool_result', 'tool_use_id': block.tool_use_id,
                'content': block.content, 'is_error': block.is_error}
    else:
        return {'type': 'unknown', 'data': str(block)}


def _format_tool_activity(name, tool_input):
    """Format a tool call as a log line, matching the Mode A/B renderer."""
    if name == 'Bash':
        cmd = tool_input.get('command', '')
        return f'[tool: Bash] {cmd[:100]}'
    elif name == 'Read':
        return f'[tool: Read] {tool_input.get("file_path", "")}'
    elif name == 'Write':
        return f'[tool: Write] {tool_input.get("file_path", "")}'
    elif name == 'Edit':
        return f'[tool: Edit] {tool_input.get("file_path", "")}'
    elif name in ('Grep', 'Glob'):
        return f'[tool: {name}] {tool_input.get("pattern", "")}'
    else:
        preview = json.dumps(tool_input, default=str)[:80]
        return f'[tool: {name}] {preview}'


# ── Core async operations ────────────────────────────────────────────────────

async def _create_client(session):
    """Create and initialize the ClaudeSDKClient."""
    client = ClaudeSDKClient(options=session.options)
    await client.__aenter__()
    session.client = client
    return client


async def _read_responses(session):
    """Read messages from the SDK client and push to the output queue.

    This runs as a long-lived task on the SDK event loop. Each time
    client.query() is called, this reads the response stream until
    the turn completes (ResultMessage), then goes back to waiting.
    """
    try:
        async for message in session.client.receive_response():
            if session._stop_event.is_set():
                break

            if isinstance(message, AssistantMessage):
                blocks = []
                for block in (message.content or []):
                    formatted = _format_content_block(block)
                    blocks.append(formatted)

                    # Also append to log_lines for agent tab compatibility
                    if isinstance(block, TextBlock) and block.text:
                        session.append_line(block.text)
                    elif isinstance(block, ToolUseBlock):
                        session.append_line(
                            _format_tool_activity(block.name, block.input))
                    elif isinstance(block, ThinkingBlock) and block.thinking:
                        session.append_line(f'[thinking] {block.thinking[:200]}...')

                session.output_queue.put({
                    'type': 'assistant',
                    'content': blocks,
                    'session_id': getattr(message, 'session_id', None),
                })
                session.status = 'running'

            elif isinstance(message, ResultMessage):
                session.claude_session_id = message.session_id
                session.cost_usd = getattr(message, 'total_cost_usd', 0) or 0
                session.usage = getattr(message, 'usage', {}) or {}
                session.num_turns += 1

                session.output_queue.put({
                    'type': 'result',
                    'session_id': message.session_id,
                    'subtype': message.subtype,
                    'result': message.result,
                    'cost_usd': session.cost_usd,
                    'usage': session.usage,
                })
                session.status = 'idle'
                session.append_line(f'[turn {session.num_turns} complete]')

            elif isinstance(message, SystemMessage):
                session.output_queue.put({
                    'type': 'system',
                    'subtype': getattr(message, 'subtype', ''),
                    'data': getattr(message, 'data', {}),
                })
                # Capture session_id from init
                data = getattr(message, 'data', {})
                if isinstance(data, dict) and 'session_id' in data:
                    session.claude_session_id = data['session_id']

            elif isinstance(message, StreamEvent):
                # Forward raw stream events for real-time text streaming
                event = message.event
                if isinstance(event, dict):
                    evt_type = event.get('type', '')
                    if evt_type == 'content_block_delta':
                        delta = event.get('delta', {})
                        if delta.get('type') == 'text_delta':
                            session.output_queue.put({
                                'type': 'text_delta',
                                'text': delta.get('text', ''),
                            })

    except Exception as e:
        session.status = 'error'
        session.append_line(f'[SDK error: {e}]')
        session.output_queue.put({
            'type': 'error',
            'error': str(e),
        })
        print(f"[interactive] {session.project_id}: SDK reader error: {e}")
    finally:
        # Signal end of stream
        session.output_queue.put(None)


async def _send_query(session, message):
    """Send a query to the SDK client and start reading responses."""
    async with session._client_lock:
        session.status = 'running'
        session.output_queue = queue.Queue()  # fresh queue for this turn

        await session.client.query(message)
        # Start (or restart) the response reader
        if session._reader_task and not session._reader_task.done():
            session._reader_task.cancel()
        session._reader_task = asyncio.ensure_future(
            _read_responses(session), loop=_sdk_loop)


async def _stop_session(session):
    """Stop an interactive session cleanly."""
    session._stop_event.set()
    if session._reader_task and not session._reader_task.done():
        session._reader_task.cancel()
    if session.client:
        try:
            await session.client.interrupt()
        except Exception:
            pass
        try:
            await session.client.__aexit__(None, None, None)
        except Exception:
            pass
    session.status = 'stopped'
    session.output_queue.put(None)


# ── Public API (called from Flask endpoints) ─────────────────────────────────

_INTERACTIVE_BEHAVIOR = """
## Interactive Conversation Mode

You are in an interactive conversation with the user — NOT in autonomous agent mode.
Behave like a senior engineer pair-programming with a colleague, not a task executor.

Key behaviors:
- **Think out loud**: before taking action, explain WHAT you're about to do and WHY.
  Show your reasoning chain. "I see X in the code, which suggests Y. Let me check Z
  to confirm before making changes."
- **Present findings before acting**: after reading files or running commands, summarize
  what you found and what it means before jumping to the next step. Don't chain 10 tool
  calls silently — pause and explain.
- **Surface options and tradeoffs**: when there are multiple approaches, present them
  briefly and ask which direction the user prefers. Don't just pick one silently.
- **Ask for clarification**: if the request is ambiguous or you're making assumptions,
  say so and ask. "I'm assuming you mean X — is that right, or did you mean Y?"
- **Admit uncertainty**: if you're not sure about something, say so instead of guessing.
  "I'm not certain this is the right fix — let me verify by checking..."
- **Keep the user in the loop**: after completing a significant step, summarize what
  you did and suggest next steps. Don't just silently finish.

The user chose interactive mode specifically because they want this back-and-forth
dialogue. They value your reasoning and analysis, not just your actions.
"""


def create_interactive_session(project_id, project_path, task, system_prompt='',
                                model='', max_turns=None, resume_id=None):
    """Create a new interactive session and send the first message.

    Returns the session_id.
    """
    session_id = f'ic_{uuid.uuid4().hex[:10]}'

    # Prepend interactive behavior instructions to the system prompt
    full_prompt = _INTERACTIVE_BEHAVIOR + '\n' + (system_prompt or '')

    options = ClaudeAgentOptions(
        cwd=project_path,
        system_prompt=full_prompt or None,
        model=model or None,
        max_turns=max_turns,
        permission_mode='bypassPermissions',
        include_partial_messages=True,
        resume=resume_id or None,
    )

    session = InteractiveSession(session_id, project_id, project_path, options)

    with _sessions_lock:
        interactive_sessions[session_id] = session

    async def _init():
        try:
            await _create_client(session)
            await _send_query(session, task)
        except Exception as e:
            session.status = 'error'
            session.append_line(f'[Failed to start interactive session: {e}]')
            session.output_queue.put({
                'type': 'error',
                'error': str(e),
            })
            session.output_queue.put(None)
            print(f"[interactive] {project_id}: init error: {e}")

    asyncio.run_coroutine_threadsafe(_init(), _sdk_loop)
    return session_id


def send_interactive_message(session_id, message):
    """Send a follow-up message to an existing interactive session."""
    with _sessions_lock:
        session = interactive_sessions.get(session_id)
    if not session:
        raise ValueError('session not found')
    if session.status == 'stopped':
        raise ValueError('session is stopped')

    session.append_line(f'\n> {message}\n')
    session.output_queue.put({
        'type': 'user_echo',
        'text': message,
    })

    asyncio.run_coroutine_threadsafe(_send_query(session, message), _sdk_loop)


def stop_interactive_session(session_id):
    """Stop an interactive session."""
    with _sessions_lock:
        session = interactive_sessions.get(session_id)
    if not session:
        return
    asyncio.run_coroutine_threadsafe(_stop_session(session), _sdk_loop)


def get_interactive_session(session_id):
    """Get session state for status endpoints."""
    with _sessions_lock:
        return interactive_sessions.get(session_id)


def list_interactive_sessions(project_id):
    """List all interactive sessions for a project."""
    with _sessions_lock:
        return [s for s in interactive_sessions.values()
                if s.project_id == project_id]


def drain_interactive_queue(session_id, timeout=30):
    """Generator that drains the output queue for SSE streaming.

    Yields formatted SSE data strings. Blocks up to `timeout` seconds
    waiting for each message. Returns when the queue signals end-of-stream
    (None sentinel) or on timeout.
    """
    with _sessions_lock:
        session = interactive_sessions.get(session_id)
    if not session:
        return

    while True:
        try:
            msg = session.output_queue.get(timeout=timeout)
        except queue.Empty:
            # Heartbeat to keep SSE alive
            yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
            continue

        if msg is None:
            # End of stream — send final status
            yield f"data: {json.dumps({'type': 'done', 'status': session.status})}\n\n"
            return

        yield f"data: {json.dumps(msg, default=str)}\n\n"
