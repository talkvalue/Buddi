#!/usr/bin/env python3
"""
Buddi Hook
- Sends session state to Buddi.app via Unix socket
- For PermissionRequest: waits for user decision from the app
- Tracks coding style stats to influence buddy identity generation
"""
import json
import os
import socket
import sys

SOCKET_PATH = "/tmp/buddi.sock"
TIMEOUT_SECONDS = 300  # 5 minutes for permission decisions
STYLE_PATH = os.path.expanduser("~/.buddi_style.json")

# Tools that signal different coding personalities
_EXPLORER_TOOLS = {"Read", "Grep", "Glob", "LS"}
_BUILDER_TOOLS = {"Write", "Edit", "NotebookEdit"}
_RUNNER_TOOLS = {"Bash", "Task"}
_AGENT_TOOLS = {"Agent", "WebSearch", "WebFetch"}


# ---------------------------------------------------------------------------
# Style tracking
# ---------------------------------------------------------------------------

def _update_style(updates: dict) -> None:
    """Atomically apply incremental updates to the style file using an flock."""
    import fcntl

    lock_path = STYLE_PATH + ".lock"
    try:
        lock_fd = open(lock_path, "w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            try:
                with open(STYLE_PATH) as f:
                    style = json.load(f)
            except (OSError, json.JSONDecodeError):
                style = {}

            for key, value in updates.items():
                if key == "tools":
                    tools = style.setdefault("tools", {})
                    for tool_name, count in value.items():
                        tools[tool_name] = tools.get(tool_name, 0) + count
                else:
                    style[key] = style.get(key, 0) + value

            with open(STYLE_PATH, "w") as f:
                json.dump(style, f)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()
    except OSError:
        pass


def _load_style() -> dict:
    try:
        with open(STYLE_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _record_tool(tool_name: str) -> None:
    _update_style({"tools": {tool_name: 1}, "total_tool_calls": 1})


def _record_denial() -> None:
    _update_style({"denials": 1})


def _record_session_end() -> None:
    _update_style({"sessions": 1})


# ---------------------------------------------------------------------------
# Style-aware buddy rolling (mirrors BuddyDetector logic in Python)
# ---------------------------------------------------------------------------

# Weights must match BuddyIdentity.swift
_RARITIES = ["common", "uncommon", "rare", "epic", "legendary"]
_RARITY_WEIGHTS = [60, 25, 10, 4, 1]

_SPECIES = [
    "duck", "goose", "blob", "cat", "dragon", "octopus", "owl", "penguin",
    "turtle", "snail", "ghost", "axolotl", "capybara", "cactus", "robot",
    "rabbit", "mushroom", "chonk",
]

_EYES = ["dot", "spark", "cross", "target", "at", "degree"]
_HATS = ["none", "crown", "tophat", "propeller", "halo", "wizard", "beanie", "tinyduck"]

# Species index constants (must match _SPECIES list above)
_IDX = {s: i for i, s in enumerate(_SPECIES)}


class _Mulberry32:
    """Pure-Python port of Mulberry32.swift."""

    def __init__(self, seed: int):
        self._seed = seed & 0xFFFFFFFF

    def next(self) -> float:
        a = (self._seed + 0x6d2b79f5) & 0xFFFFFFFF
        # Treat as signed 32-bit for the multiplications
        sa = a if a < 0x80000000 else a - 0x100000000

        def imul(x: int, y: int) -> int:
            return ((x & 0xFFFFFFFF) * (y & 0xFFFFFFFF)) & 0xFFFFFFFF

        def to_s32(v: int) -> int:
            v = v & 0xFFFFFFFF
            return v if v < 0x80000000 else v - 0x100000000

        t = imul(to_s32(a ^ (a >> 15)), to_s32(1 | a))
        t = to_s32(t)
        inner = imul(to_s32(t ^ (t >> 7)), to_s32(61 | t))
        t = to_s32((t + inner) ^ t)

        self._seed = a & 0xFFFFFFFF
        result = (t ^ (t >> 14)) & 0xFFFFFFFF
        return result / 4_294_967_296.0


def _fnv1a_32(data: bytes) -> int:
    """FNV-1a 32-bit — folds the style delta into the seed."""
    h = 0x811c9dc5
    for b in data:
        h ^= b
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


_WYHASH_SECRET = [
    0xa0761d6478bd642f,
    0xe7037ed1a0b428db,
    0x8ebc6af09c88c6e3,
    0x589965cc75374cc3,
]

M64 = 0xFFFFFFFFFFFFFFFF


def _wy_read(buf: bytes, offset: int, n: int) -> int:
    v = 0
    for i in range(n):
        idx = offset + i
        if idx < len(buf):
            v |= buf[idx] << (i * 8)
    return v


def _wy_mum(a: int, b: int):
    p = a * b
    return p & M64, (p >> 64) & M64


def _wy_mix(a: int, b: int) -> int:
    lo, hi = _wy_mum(a, b)
    return (lo ^ hi) & M64


def _wyhash(key: str) -> int:
    """Full wyhash port matching Wyhash.swift (seed=0). Handles any key length."""
    data = key.encode()
    length = len(data)
    s = _WYHASH_SECRET

    seed = 0
    state0 = (seed ^ _wy_mix((seed ^ s[0]) & M64, s[1])) & M64

    if length <= 16:
        if length >= 4:
            end = length - 4
            quarter = (length >> 3) << 2
            a = (_wy_read(data, 0, 4) << 32) | _wy_read(data, quarter, 4)
            b = (_wy_read(data, end, 4) << 32) | _wy_read(data, end - quarter, 4)
        elif length > 0:
            a = (data[0] << 16) | (data[length >> 1] << 8) | data[length - 1]
            b = 0
        else:
            a = b = 0
    else:
        state = [state0, state0, state0]
        i = 0

        if length >= 48:
            while i + 48 < length:
                for j in range(3):
                    ar = _wy_read(data, i + 8 * (2 * j), 8)
                    br = _wy_read(data, i + 8 * (2 * j + 1), 8)
                    state[j] = _wy_mix((ar ^ s[j + 1]) & M64, (br ^ state[j]) & M64)
                i += 48
            state[0] ^= state[1] ^ state[2]

        remaining = data[i:]
        k = 0
        while k + 16 < len(remaining):
            state[0] = _wy_mix(
                (_wy_read(remaining, k, 8) ^ s[1]) & M64,
                (_wy_read(remaining, k + 8, 8) ^ state[0]) & M64,
            )
            k += 16

        a = _wy_read(data, length - 16, 8)
        b = _wy_read(data, length - 8, 8)
        state0 = state[0]

    a ^= s[1]
    b ^= state0
    lo, hi = _wy_mum(a & M64, b & M64)
    return _wy_mix((lo ^ s[0] ^ length) & M64, (hi ^ s[1]) & M64)


def _roll_rarity(rng: _Mulberry32) -> str:
    total = sum(_RARITY_WEIGHTS)
    roll = rng.next() * total
    for rarity, weight in zip(_RARITIES, _RARITY_WEIGHTS):
        roll -= weight
        if roll < 0:
            return rarity
    return "common"


def _pick(rng: _Mulberry32, values: list):
    idx = int(rng.next() * len(values))
    return values[min(idx, len(values) - 1)]


def _style_delta(style: dict) -> int:
    """
    Compute a small integer delta [0, 255] from accumulated style stats.
    This is XOR'd into the seed so the same user ID + same style = same buddy,
    but a different style shifts the outcome.
    """
    tools = style.get("tools", {})
    total = max(style.get("total_tool_calls", 1), 1)

    explorer = sum(tools.get(t, 0) for t in _EXPLORER_TOOLS) / total
    builder = sum(tools.get(t, 0) for t in _BUILDER_TOOLS) / total
    runner = sum(tools.get(t, 0) for t in _RUNNER_TOOLS) / total
    agent = sum(tools.get(t, 0) for t in _AGENT_TOOLS) / total
    sessions = style.get("sessions", 0)
    denials = style.get("denials", 0)
    chaos = denials / max(sessions, 1)

    # Combine into a single byte: each ratio contributes 0-51 points
    delta = (
        int(explorer * 51)
        + int(builder * 51)
        + int(runner * 51)
        + int(agent * 51)
        + int(min(chaos, 1.0) * 51)
    ) & 0xFF
    return delta


def _species_bias(style: dict) -> list[float]:
    """
    Return per-species additive weight boosts based on coding style.
    Values are small so the base RNG still dominates — style nudges, not dictates.
    """
    tools = style.get("tools", {})
    total = max(style.get("total_tool_calls", 1), 1)
    sessions = style.get("sessions", 0)
    denials = style.get("denials", 0)

    explorer = sum(tools.get(t, 0) for t in _EXPLORER_TOOLS) / total
    runner = sum(tools.get(t, 0) for t in _RUNNER_TOOLS) / total
    chaos = min(denials / max(sessions, 1), 1.0)
    is_rare_coder = sessions < 5

    boosts = [0.0] * len(_SPECIES)
    boosts[_IDX["owl"]] += explorer * 2.0
    boosts[_IDX["octopus"]] += explorer * 1.5
    boosts[_IDX["robot"]] += runner * 2.0
    boosts[_IDX["dragon"]] += runner * 1.5
    boosts[_IDX["ghost"]] += chaos * 2.0
    boosts[_IDX["blob"]] += chaos * 1.5
    boosts[_IDX["turtle"]] += (1.0 - chaos) * 1.5
    boosts[_IDX["penguin"]] += (1.0 - chaos) * 1.0
    if is_rare_coder:
        boosts[_IDX["snail"]] += 2.0
        boosts[_IDX["cactus"]] += 1.5
    return boosts


def compute_style_identity(user_id: str, style: dict) -> dict:
    """
    Derive a buddy identity dict from user_id + accumulated style stats.
    Mirrors BuddyDetector.roll() but folds in the style delta.
    """
    salt = "friend-2026-401"
    base_hash = _wyhash(user_id + salt)
    delta = _style_delta(style)
    seed = (base_hash ^ (delta << 16)) & 0xFFFFFFFF
    rng = _Mulberry32(seed=seed)

    rarity = _roll_rarity(rng)

    # Apply species bias: weight each species and pick via weighted RNG
    boosts = _species_bias(style)
    weights = [1.0 + b for b in boosts]
    total_w = sum(weights)
    roll = rng.next() * total_w
    species = _SPECIES[-1]
    for sp, w in zip(_SPECIES, weights):
        roll -= w
        if roll < 0:
            species = sp
            break

    eye = _pick(rng, _EYES)
    hat = "none" if rarity == "common" else _pick(rng, _HATS)

    sessions = style.get("sessions", 0)
    if sessions >= 100:
        # Veteran bonus: legendary chance doubles (applied as override when base rolled legendary)
        pass  # rarity already set; legendary probability naturally increases with stable seed

    return {"species": species, "rarity": rarity, "eye": eye, "hat": hat}


def _load_claude_user_id() -> str | None:
    candidates = []
    custom = os.environ.get("CLAUDE_CONFIG_DIR", "")
    if custom:
        p = os.path.expanduser(custom)
        if p.endswith(".json"):
            candidates.append(p)
        else:
            for name in (".claude.json", ".config.json", "config.json"):
                candidates.append(os.path.join(p, name))
    home = os.path.expanduser("~")
    candidates += [
        os.path.join(home, ".claude.json"),
        os.path.join(home, ".claude", ".config.json"),
    ]
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            with open(path) as f:
                cfg = json.load(f)
            uid = (cfg.get("oauthAccount") or {}).get("accountUuid") or cfg.get("userID")
            if uid:
                return uid
        except (OSError, json.JSONDecodeError):
            continue
    return None


# ---------------------------------------------------------------------------
# Original helpers
# ---------------------------------------------------------------------------

def get_tty():
    """Get the TTY of the Claude process (parent)"""
    import subprocess

    ppid = os.getppid()
    try:
        result = subprocess.run(
            ["ps", "-p", str(ppid), "-o", "tty="],
            capture_output=True,
            text=True,
            timeout=2,
        )
        tty = result.stdout.strip()
        if tty and tty != "??" and tty != "-":
            if not tty.startswith("/dev/"):
                tty = "/dev/" + tty
            return tty
    except Exception:
        pass

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
            timeout=2,
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
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(TIMEOUT_SECONDS)
        sock.connect(SOCKET_PATH)
        sock.sendall(json.dumps(state).encode())

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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(1)

    session_id = data.get("session_id", "unknown")
    event = data.get("hook_event_name", "")
    cwd = data.get("cwd", "")
    tool_input = data.get("tool_input", {})

    claude_pid = os.getppid()
    tty = get_tty()
    cmux_workspace, cmux_surface = get_cmux_surface()

    state = {
        "session_id": session_id,
        "cwd": cwd,
        "event": event,
        "pid": claude_pid,
        "tty": tty,
    }

    if cmux_workspace and cmux_surface:
        state["cmux_workspace"] = cmux_workspace
        state["cmux_surface"] = cmux_surface

    if event == "UserPromptSubmit":
        state["status"] = "processing"

    elif event == "PreToolUse":
        state["status"] = "running_tool"
        state["tool"] = data.get("tool_name")
        state["tool_input"] = tool_input
        tool_use_id_from_event = data.get("tool_use_id")
        if tool_use_id_from_event:
            state["tool_use_id"] = tool_use_id_from_event
        # Track tool usage for style
        tool_name = data.get("tool_name", "")
        if tool_name:
            _record_tool(tool_name)

    elif event == "PostToolUse":
        state["status"] = "processing"
        state["tool"] = data.get("tool_name")
        state["tool_input"] = tool_input
        tool_use_id_from_event = data.get("tool_use_id")
        if tool_use_id_from_event:
            state["tool_use_id"] = tool_use_id_from_event

    elif event == "PermissionRequest":
        state["status"] = "waiting_for_approval"
        state["tool"] = data.get("tool_name")
        state["tool_input"] = tool_input

        response = send_event(state)

        if response:
            decision = response.get("decision", "ask")
            reason = response.get("reason", "")

            if decision == "allow":
                output = {
                    "hookSpecificOutput": {
                        "hookEventName": "PermissionRequest",
                        "decision": {"behavior": "allow"},
                    }
                }
                print(json.dumps(output))
                sys.exit(0)

            elif decision == "deny":
                _record_denial()
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

        sys.exit(0)

    elif event == "Notification":
        notification_type = data.get("notification_type")
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
        state["status"] = "waiting_for_input"

    elif event == "SessionStart":
        state["status"] = "waiting_for_input"

    elif event == "SessionEnd":
        state["status"] = "ended"
        _record_session_end()
        # Attach the style-derived identity so the Swift app can use it
        user_id = _load_claude_user_id() or "anon"
        style = _load_style()
        if style:
            state["style_identity"] = compute_style_identity(user_id, style)

    elif event == "PreCompact":
        state["status"] = "compacting"

    else:
        state["status"] = "unknown"

    send_event(state)


if __name__ == "__main__":
    main()
