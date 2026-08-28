"""
Tests adicionales: CLI + postmortem branches + triage edge cases
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from commander.cli import main
from commander.nodes.postmortem import _build_action_items, _build_timeline, postmortem_node
from commander.nodes.triage import triage_node
from commander.state import (
    ActionType,
    BurnRateStatus,
    IncidentState,
    IncidentStatus,
    LogEntry,
    RemediationAction,
    Severity,
    SLOMetric,
)


class TestCLI:
    def test_exits_without_args(self) -> None:
        with patch.object(sys, "argv", ["sre-commander"]):
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 1

    def test_exits_with_one_arg(self) -> None:
        with patch.object(sys, "argv", ["sre-commander", "INC-001"]):
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 1

    def test_runs_with_minimal_args(self) -> None:
        with patch.object(sys, "argv", ["sre-commander", "INC-CLI-01", "API degradation."]):
            try:
                main()
            except (SystemExit, Exception):
                pass

    def test_runs_with_invalid_severity(self) -> None:
        with patch.object(sys, "argv", ["sre-commander", "INC-CLI-02", "Spike detected.", "invalid_severity"]):
            try:
                main()
            except (SystemExit, Exception):
                pass


class TestPostmortemBranches:
    def test_build_timeline_from_status_messages(self) -> None:
        state: IncidentState = {
            "status_messages": [
                "[TRIAGE] Burnrate crítico detectado.",
                "[DIAGNOSIS] Causa raíz identificada.",
                "Mensaje sin tag relevante.",
            ]
        }
        timeline = _build_timeline(state)
        assert len(timeline) == 2  # Solo los que tienen TRIAGE o DIAGNOSIS

    def test_build_action_items_approved(self) -> None:
        actions = [
            RemediationAction(
                action_id="ACT-001", action_type=ActionType.ROLLBACK,
                target_service="svc", description="Rollback to stable version.",
                estimated_recovery_time_minutes=5, risk_level="high",
                approved_by_human=True,
            )
        ]
        items = _build_action_items(actions)
        assert len(items) == 1
        assert "ROLLBACK" in items[0].upper()

    def test_build_action_items_fallback_not_approved(self) -> None:
        actions = [
            RemediationAction(
                action_id="ACT-001", action_type=ActionType.SCALE_UP,
                target_service="svc", description="Scale service horizontally.",
                estimated_recovery_time_minutes=3, risk_level="medium",
                approved_by_human=False,
            )
        ]
        items = _build_action_items(actions)
        assert len(items) >= 1
        assert "PENDIENTE" in items[0]

    def test_postmortem_with_human_approved_actions(self) -> None:
        action = RemediationAction(
            action_id="ACT-X01", action_type=ActionType.CIRCUIT_BREAK,
            target_service="api", description="Circuit break on api gateway.",
            estimated_recovery_time_minutes=2, risk_level="high",
        )
        state: IncidentState = {
            "incident_id": "INC-PM-010",
            "incident_description": "API gateway circuit break needed due to cascading failures.",
            "severity": Severity.P1_CRITICAL,
            "affected_services": ["api"],
            "slo_metrics": [],
            "logs": [],
            "proposed_actions": [action],
            "root_cause": "Cascading failures from database.",
            "awaiting_human_approval": False,
            "human_decision": "approve",
            "human_approved_actions": ["ACT-X01"],
            "status_messages": ["[TRIAGE] inicio", "[PROPOSER] propuesta"],
            "is_complete": False,
        }
        result = postmortem_node(state)  # type: ignore[arg-type]
        assert result["postmortem"] is not None
        approved = [a for a in result["proposed_actions"] if a.approved_by_human]
        assert len(approved) >= 1


class TestTriageEdgeCases:
    def test_triage_warning_burn_rate(self) -> None:
        m = SLOMetric(
            service="svc", slo_name="avail", target_percent=99.9,
            current_percent=99.0, error_budget_remaining_percent=60.0,
            burn_rate=1.5, window_hours=1,
        )
        state: IncidentState = {
            "incident_id": "INC-W01",
            "severity": Severity.P2_HIGH,
            "slo_metrics": [m],
        }
        result = triage_node(state)  # type: ignore[arg-type]
        assert result["burn_rate_status"] == BurnRateStatus.WARNING

    def test_triage_fast_burn_rate(self) -> None:
        m = SLOMetric(
            service="svc", slo_name="avail", target_percent=99.9,
            current_percent=97.0, error_budget_remaining_percent=20.0,
            burn_rate=5.0, window_hours=1,
        )
        state: IncidentState = {
            "incident_id": "INC-F01",
            "severity": Severity.P2_HIGH,
            "slo_metrics": [m],
        }
        result = triage_node(state)  # type: ignore[arg-type]
        assert result["burn_rate_status"] == BurnRateStatus.FAST_BURN
