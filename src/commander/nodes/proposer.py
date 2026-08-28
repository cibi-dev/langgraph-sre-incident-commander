"""
Nodo: ActionProposer — proposer.py
=====================================
Genera acciones de remediación y activa el punto de interrupción humano
(interrupt_before=['remediate'], Guardrail #16).

Lógica:
  - Selecciona acciones según severidad y causa raíz.
  - Siempre incluye al menos una acción de "investigación manual" como fallback.
  - Las acciones de alto riesgo (ROLLBACK, DRAIN_TRAFFIC) requieren aprobación humana.

Guardrails:
  - #16 (Human-in-the-Loop): awaiting_human_approval=True para acciones mutables.
  - #17 (Anti-DoS): Máximo 5 acciones propuestas por iteración.
"""
from __future__ import annotations

from typing import Any

from commander.state import (
    ActionType,
    BurnRateStatus,
    IncidentState,
    IncidentStatus,
    RemediationAction,
    Severity,
)


_MAX_ACTIONS = 5
_HIGH_RISK_ACTIONS = {ActionType.ROLLBACK, ActionType.DRAIN_TRAFFIC, ActionType.CIRCUIT_BREAK}


def _select_actions(
    severity: Severity,
    burn_rate_status: BurnRateStatus,
    root_cause: str,
    affected_services: list[str],
) -> list[RemediationAction]:
    """
    Selecciona acciones de remediación basadas en severidad y diagnóstico.

    Returns:
        Lista de RemediationAction propuestas (máximo _MAX_ACTIONS).
    """
    actions: list[RemediationAction] = []
    service = affected_services[0] if affected_services else "unknown-service"

    # Acción según burnrate crítico
    if burn_rate_status == BurnRateStatus.CRITICAL:
        actions.append(
            RemediationAction(
                action_id="ACT-001",
                action_type=ActionType.ROLLBACK,
                target_service=service,
                description=(
                    f"Ejecutar rollback inmediato a última versión estable de '{service}'. "
                    "Burnrate crítico detectado (>14.4x): pérdida de error budget acelerada."
                ),
                estimated_recovery_time_minutes=5,
                risk_level="high",
            )
        )
        actions.append(
            RemediationAction(
                action_id="ACT-002",
                action_type=ActionType.DRAIN_TRAFFIC,
                target_service=service,
                description=f"Drenar tráfico de '{service}' hacia instancias sanas.",
                estimated_recovery_time_minutes=2,
                risk_level="critical",
            )
        )
    elif burn_rate_status == BurnRateStatus.FAST_BURN:
        actions.append(
            RemediationAction(
                action_id="ACT-001",
                action_type=ActionType.SCALE_UP,
                target_service=service,
                description=f"Escalar horizontalmente '{service}' para absorber la carga.",
                estimated_recovery_time_minutes=3,
                risk_level="medium",
            )
        )
        actions.append(
            RemediationAction(
                action_id="ACT-002",
                action_type=ActionType.CIRCUIT_BREAK,
                target_service=service,
                description=f"Activar circuit breaker en '{service}' para proteger dependencias.",
                estimated_recovery_time_minutes=1,
                risk_level="high",
            )
        )
    elif burn_rate_status == BurnRateStatus.WARNING:
        actions.append(
            RemediationAction(
                action_id="ACT-001",
                action_type=ActionType.RESTART_SERVICE,
                target_service=service,
                description=f"Reiniciar instancias degradadas de '{service}' de forma gradual.",
                estimated_recovery_time_minutes=10,
                risk_level="low",
            )
        )

    # Siempre: investigación manual como fallback
    actions.append(
        RemediationAction(
            action_id=f"ACT-{len(actions)+1:03d}",
            action_type=ActionType.MANUAL_INVESTIGATION,
            target_service=service,
            description=(
                f"Investigación manual de causa raíz: {root_cause[:200]}. "
                "Revisar dashboards y traces en detalle."
            ),
            estimated_recovery_time_minutes=30,
            risk_level="low",
        )
    )

    return actions[:_MAX_ACTIONS]


def proposer_node(state: IncidentState) -> dict[str, Any]:
    """
    Nodo ActionProposer: genera acciones y activa Human-in-the-Loop (Guardrail #16).

    Returns:
        Parcial del estado con proposed_actions, awaiting_human_approval y status_messages.
    """
    severity: Severity = state.get("severity", Severity.P3_MEDIUM)
    burn_rate_status: BurnRateStatus = state.get("burn_rate_status", BurnRateStatus.NORMAL)
    root_cause: str = state.get("root_cause", "Causa raíz no determinada.")
    affected_services: list[str] = state.get("affected_services", ["unknown-service"])
    incident_id: str = state.get("incident_id", "INC-UNKNOWN")
    messages: list[str] = []

    actions = _select_actions(severity, burn_rate_status, root_cause, affected_services)

    # Detectar si hay acciones de alto riesgo → activar HITL (Guardrail #16)
    has_high_risk = any(a.action_type in _HIGH_RISK_ACTIONS for a in actions)
    requires_approval = severity in (Severity.P1_CRITICAL, Severity.P2_HIGH) or has_high_risk

    messages.append(
        f"[PROPOSER] Incidente {incident_id}: {len(actions)} acciones propuestas. "
        f"Aprobación humana requerida: {'SÍ' if requires_approval else 'NO'}."
    )
    for action in actions:
        messages.append(
            f"[PROPOSER]   [{action.risk_level.upper()}] {action.action_type.value}: {action.description[:100]}…"
        )

    return {
        "proposed_actions": actions,
        "awaiting_human_approval": requires_approval,
        "status": IncidentStatus.DIAGNOSED,
        "status_messages": messages,
    }
