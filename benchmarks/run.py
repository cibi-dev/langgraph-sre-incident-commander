"""
Benchmark L3: SRE Incident Commander
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from commander.nodes.diagnosis import diagnosis_node
from commander.nodes.postmortem import postmortem_node
from commander.nodes.proposer import proposer_node
from commander.nodes.triage import triage_node
from commander.state import (
    ActionType,
    BurnRateStatus,
    IncidentState,
    LogEntry,
    RemediationAction,
    Severity,
    SLOMetric,
)


def run_benchmark() -> dict:
    results: dict = {"timestamp": datetime.now(timezone.utc).isoformat(), "steps": {}}

    state: IncidentState = {
        "incident_id": "BENCH-SRE-001",
        "incident_description": "API Gateway 40% error rate surge detected.",
        "severity": Severity.P1_CRITICAL,
        "affected_services": ["api-gateway"],
        "slo_metrics": [SLOMetric(
            service="api-gateway", slo_name="availability-99.9",
            target_percent=99.9, current_percent=95.0,
            error_budget_remaining_percent=5.0, burn_rate=20.0, window_hours=1,
        )],
        "logs": [
            LogEntry(service="api-gateway", level="ERROR",
                     message="DB timeout", error_code="DB_TIMEOUT")
        ] * 10,
        "max_iterations": 5, "iterations": 0,
        "awaiting_human_approval": False, "human_decision": None,
        "human_approved_actions": [], "status_messages": [], "is_complete": False,
    }

    t0 = time.perf_counter()
    r1 = triage_node(state)
    results["steps"]["triage_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    state = {**state, **r1}

    t0 = time.perf_counter()
    r2 = diagnosis_node(state)
    results["steps"]["diagnosis_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    state = {**state, **r2}

    t0 = time.perf_counter()
    r3 = proposer_node(state)
    results["steps"]["proposer_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    state = {**state, **r3, "human_decision": "approve"}

    t0 = time.perf_counter()
    r4 = postmortem_node(state)
    results["steps"]["postmortem_ms"] = round((time.perf_counter() - t0) * 1000, 2)

    total = sum(results["steps"].values())
    results["total_pipeline_ms"] = total
    results["postmortem_generated"] = r4.get("postmortem") is not None
    results["performance_ok"] = total < 500.0

    print(f"📊 Benchmark SRE: {total:.2f}ms total")
    for step, ms in results["steps"].items():
        print(f"   {step}: {ms:.2f}ms")
    print(f"   ✅ Performance OK: {results['performance_ok']}")

    return results


if __name__ == "__main__":
    results = run_benchmark()
    out = Path(__file__).parent / "resultados.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"\n💾 Resultados guardados en {out}")
