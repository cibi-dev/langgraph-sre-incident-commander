"""CLI entry-point para SRE Incident Commander."""
from __future__ import annotations

import sys

from commander.graph import compile_graph
from commander.state import IncidentState, Severity


def main() -> None:
    if len(sys.argv) < 3:
        print("Uso: sre-commander <incident_id> '<description>' [severity]")
        sys.exit(1)

    incident_id = sys.argv[1]
    description = sys.argv[2]
    severity_str = sys.argv[3] if len(sys.argv) > 3 else "p3_medium"

    try:
        severity = Severity(severity_str)
    except ValueError:
        severity = Severity.P3_MEDIUM

    state: IncidentState = {
        "incident_id": incident_id,
        "incident_description": description,
        "severity": severity,
        "affected_services": ["api-gateway"],
        "slo_metrics": [],
        "logs": [],
        "max_iterations": 5,
        "iterations": 0,
    }

    app = compile_graph()
    config = {"configurable": {"thread_id": incident_id}}

    print(f"\n🚨 Iniciando respuesta a incidente: {incident_id}")
    try:
        for step in app.stream(state, config=config):
            node_name, node_state = next(iter(step.items()))
            for msg in (node_state.get("status_messages") or []):
                print(f"  {msg}")
    except Exception as e:
        print(f"  [WARN] Error en stream: {e}")

    print("\n✅ Respuesta finalizada.")


if __name__ == "__main__":
    main()
