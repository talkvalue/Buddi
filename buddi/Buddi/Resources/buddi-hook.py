#!/usr/bin/env python3
"""
Buddi Hook
- Sends session state to Buddi.app via Unix socket (local) or TCP (remote)
- For PermissionRequest: waits for user decision from the app

Environment variables:
  BUDDI_HOST    If set (e.g. "localhost:9999"), connect over TCP instead of
                a Unix socket. Intended for Claude Code running on a remote
                VM with an SSH reverse port-forward back to the Mac running
                Buddi. Always tunnel over SSH — events may include prompts,
                tool calls, and file paths.
  BUDDI_SOCKET  Override the default Unix socket path (/tmp/buddi.sock).
                Ignored when BUDDI_HOST is set.
"""
import json
import os
import socket
import sys

BUDDI_SOCKET = os.environ.get("BUDDI_SOCKET", "/tmp/buddi.sock")
BUDDI_HOST = os.environ.get("BUDDI_HOST")
TIMEOUT_SECONDS = 300  # 5 minutes for permission decisions

if BUDDI_HOST:
    _host = BUDDI_HOST.rpartition(":")[0].strip("[]")
    if _host not in ("localhost", "127.0.0.1", "::1"):
        print(
            f"buddi-hook: warning: BUDDI_HOST={BUDDI_HOST!r} is not a loopback "
            "address; events contain prompts and tool inputs — only use over "
            "an SSH tunnel.",
            file=sys.stderr,
        )


def _connect_to_buddi():
    """Open a connection to Buddi (TCP if BUDDI_HOST is set, else Unix socket)."""
    if BUDDI_HOST:
        host, sep, port = BUDDI_HOST.rpartition(":")
        if not sep or not host:
            raise OSError(f"BUDDI_HOST must be host:port, got {BUDDI_HOST!r}")
        try:
            port_num = int(port)
        except ValueError:
            raise OSError(f"BUDDI_HOST port must be an integer, got {port!r}")
        if not 0 < port_num <= 65535:
            raise OSError(f"BUDDI_HOST port {port_num} out of range (1-65535)")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(TIMEOUT_SECONDS)
        sock.connect((host, port_num))
    else:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(TIMEOUT_SECONDS)
        sock.connect(BUDDI_SOCKET)
    return sock


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


def send_event(state):
    """Send event to app, return response if any"""
    try:
        sock = _connect_to_buddi()
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
        # User just sent a message - Claude is now processing
        state["status"] = "processing"

    elif event == "PreToolUse":
        state["status"] = "running_tool"
        state["tool"] = data.get("tool_name")
        state["tool_input"] = tool_input
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
        state["tool"] = data.get("tool_name")
        state["tool_input"] = tool_input
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
