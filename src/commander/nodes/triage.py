"""
Nodo: TriageNode — triage.py
==============================
Ingesta métricas SLO y calcula el consumo de error budget (SLO burnrate engine).
Determina la severidad efectiva del incidente y prioriza la respuesta.

Guardrails:
  - #2 (Input Validation): SLOMetric es Pydantic v2 inmutable.
  - #17 (Anti-DoS): max_iterations se inicializa aquí.
"""
from __future__ import annotations

from typing import Any

from commander.state import (
    BurnRateStatus,
    IncidentState,
    IncidentStatus,
    Severity,
    SLOMetric,
)


_CRITICAL_BURN_THRESHOLD = 14.4
_FAST_BURN_THRESHOLD = 2.0
_WARNING_BURN_THRESHOLD = 1.0


def _compute_effective_severity(metrics: list[SLOMetric], declared: Severity) -> Severity:
    """
    Ajusta la severidad según el burnrate real de las métricas SLO.

    Si alguna métrica está en CRITICAL burnrate, eleva a P1.
    Si hay FAST_BURN, asegura al menos P2.
    """
    max_burn = max((m.burn_rate for m in metrics), default=0.0)

    if max_burn >= _CRITICAL_BURN_THRESHOLD:
        return Severity.P1_CRITICAL
    if max_burn >= _FAST_BURN_THRESHOLD and declared in (Severity.P3_MEDIUM, Severity.P4_LOW):
        return Severity.P2_HIGH
    return declared


def triage_node(state: IncidentState) -> dict[str, Any]:
    """
    Nodo TriageNode: evalúa métricas SLO y calcula el burnrate del incidente.

    Returns:
        Parcial del estado con severity, burn_rate_status y status_messages.
    """
    incident_id: str = state.get("incident_id", "INC-UNKNOWN")
    declared_severity: Severity = state.get("severity", Severity.P3_MEDIUM)
    slo_metrics: list[SLOMetric] = state.get("slo_metrics", [])
    messages: list[str] = []

    if not slo_metrics:
        messages.append(f"[TRIAGE] Sin métricas SLO — asumiendo severidad declarada {declared_severity.value}.")
        return {
            "severity": declared_severity,
            "burn_rate_status": BurnRateStatus.NORMAL,
            "status": IncidentStatus.TRIAGED,
            "max_iterations": state.get("max_iterations", 5),
            "iterations": 0,
            "is_complete": False,
            "awaiting_human_approval": False,
            "status_messages": messages,
        }

    # Calcular burnrate status efectivo (peor caso entre todas las métricas)
    worst_burn_rate = max(m.burn_rate for m in slo_metrics)
    min_budget = min(m.error_budget_remaining_percent for m in slo_metrics)

    # Determinar estado
    if worst_burn_rate >= _CRITICAL_BURN_THRESHOLD:
        burn_status = BurnRateStatus.CRITICAL
    elif worst_burn_rate >= _FAST_BURN_THRESHOLD:
        burn_status = BurnRateStatus.FAST_BURN
    elif worst_burn_rate >= _WARNING_BURN_THRESHOLD:
        burn_status = BurnRateStatus.WARNING
    else:
        burn_status = BurnRateStatus.NORMAL

    effective_severity = _compute_effective_severity(slo_metrics, declared_severity)

    messages.append(
        f"[TRIAGE] Incidente {incident_id}: "
        f"Burn rate máximo={worst_burn_rate:.1f}x, "
        f"Budget restante={min_budget:.1f}%, "
        f"Severidad efectiva={effective_severity.value}, "
        f"Estado={burn_status.value}."
    )

    for m in slo_metrics:
        messages.append(
            f"[TRIAGE]   SLO '{m.slo_name}' ({m.service}): "
            f"{m.current_percent:.2f}% (objetivo {m.target_percent:.1f}%), "
            f"burn={m.burn_rate:.1f}x."
        )

    return {
        "severity": effective_severity,
        "burn_rate_status": burn_status,
        "status": IncidentStatus.TRIAGED,
        "max_iterations": state.get("max_iterations", 5),
        "iterations": 0,
        "is_complete": False,
        "awaiting_human_approval": False,
        "status_messages": messages,
    }
