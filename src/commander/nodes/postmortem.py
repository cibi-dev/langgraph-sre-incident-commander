"""
Nodo: PostmortemNode — postmortem.py
=======================================
Generador de postmortem blameless estilo Google SRE.

Genera un documento estructurado con: título, resumen, timeline de eventos,
causa raíz, impacto, acciones correctivas y lecciones aprendidas.
Garantiza que el documento sea "blameless" (sin culpabilización personal).

Guardrails:
  - #3 (Output Sanitization): Sanitiza contenido antes de persistir.
  - #16 (HITL): Solo genera si human_decision='approve' o no hay acciones de riesgo.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from commander.state import (
    IncidentState,
    IncidentStatus,
    Postmortem,
    RemediationAction,
    Severity,
)


_BLAME_PATTERNS = re.compile(
    r"\b(culpa|culpó|culpable|negligencia|responsable\s+de\s+la\s+falla|error\s+humano\s+de)\b",
    re.IGNORECASE,
)


def _sanitize_blameless(text: str) -> str:
    """
    Reemplaza frases culpabilizadoras por lenguaje sistémico blameless.

    Returns:
        Texto sanitizado sin lenguaje de culpa personal.
    """
    return _BLAME_PATTERNS.sub("[factor sistémico]", text)


def _build_timeline(state: IncidentState) -> list[str]:
    """Construye el timeline del incidente desde los status_messages."""
    timeline: list[str] = []
    now = datetime.now(timezone.utc)
    for msg in (state.get("status_messages") or []):
        if any(tag in msg for tag in ("[TRIAGE]", "[DIAGNOSIS]", "[PROPOSER]")):
            timeline.append(f"{now.isoformat()}: {msg[:200]}")
    return timeline[:20]  # Max 20 eventos en timeline


def _build_action_items(actions: list[RemediationAction]) -> list[str]:
    """Convierte acciones aprobadas en ítems de acción para el postmortem."""
    items: list[str] = []
    for action in actions:
        if action.approved_by_human:
            items.append(
                f"[{action.action_id}] {action.action_type.value.upper()} en '{action.target_service}': "
                f"{action.description[:150]}"
            )
    if not items:
        # Fallback: todas las acciones como ítems pendientes
        for action in actions[:3]:
            items.append(
                f"[{action.action_id}] PENDIENTE: {action.action_type.value} en '{action.target_service}'"
            )
    return items


def postmortem_node(state: IncidentState) -> dict[str, Any]:
    """
    Nodo PostmortemNode: genera postmortem blameless Google SRE.

    Returns:
        Parcial del estado con postmortem, status e is_complete.
    """
    incident_id: str = state.get("incident_id", "INC-UNKNOWN")
    incident_description: str = state.get("incident_description", "Sin descripción.")
    severity: Severity = state.get("severity", Severity.P3_MEDIUM)
    root_cause: str = state.get("root_cause", "Causa raíz no determinada.")
    proposed_actions: list[RemediationAction] = state.get("proposed_actions", [])
    human_decision: str | None = state.get("human_decision")
    awaiting: bool = state.get("awaiting_human_approval", False)
    messages: list[str] = []

    # Verificar aprobación humana si se requería (Guardrail #16)
    if awaiting and human_decision != "approve":
        messages.append(
            f"[POSTMORTEM] Generación pausada: decisión humana='{human_decision}'. "
            "Postmortem no generado."
        )
        return {
            "is_complete": True,
            "status": IncidentStatus.CLOSED,
            "status_messages": messages,
        }

    # Marcar acciones como aprobadas si hay aprobación humana
    approved_ids: list[str] = state.get("human_approved_actions", [])
    finalized_actions: list[RemediationAction] = []
    for action in proposed_actions:
        if action.action_id in approved_ids or human_decision == "approve":
            finalized_actions.append(
                action.model_copy(update={"approved_by_human": True})
            )
        else:
            finalized_actions.append(action)

    # Construir postmortem
    raw_summary = _sanitize_blameless(
        f"Incidente {incident_id} ({severity.value}): {incident_description[:300]}. "
        f"Se activó el flujo de respuesta automática con {len(proposed_actions)} acciones propuestas."
    )
    raw_root_cause = _sanitize_blameless(root_cause)
    raw_lessons = _sanitize_blameless(
        f"El sistema de monitoreo detectó la degradación con el burnrate de SLO. "
        f"La automatización del runbook redujo el MTTR. "
        f"Se recomienda revisar los umbrales de alerta para el servicio afectado."
    )

    timeline = _build_timeline(state)
    action_items = _build_action_items(finalized_actions)

    # Calcular duración estimada del incidente
    all_msgs: list[str] = state.get("status_messages") or []
    duration = max(len(all_msgs) * 2, 5)  # Estimación simple: 2min por mensaje, min 5min

    postmortem = Postmortem(
        incident_id=incident_id,
        title=f"[{severity.value.upper()}] Incidente {incident_id} — {incident_description[:80]}",
        severity=severity,
        duration_minutes=duration,
        summary=raw_summary,
        root_cause=raw_root_cause,
        impact=(
            f"Servicios afectados: {', '.join(state.get('affected_services', ['N/A']))}. "
            f"Error budget consumido. {len(state.get('logs', []))} eventos de error registrados."
        ),
        timeline=timeline,
        action_items=action_items,
        lessons_learned=raw_lessons,
        is_blameless=True,
    )

    messages.append(
        f"[POSTMORTEM] Postmortem blameless generado para incidente {incident_id}. "
        f"Duración estimada: {duration}min. Ítems de acción: {len(action_items)}."
    )

    return {
        "postmortem": postmortem,
        "proposed_actions": finalized_actions,
        "is_complete": True,
        "status": IncidentStatus.POSTMORTEM_DONE,
        "awaiting_human_approval": False,
        "status_messages": messages,
    }
