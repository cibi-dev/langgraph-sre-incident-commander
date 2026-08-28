"""
Tests L3: SRE Incident Commander — Suite completa (40+ tests)
==============================================================
Cubre: state.py (modelos), triage, diagnosis, proposer, postmortem, graph.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from commander.graph import _route_after_proposer, build_graph
from commander.nodes.diagnosis import (
    _detect_error_patterns,
    _determine_root_cause,
    _truncate_pii,
    diagnosis_node,
)
from commander.nodes.postmortem import _sanitize_blameless, postmortem_node
from commander.nodes.proposer import proposer_node
from commander.nodes.triage import _compute_effective_severity, triage_node
from commander.state import (
    ActionType,
    BurnRateStatus,
    IncidentState,
    IncidentStatus,
    LogEntry,
    Postmortem,
    RemediationAction,
    Severity,
    SLOMetric,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def critical_slo() -> SLOMetric:
    return SLOMetric(
        service="api-gateway",
        slo_name="availability-99.9",
        target_percent=99.9,
        current_percent=95.0,
        error_budget_remaining_percent=5.0,
        burn_rate=20.0,
        window_hours=1,
    )


@pytest.fixture()
def normal_slo() -> SLOMetric:
    return SLOMetric(
        service="worker-service",
        slo_name="latency-p99",
        target_percent=99.0,
        current_percent=98.5,
        error_budget_remaining_percent=70.0,
        burn_rate=0.8,
        window_hours=1,
    )


@pytest.fixture()
def error_log() -> LogEntry:
    return LogEntry(
        service="api-gateway",
        level="ERROR",
        message="Connection timeout to database after 30s",
        error_code="DB_TIMEOUT",
    )


@pytest.fixture()
def basic_incident_state(critical_slo: SLOMetric, error_log: LogEntry) -> IncidentState:
    return {
        "incident_id": "INC-001",
        "incident_description": "API Gateway serving 40% error rate — 5xx spike detected by SLO monitoring.",
        "severity": Severity.P1_CRITICAL,
        "affected_services": ["api-gateway", "db-primary"],
        "slo_metrics": [critical_slo],
        "logs": [error_log] * 5,
        "max_iterations": 5,
        "iterations": 0,
        "awaiting_human_approval": False,
        "human_decision": None,
        "human_approved_actions": [],
        "status_messages": [],
        "is_complete": False,
    }


# ---------------------------------------------------------------------------
# Tests: SLOMetric
# ---------------------------------------------------------------------------

class TestSLOMetric:
    def test_valid_slo_metric(self, critical_slo: SLOMetric) -> None:
        assert critical_slo.service == "api-gateway"
        assert critical_slo.burn_rate == 20.0

    def test_burn_rate_status_critical(self, critical_slo: SLOMetric) -> None:
        assert critical_slo.burn_rate_status == BurnRateStatus.CRITICAL

    def test_burn_rate_status_fast_burn(self, normal_slo: SLOMetric) -> None:
        fast = SLOMetric(
            service="svc", slo_name="avail", target_percent=99.9,
            current_percent=98.0, error_budget_remaining_percent=30.0,
            burn_rate=3.5, window_hours=1,
        )
        assert fast.burn_rate_status == BurnRateStatus.FAST_BURN

    def test_burn_rate_status_warning(self) -> None:
        m = SLOMetric(
            service="svc", slo_name="avail", target_percent=99.9,
            current_percent=99.0, error_budget_remaining_percent=60.0,
            burn_rate=1.5, window_hours=1,
        )
        assert m.burn_rate_status == BurnRateStatus.WARNING

    def test_burn_rate_status_normal(self, normal_slo: SLOMetric) -> None:
        assert normal_slo.burn_rate_status == BurnRateStatus.NORMAL

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            SLOMetric(
                service="svc", slo_name="avail", target_percent=99.9,
                current_percent=98.0, error_budget_remaining_percent=30.0,
                burn_rate=1.0, window_hours=1,
                injected="BAD",  # type: ignore[call-arg]
            )

    def test_frozen_slo(self, critical_slo: SLOMetric) -> None:
        with pytest.raises(Exception):
            critical_slo.burn_rate = 0.0  # type: ignore[misc]

    def test_target_percent_range(self) -> None:
        with pytest.raises(ValidationError):
            SLOMetric(
                service="svc", slo_name="avail", target_percent=101.0,  # > 100
                current_percent=98.0, error_budget_remaining_percent=30.0,
                burn_rate=1.0, window_hours=1,
            )


# ---------------------------------------------------------------------------
# Tests: TriageNode
# ---------------------------------------------------------------------------

class TestTriageNode:
    def test_triage_critical_burnrate(self, critical_slo: SLOMetric) -> None:
        state = {
            "incident_id": "INC-T01",
            "severity": Severity.P2_HIGH,
            "slo_metrics": [critical_slo],
        }
        result = triage_node(state)  # type: ignore[arg-type]
        assert result["severity"] == Severity.P1_CRITICAL
        assert result["burn_rate_status"] == BurnRateStatus.CRITICAL

    def test_triage_normal_burnrate(self, normal_slo: SLOMetric) -> None:
        state = {
            "incident_id": "INC-T02",
            "severity": Severity.P3_MEDIUM,
            "slo_metrics": [normal_slo],
        }
        result = triage_node(state)  # type: ignore[arg-type]
        assert result["burn_rate_status"] == BurnRateStatus.NORMAL

    def test_triage_no_metrics(self) -> None:
        state = {
            "incident_id": "INC-T03",
            "severity": Severity.P4_LOW,
            "slo_metrics": [],
        }
        result = triage_node(state)  # type: ignore[arg-type]
        assert result["status"] == IncidentStatus.TRIAGED
        assert result["burn_rate_status"] == BurnRateStatus.NORMAL

    def test_compute_effective_severity_critical(self, critical_slo: SLOMetric) -> None:
        result = _compute_effective_severity([critical_slo], Severity.P4_LOW)
        assert result == Severity.P1_CRITICAL

    def test_compute_effective_severity_fast_burn_elevates(self) -> None:
        fast_slo = SLOMetric(
            service="svc", slo_name="avail", target_percent=99.9,
            current_percent=97.0, error_budget_remaining_percent=20.0,
            burn_rate=5.0, window_hours=1,
        )
        result = _compute_effective_severity([fast_slo], Severity.P4_LOW)
        assert result == Severity.P2_HIGH

    def test_compute_effective_severity_no_elevation_for_p1(self, normal_slo: SLOMetric) -> None:
        result = _compute_effective_severity([normal_slo], Severity.P1_CRITICAL)
        assert result == Severity.P1_CRITICAL

    def test_triage_status_messages_populated(self, critical_slo: SLOMetric) -> None:
        state = {
            "incident_id": "INC-T04",
            "severity": Severity.P1_CRITICAL,
            "slo_metrics": [critical_slo],
        }
        result = triage_node(state)  # type: ignore[arg-type]
        assert any("TRIAGE" in m for m in result["status_messages"])

    def test_triage_initializes_iterations(self, critical_slo: SLOMetric) -> None:
        state = {
            "incident_id": "INC-T05",
            "severity": Severity.P1_CRITICAL,
            "slo_metrics": [critical_slo],
        }
        result = triage_node(state)  # type: ignore[arg-type]
        assert result["iterations"] == 0


# ---------------------------------------------------------------------------
# Tests: DiagnosisNode
# ---------------------------------------------------------------------------

class TestDiagnosisNode:
    def test_diagnosis_detects_error_patterns(self, error_log: LogEntry) -> None:
        logs = [error_log] * 5
        patterns = _detect_error_patterns(logs)
        assert any("DB_TIMEOUT" in p for p in patterns)

    def test_diagnosis_no_patterns_below_threshold(self, error_log: LogEntry) -> None:
        logs = [error_log] * 2  # < 3, threshold
        patterns = _detect_error_patterns(logs)
        assert not any("DB_TIMEOUT" in p for p in patterns)

    def test_truncate_pii(self) -> None:
        long_msg = "A" * 500
        assert len(_truncate_pii(long_msg)) == 200

    def test_truncate_pii_short_message(self) -> None:
        msg = "Short message"
        assert _truncate_pii(msg) == msg

    def test_determine_root_cause_no_logs(self) -> None:
        result = _determine_root_cause([], [])
        assert "Sin logs" in result

    def test_determine_root_cause_no_patterns(self, error_log: LogEntry) -> None:
        result = _determine_root_cause([], [error_log])
        assert "Sin patrones" in result

    def test_determine_root_cause_with_patterns(self, error_log: LogEntry) -> None:
        patterns = ["Patrón detectado: DB_TIMEOUT"]
        logs = [error_log] * 3
        result = _determine_root_cause(patterns, logs)
        assert "api-gateway" in result

    def test_diagnosis_node_with_logs(self, error_log: LogEntry) -> None:
        state = {
            "incident_id": "INC-D01",
            "logs": [error_log] * 5,
        }
        result = diagnosis_node(state)  # type: ignore[arg-type]
        assert result["status"] == IncidentStatus.DIAGNOSED
        assert "root_cause" in result

    def test_diagnosis_node_no_logs(self) -> None:
        state = {
            "incident_id": "INC-D02",
            "logs": [],
        }
        result = diagnosis_node(state)  # type: ignore[arg-type]
        assert result["status"] == IncidentStatus.DIAGNOSED
        assert "Sin logs" in result["root_cause"]

    def test_log_entry_invalid_level(self) -> None:
        with pytest.raises(ValidationError):
            LogEntry(service="svc", level="TRACE", message="test")


# ---------------------------------------------------------------------------
# Tests: ActionProposer
# ---------------------------------------------------------------------------

class TestProposerNode:
    def test_critical_incident_requires_hitl(self, basic_incident_state: IncidentState) -> None:
        state = {**basic_incident_state, "burn_rate_status": BurnRateStatus.CRITICAL, "root_cause": "DB timeout."}
        result = proposer_node(state)  # type: ignore[arg-type]
        assert result["awaiting_human_approval"] is True

    def test_normal_incident_no_hitl(self, basic_incident_state: IncidentState) -> None:
        state = {
            **basic_incident_state,
            "severity": Severity.P4_LOW,
            "burn_rate_status": BurnRateStatus.NORMAL,
            "root_cause": "Intermittent network glitch.",
        }
        result = proposer_node(state)  # type: ignore[arg-type]
        assert len(result["proposed_actions"]) >= 1

    def test_actions_limited_to_max(self, basic_incident_state: IncidentState) -> None:
        state = {**basic_incident_state, "burn_rate_status": BurnRateStatus.CRITICAL, "root_cause": "DB crash."}
        result = proposer_node(state)  # type: ignore[arg-type]
        assert len(result["proposed_actions"]) <= 5

    def test_always_has_manual_investigation(self, basic_incident_state: IncidentState) -> None:
        state = {**basic_incident_state, "burn_rate_status": BurnRateStatus.CRITICAL, "root_cause": "Unknown."}
        result = proposer_node(state)  # type: ignore[arg-type]
        action_types = [a.action_type for a in result["proposed_actions"]]
        assert ActionType.MANUAL_INVESTIGATION in action_types

    def test_fast_burn_proposes_scale_up(self, basic_incident_state: IncidentState) -> None:
        state = {**basic_incident_state, "burn_rate_status": BurnRateStatus.FAST_BURN, "root_cause": "High traffic."}
        result = proposer_node(state)  # type: ignore[arg-type]
        action_types = [a.action_type for a in result["proposed_actions"]]
        assert ActionType.SCALE_UP in action_types

    def test_warning_proposes_restart(self, basic_incident_state: IncidentState) -> None:
        state = {
            **basic_incident_state,
            "severity": Severity.P3_MEDIUM,
            "burn_rate_status": BurnRateStatus.WARNING,
            "root_cause": "Memory leak.",
        }
        result = proposer_node(state)  # type: ignore[arg-type]
        action_types = [a.action_type for a in result["proposed_actions"]]
        assert ActionType.RESTART_SERVICE in action_types


# ---------------------------------------------------------------------------
# Tests: PostmortemNode
# ---------------------------------------------------------------------------

class TestPostmortemNode:
    def test_generates_blameless_postmortem(self, basic_incident_state: IncidentState) -> None:
        action = RemediationAction(
            action_id="ACT-001", action_type=ActionType.ROLLBACK,
            target_service="api-gateway", description="Rollback to v1.2.3.",
            estimated_recovery_time_minutes=5, risk_level="high",
        )
        state = {
            **basic_incident_state,
            "proposed_actions": [action],
            "root_cause": "Database connection pool exhausted.",
            "human_decision": "approve",
            "awaiting_human_approval": False,
        }
        result = postmortem_node(state)  # type: ignore[arg-type]
        assert result["postmortem"] is not None
        assert result["postmortem"].is_blameless is True

    def test_aborts_without_approval(self, basic_incident_state: IncidentState) -> None:
        state = {
            **basic_incident_state,
            "proposed_actions": [],
            "root_cause": "Unknown.",
            "awaiting_human_approval": True,
            "human_decision": "reject",
        }
        result = postmortem_node(state)  # type: ignore[arg-type]
        assert result.get("postmortem") is None
        assert result["is_complete"] is True

    def test_sanitizes_blame_language(self) -> None:
        text = "La culpa es del ingeniero responsable de la falla del sistema."
        cleaned = _sanitize_blameless(text)
        assert "culpa" not in cleaned.lower()

    def test_postmortem_model_validation(self) -> None:
        pm = Postmortem(
            incident_id="INC-PM01",
            title="Test Postmortem Title",
            severity=Severity.P1_CRITICAL,
            duration_minutes=30,
            summary="Servicio degradado por agotamiento de conexiones a base de datos.",
            root_cause="Pool de conexiones agotado por incremento súbito de tráfico.",
            impact="50% de requests fallando durante 30 minutos.",
            timeline=["10:00: Alerta disparada", "10:05: Triage completado"],
            action_items=["Aumentar pool de conexiones", "Revisar límites de autoscaling"],
            lessons_learned="Los umbrales de alerta deben ajustarse al patrón de tráfico real.",
        )
        assert pm.is_blameless is True
        assert pm.duration_minutes == 30

    def test_postmortem_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            Postmortem(
                incident_id="INC-PM02",
                title="Test",
                severity=Severity.P2_HIGH,
                duration_minutes=10,
                summary="Summary of the incident that is long enough.",
                root_cause="Root cause identified here.",
                impact="Impact statement here.",
                lessons_learned="Lessons learned from this incident.",
                hacker_field="INJECTION",  # type: ignore[call-arg]
            )


# ---------------------------------------------------------------------------
# Tests: Graph
# ---------------------------------------------------------------------------

class TestGraph:
    def test_router_to_postmortem_when_awaiting(self) -> None:
        state = {"awaiting_human_approval": True, "is_complete": False}
        assert _route_after_proposer(state) == "postmortem"  # type: ignore[arg-type]

    def test_router_to_postmortem_when_not_awaiting(self) -> None:
        state = {"awaiting_human_approval": False, "is_complete": False}
        assert _route_after_proposer(state) == "postmortem"  # type: ignore[arg-type]

    def test_build_graph_has_nodes(self) -> None:
        graph = build_graph()
        for node in ["triage", "diagnosis", "proposer", "postmortem"]:
            assert node in graph.nodes

    def test_remediation_action_low_risk(self) -> None:
        action = RemediationAction(
            action_id="ACT-LR01", action_type=ActionType.MANUAL_INVESTIGATION,
            target_service="service-x",
            description="Manually investigate the root cause of the issue.",
            estimated_recovery_time_minutes=60,
            risk_level="low",
        )
        assert action.approved_by_human is False

    def test_remediation_action_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            RemediationAction(
                action_id="ACT-BAD", action_type=ActionType.ROLLBACK,
                target_service="svc",
                description="Bad action with injection.",
                estimated_recovery_time_minutes=5,
                risk_level="high",
                injected="INJECTION",  # type: ignore[call-arg]
            )
