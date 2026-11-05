import json

from src import agent_bridge as ab
from src.agent_bridge import AgentBridge, Event, StreamParser


def line(obj):
    return json.dumps(obj)


INIT = line({"type": "system", "subtype": "init", "session_id": "s-1",
             "tools": ["Bash", "mcp__senses__capture_screen", "mcp__claude_ai_Gmail__search_threads",
                       "mcp__claude_ai_Gmail__send_message", "mcp__claude_ai_Google_Calendar__list_events"]})
START = line({"type": "stream_event", "event": {"type": "message_start", "message": {}}, "session_id": "s-1"})


def delta(text):
    return line({"type": "stream_event", "session_id": "s-1",
                 "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}}})


def assistant(*blocks):
    return line({"type": "assistant", "session_id": "s-1", "message": {"content": list(blocks)}})


RESULT_OK = line({"type": "result", "subtype": "success", "is_error": False, "result": "Hello there.", "session_id": "s-1"})


def parse(lines):
    p = StreamParser()
    out = []
    for raw in lines:
        out.extend(p.feed(raw))
    return p, out


def test_streamed_text_is_not_duplicated():
    p, events = parse([INIT, START, delta("Hello "), delta("there."),
                       assistant({"type": "text", "text": "Hello there."}), RESULT_OK])
    assert events == [Event("text", "Hello "), Event("text", "there."), Event("done", "Hello there.")]
    assert p.session_id == "s-1"
    assert "mcp__senses__capture_screen" in p.tools


def test_full_message_text_used_without_deltas():
    _, events = parse([INIT, assistant({"type": "text", "text": "Hi."}), RESULT_OK])
    assert events[0] == Event("text", "Hi.")


def test_tool_use_event():
    _, events = parse([
        START, delta("Let me look."),
        assistant({"type": "text", "text": "Let me look."},
                  {"type": "tool_use", "name": "mcp__senses__capture_screen", "input": {}}),
        START, delta("You have VS Code open."),
        assistant({"type": "text", "text": "You have VS Code open."}),
        RESULT_OK,
    ])
    kinds = [e.type for e in events]
    assert kinds == ["text", "tool_use", "text", "done"]
    assert events[1].content == "mcp__senses__capture_screen"


def test_error_result():
    _, events = parse([line({"type": "result", "subtype": "error_during_execution", "is_error": True,
                             "result": "boom", "session_id": "s-1"})])
    assert events == [Event("error", "boom")]


def test_garbage_lines_are_ignored():
    _, events = parse(["", "not json", "[1,2]", line({"type": "rate_limit_event"}),
                       line({"type": "system", "subtype": "status"})])
    assert events == []


def test_event_dict_shape():
    assert Event("text", "a").as_dict() == {"type": "text", "content": "a"}


def test_allowed_tools_only_read_only_connectors():
    tools = ["mcp__claude_ai_Gmail__search_threads", "mcp__claude_ai_Gmail__send_message",
             "mcp__claude_ai_Gmail__get_thread", "mcp__claude_ai_Google_Calendar__create_event", "Bash"]
    allowed = ab.allowed_tools(tools)
    assert "mcp__senses" in allowed
    assert "mcp__claude_ai_Gmail__search_threads" in allowed
    assert "mcp__claude_ai_Gmail__get_thread" in allowed
    assert "mcp__claude_ai_Gmail__send_message" not in allowed
    assert "mcp__claude_ai_Google_Calendar__create_event" not in allowed
    assert "Bash" not in allowed


def test_command_resumes_session(app_home):
    bridge = AgentBridge()
    cmd = bridge.build_command("claude", "what time is it")
    assert cmd[:3] == ["claude", "-p", "what time is it"]
    assert cmd[cmd.index("--output-format") + 1] == "stream-json"
    assert "--resume" not in cmd
    bridge.session_id = "abc"
    cmd = bridge.build_command("claude", "and now?")
    assert cmd[cmd.index("--resume") + 1] == "abc"


def test_session_survives_restart(app_home):
    bridge = AgentBridge()
    bridge.session_id = "keep-me"
    bridge._save_state()
    assert AgentBridge().session_id == "keep-me"
    AgentBridge().new_session()
    assert AgentBridge().session_id is None
