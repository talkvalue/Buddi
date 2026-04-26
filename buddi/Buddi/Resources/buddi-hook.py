#!/usr/bin/env python3
"""
Buddi Hook
- Sends session state to Buddi.app via Unix socket
- For PermissionRequest: waits for user decision from the app
- Tracks session rhythm for buddy dialogue flavoring
"""
import json
import os
import socket
import sys
import time

SOCKET_PATH = "/tmp/buddi.sock"
TIMEOUT_SECONDS = 300  # 5 minutes for permission decisions


def get_tty():
    """Get the TTY of the Claude process (parent)"""
    import subprocess

    # Get parent PID (Claude process)
    ppid = os.getppid()

    # Try to get TTY from ps command for the parent process
    try:
        result = subprocess.run(
            ["ps", "-p", str(ppid), "-o", "tty="],
            capture_output=True,
            text=True,
            timeout=2
        )
        tty = result.stdout.strip()
        if tty and tty != "??" and tty != "-":
            # ps returns just "ttys001", we need "/dev/ttys001"
            if not tty.startswith("/dev/"):
                tty = "/dev/" + tty
            return tty
    except Exception:
        pass

    # Fallback: try current process stdin/stdout
    try:
        return os.ttyname(sys.stdin.fileno())
    except (OSError, AttributeError):
        pass
    try:
        return os.ttyname(sys.stdout.fileno())
    except (OSError, AttributeError):
        pass
    return None


def get_cmux_surface():
    """Get the cmux workspace and surface refs for the current terminal"""
    import subprocess
    import shutil

    cmux = shutil.which("cmux")
    if not cmux:
        # Check common locations
        for path in ["/Applications/cmux.app/Contents/Resources/bin/cmux"]:
            if os.path.isfile(path):
                cmux = path
                break
    if not cmux:
        return None, None

    try:
        result = subprocess.run(
            [cmux, "--json", "identify"],
            capture_output=True,
            text=True,
            timeout=2
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            caller = data.get("caller") or data.get("focused", {})
            return caller.get("workspace_ref"), caller.get("surface_ref")
    except Exception:
        pass
    return None, None


SESSION_STATS_PATH = os.path.expanduser("~/.buddi-session-stats.json")
SESSION_STATS_LOCK_PATH = SESSION_STATS_PATH + ".lock"


def load_session_stats():
    try:
        with open(SESSION_STATS_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_session_stats(stats):
    try:
        import tempfile
        dir_ = os.path.dirname(SESSION_STATS_PATH)
        with tempfile.NamedTemporaryFile("w", dir=dir_, delete=False, suffix=".tmp") as tmp:
            json.dump(stats, tmp)
            tmp_path = tmp.name
        os.replace(tmp_path, SESSION_STATS_PATH)
    except OSError:
        pass


def update_session_stats_atomic(session_id, event, tool_name=None, denied=False):
    import fcntl
    try:
        lock = open(SESSION_STATS_LOCK_PATH, "w")
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            return update_session_stats_atomic(session_id, event, tool_name=tool_name, denied=denied)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()
    except OSError:
        return update_session_stats_atomic(session_id, event, tool_name=tool_name, denied=denied)


def update_session_stats_atomic(session_id, event, tool_name=None, denied=False):
    stats = load_session_stats()
    s = stats.setdefault(session_id, {
        "tool_counts": {},
        "denial_count": 0,
        "prompt_count": 0,
        "session_start": time.time(),
        "last_event_time": time.time(),
    })

    s["last_event_time"] = time.time()

    if event == "PreToolUse" and tool_name:
        s["tool_counts"][tool_name] = s["tool_counts"].get(tool_name, 0) + 1
    elif event == "UserPromptSubmit":
        s["prompt_count"] = s.get("prompt_count", 0) + 1
    elif denied:
        s["denial_count"] = s.get("denial_count", 0) + 1

    save_session_stats(stats)
    return s


def compute_dialogue_flavor(stats):
    """
    Returns a dialogue flavor string based on session rhythm.
    This is purely additive — it never affects buddy identity.
    The Swift side uses this to vary what the buddy says, not what it looks like.
    """
    tool_counts = stats.get("tool_counts", {})
    denial_count = stats.get("denial_count", 0)
    prompt_count = max(stats.get("prompt_count", 1), 1)
    total_tools = sum(tool_counts.values())

    chaos_rate = denial_count / prompt_count

    shell_tools = {"Bash", "computer"}
    explore_tools = {"Read", "Grep", "LS", "Glob"}

    shell_uses = sum(tool_counts.get(t, 0) for t in shell_tools)
    explore_uses = sum(tool_counts.get(t, 0) for t in explore_tools)

    if chaos_rate > 0.4:
        return "chaotic"
    elif total_tools > 0 and shell_uses / max(total_tools, 1) > 0.5:
        return "runner"
    elif total_tools > 0 and explore_uses / max(total_tools, 1) > 0.5:
        return "explorer"
    elif total_tools > 20 and chaos_rate < 0.1:
        return "methodical"
    else:
        return "neutral"


def send_event(state):
    """Send event to app, return response if any"""
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(TIMEOUT_SECONDS)
        sock.connect(SOCKET_PATH)
        sock.sendall(json.dumps(state).encode())

        # For permission requests, wait for response
        if state.get("status") == "waiting_for_approval":
            response = sock.recv(4096)
            sock.close()
            if response:
                return json.loads(response.decode())
        else:
            sock.close()

        return None
    except (socket.error, OSError, json.JSONDecodeError):
        return None


def main():
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(1)

    session_id = data.get("session_id", "unknown")
    event = data.get("hook_event_name", "")
    cwd = data.get("cwd", "")
    tool_input = data.get("tool_input", {})

    # Get process info
    claude_pid = os.getppid()
    tty = get_tty()
    cmux_workspace, cmux_surface = get_cmux_surface()

    # Build state object
    state = {
        "session_id": session_id,
        "cwd": cwd,
        "event": event,
        "pid": claude_pid,
        "tty": tty,
    }

    # Include cmux surface info if running in cmux
    if cmux_workspace and cmux_surface:
        state["cmux_workspace"] = cmux_workspace
        state["cmux_surface"] = cmux_surface

    # Map events to status
    if event == "UserPromptSubmit":
        session_stats = update_session_stats_atomic(session_id, event)
        state["status"] = "processing"
        state["dialogue_flavor"] = compute_dialogue_flavor(session_stats)

    elif event == "PreToolUse":
        tool_name = data.get("tool_name")
        session_stats = update_session_stats_atomic(session_id, event, tool_name=tool_name)
        state["status"] = "running_tool"
        state["tool"] = tool_name
        state["tool_input"] = tool_input
        state["dialogue_flavor"] = compute_dialogue_flavor(session_stats)
        # Send tool_use_id to Swift for caching
        tool_use_id_from_event = data.get("tool_use_id")
        if tool_use_id_from_event:
            state["tool_use_id"] = tool_use_id_from_event

    elif event == "PostToolUse":
        state["status"] = "processing"
        state["tool"] = data.get("tool_name")
        state["tool_input"] = tool_input
        # Send tool_use_id so Swift can cancel the specific pending permission
        tool_use_id_from_event = data.get("tool_use_id")
        if tool_use_id_from_event:
            state["tool_use_id"] = tool_use_id_from_event

    elif event == "PermissionRequest":
        # This is where we can control the permission
        state["status"] = "waiting_for_approval"
        tool_name = data.get("tool_name")
        state["tool"] = tool_name
        state["tool_input"] = tool_input
        # Count denials for chaos tracking — updated after response below
        # tool_use_id lookup handled by Swift-side cache from PreToolUse

        # Send to app and wait for decision
        response = send_event(state)

        if response:
            decision = response.get("decision", "ask")
            reason = response.get("reason", "")

            if decision == "allow":
                # Output JSON to approve
                output = {
                    "hookSpecificOutput": {
                        "hookEventName": "PermissionRequest",
                        "decision": {"behavior": "allow"},
                    }
                }
                print(json.dumps(output))
                sys.exit(0)

            elif decision == "deny":
                update_session_stats_atomic(session_id, event, denied=True)
                # Output JSON to deny
                output = {
                    "hookSpecificOutput": {
                        "hookEventName": "PermissionRequest",
                        "decision": {
                            "behavior": "deny",
                            "message": reason or "Denied by user via Buddi",
                        },
                    }
                }
                print(json.dumps(output))
                sys.exit(0)

        # No response or "ask" - let Claude Code show its normal UI
        sys.exit(0)

    elif event == "Notification":
        notification_type = data.get("notification_type")
        # Skip permission_prompt - PermissionRequest hook handles this with better info
        if notification_type == "permission_prompt":
            sys.exit(0)
        elif notification_type == "idle_prompt":
            state["status"] = "waiting_for_input"
        else:
            state["status"] = "notification"
        state["notification_type"] = notification_type
        state["message"] = data.get("message")

    elif event == "Stop":
        state["status"] = "waiting_for_input"

    elif event == "SubagentStop":
        # SubagentStop fires when a subagent completes - usually means back to waiting
        state["status"] = "waiting_for_input"

    elif event == "SessionStart":
        # New session starts waiting for user input
        state["status"] = "waiting_for_input"

    elif event == "SessionEnd":
        state["status"] = "ended"

    elif event == "PreCompact":
        # Context is being compacted (manual or auto)
        state["status"] = "compacting"

    else:
        state["status"] = "unknown"

    # Send to socket (fire and forget for non-permission events)
    send_event(state)


if __name__ == "__main__":
    main()
