"""A structured record of what the agent did -- the "decision tracer".

Two kinds of ordered events:
  * tool_call -- an external action: a model call (recorded by the LLM client, tagged
    with cache hit/miss), plus checkout / apply_edits / validate / coverage from the agent.
  * decision -- an internal choice: localize terms+candidates, the evidence + the
    model-ranked targets, each repair attempt's outcome, the final status.

This is orchestration-level: `validate` shows up as one event with its stage outcome
(build/vet/repro), not as three separate leaf calls. It captures what a reviewer of an
agentic system needs -- what the agent decided and which tools it invoked, in order.
Writing is best-effort and never raises.
"""
from __future__ import annotations

import json
import time
from pathlib import Path


class Tracer:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self._t0 = time.time()

    def _add(self, kind: str, name: str, fields: dict) -> None:
        self.events.append({"seq": len(self.events), "type": kind, "name": name,
                            "t_ms": int((time.time() - self._t0) * 1000), **fields})

    def tool_call(self, name: str, **fields) -> None:
        self._add("tool_call", name, fields)

    def decision(self, name: str, **fields) -> None:
        self._add("decision", name, fields)

    @property
    def tool_call_count(self) -> int:
        return sum(1 for e in self.events if e["type"] == "tool_call")

    def to_dict(self) -> dict:
        return {"tool_call_count": self.tool_call_count,
                "event_count": len(self.events),
                "events": self.events}

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def write(self, path: str | Path) -> None:
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(self.to_json(), encoding="utf-8")
        except OSError:
            pass
