from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Callable, Any


@dataclass(frozen=True)
class HarnessConfig:
    max_steps: int = 8
    max_seconds: float = 90.0
    max_tool_calls: int = 12

    def __post_init__(self):
        if self.max_steps < 1:
            raise ValueError("max_steps must be >= 1")
        if self.max_seconds <= 0:
            raise ValueError("max_seconds must be > 0")
        if self.max_tool_calls < 0:
            raise ValueError("max_tool_calls must be >= 0")


def run_bounded_steps(
    step: Callable[[dict[str, Any]], dict[str, Any]],
    initial_state: dict[str, Any],
    config: HarnessConfig,
) -> dict[str, Any]:
    """Execute a caller-supplied step function under hard loop/time/tool limits.

    The step function returns a state dict. Set state['done'] = True to finish.
    Increment state['tool_calls'] when a tool is called. This function does not
    interrupt a blocking tool call; individual tools must also have timeouts.
    """
    state = dict(initial_state)
    state.setdefault("tool_calls", 0)
    started = monotonic()
    history = []

    for number in range(1, config.max_steps + 1):
        if monotonic() - started > config.max_seconds:
            state["stop_reason"] = "time_budget_exceeded"
            break
        if state["tool_calls"] > config.max_tool_calls:
            state["stop_reason"] = "tool_call_budget_exceeded"
            break

        before = state.get("tool_calls", 0)
        next_state = step(dict(state))
        if not isinstance(next_state, dict):
            raise TypeError("step must return a dict")
        state = next_state
        state.setdefault("tool_calls", before)
        history.append({"step": number, "tool_calls": state["tool_calls"]})

        if state.get("tool_calls", 0) > config.max_tool_calls:
            state["stop_reason"] = "tool_call_budget_exceeded"
            break
        if state.get("done"):
            state.setdefault("stop_reason", "completed")
            break
    else:
        state["stop_reason"] = "step_budget_exceeded"

    state["step_history"] = history
    state["elapsed_seconds"] = round(monotonic() - started, 4)
    return state
