"""
SRE Incident State Models — state.py
======================================
Modelos Pydantic v2 para el sistema de orquestación de incidentes SRE.

Guardrails:
  - Pydantic v2 `extra='forbid'` (#15): No campos inesperados.
  - TypedDict ForensicState compatible con StateGraph LangGraph.
  - #16 (HITL): interrupt_before=['remediate'] forzado en el grafo.
"""
from __future__ import annotations

import operator
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class Severity(str, Enum):
    """Severidad del incidente SRE."""
    P1_CRITICAL = "p1_critical"
    P2_HIGH = "p2_high"
    P3_MEDIUM = "p3_medium"
    P4_LOW = "p4_low"


class IncidentStatus(str, Enum):
    """Estado de procesamiento del incidente."""
    OPEN = "open"
    TRIAGED = "triaged"
    DIAGNOSED = "diagnosed"
    REMEDIATED = "remediated"
    POSTMORTEM_DONE = "postmortem_done"
    CLOSED = "closed"


class BurnRateStatus(str, Enum):
    """Estado del error budget según burnrate."""
    NORMAL = "normal"           # < 1x burnrate
    WARNING = "warning"         # 1x–2x burnrate
    FAST_BURN = "fast_burn"     # 2x–14.4x burnrate (1h/5% window)
    CRITICAL = "critical"       # > 14.4x burnrate (alerta crítica)


class ActionType(str, Enum):
    """Tipo de acción de remediación propuesta."""
    ROLLBACK = "rollback"
    SCALE_UP = "scale_up"
    CIRCUIT_BREAK = "circuit_break"
    RESTART_SERVICE = "restart_service"
    DRAIN_TRAFFIC = "drain_traffic"
    MANUAL_INVESTIGATION = "manual_investigation"


# ---------------------------------------------------------------------------
# SRE Models
# ---------------------------------------------------------------------------


class SLOMetric(BaseModel):
    """Métrica SLO con consumo de error budget."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    service: str = Field(..., min_length=1, max_length=128)
    slo_name: str = Field(..., min_length=1, max_length=128)
    target_percent: float = Field(..., ge=0.0, le=100.0)
    current_percent: float = Field(..., ge=0.0, le=100.0)
    error_budget_remaining_percent: float = Field(..., ge=0.0, le=100.0)
    burn_rate: float = Field(..., ge=0.0, description="Tasa de consumo relativa (1.0 = normal).")
    window_hours: int = Field(default=1, ge=1, le=720)
    measured_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def burn_rate_status(self) -> BurnRateStatus:
        """Determina el estado del burnrate basado en umbrales Google SRE."""
        if self.burn_rate >= 14.4:
            return BurnRateStatus.CRITICAL
        if self.burn_rate >= 2.0:
            return BurnRateStatus.FAST_BURN
        if self.burn_rate >= 1.0:
            return BurnRateStatus.WARNING
        return BurnRateStatus.NORMAL


class LogEntry(BaseModel):
    """Entrada de log para correlación y diagnóstico."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    service: str = Field(..., min_length=1, max_length=128)
    level: str = Field(..., pattern=r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")
    message: str = Field(..., min_length=1, max_length=2048)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    trace_id: str = Field(default="", max_length=64)
    error_code: Optional[str] = Field(default=None, max_length=32)


class RemediationAction(BaseModel):
    """Acción de remediación propuesta por el agente."""

    model_config = ConfigDict(extra="forbid")

    action_id: str = Field(..., min_length=2, max_length=32)
    action_type: ActionType
    target_service: str = Field(..., min_length=1, max_length=128)
    description: str = Field(..., min_length=10, max_length=1024)
    estimated_recovery_time_minutes: int = Field(..., ge=1, le=480)
    risk_level: str = Field(..., pattern=r"^(low|medium|high|critical)$")
    approved_by_human: bool = Field(default=False)


class Postmortem(BaseModel):
    """Postmortem blameless estilo Google SRE."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    incident_id: str = Field(..., min_length=3, max_length=64)
    title: str = Field(..., min_length=5, max_length=256)
    date: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    severity: Severity
    duration_minutes: int = Field(..., ge=1)
    summary: str = Field(..., min_length=20, max_length=4096)
    root_cause: str = Field(..., min_length=10, max_length=2048)
    impact: str = Field(..., min_length=10, max_length=2048)
    timeline: list[str] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)
    lessons_learned: str = Field(..., min_length=10, max_length=2048)
    is_blameless: bool = Field(default=True)


# ---------------------------------------------------------------------------
# LangGraph IncidentState (TypedDict)
# ---------------------------------------------------------------------------

from typing import TypedDict  # noqa: E402


class IncidentState(TypedDict, total=False):
    """
    Estado principal del grafo SRE LangGraph.
    """
    # Entrada del incidente
    incident_id: str
    incident_description: str
    severity: Severity
    affected_services: list[str]

    # Métricas SLO
    slo_metrics: list[SLOMetric]
    burn_rate_status: BurnRateStatus

    # Logs para diagnóstico
    logs: list[LogEntry]
    error_patterns: Annotated[list[str], operator.add]
    root_cause: str

    # Acciones propuestas
    proposed_actions: list[RemediationAction]

    # Control Human-in-the-Loop (Guardrail #16)
    awaiting_human_approval: bool
    human_decision: Optional[str]  # "approve" | "reject" | None
    human_approved_actions: list[str]  # action_ids aprobados

    # Postmortem
    postmortem: Optional[Postmortem]

    # Control anti-DoS (#17)
    iterations: Annotated[int, operator.add]
    max_iterations: int

    # Estado general
    status: IncidentStatus
    status_messages: Annotated[list[str], operator.add]
    is_complete: bool
