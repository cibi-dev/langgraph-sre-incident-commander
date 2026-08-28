"""
Nodo: DiagnosisNode — diagnosis.py
=====================================
Correlación de logs y detección de causa raíz (stream-log-aggregator).

Lógica:
  - Agrupa logs por servicio y nivel de severidad.
  - Detecta patrones de error recurrentes (>= 3 ocurrencias del mismo error_code).
  - Genera un diagnóstico de causa raíz basado en los patrones detectados.
  - Prioriza servicios con más ERRORs/CRITICALs.

Guardrails:
  - #5 (No PII logging): Los mensajes de log se truncan a 200 chars.
  - #2 (Input Validation): LogEntry es Pydantic v2 inmutable.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from commander.state import (
    IncidentState,
    IncidentStatus,
    LogEntry,
)


_MAX_LOGS = 1000
_PATTERN_THRESHOLD = 3
_HIGH_SEVERITY_LEVELS = {"ERROR", "CRITICAL"}
_LOG_MESSAGE_MAX = 200


def _truncate_pii(message: str) -> str:
    """Trunca mensajes a 200 chars para evitar exposición de PII (Guardrail #5)."""
    return message[:_LOG_MESSAGE_MAX] if len(message) > _LOG_MESSAGE_MAX else message


def _detect_error_patterns(logs: list[LogEntry]) -> list[str]:
    """
    Detecta patrones de error recurrentes en los logs.

    Returns:
        Lista de strings describiendo patrones detectados.
    """
    # Contar ocurrencias de error_code
    error_codes = [
        log.error_code for log in logs
        if log.error_code and log.level in _HIGH_SEVERITY_LEVELS
    ]
    code_counts = Counter(error_codes)

    patterns: list[str] = []
    for code, count in code_counts.most_common():
        if count >= _PATTERN_THRESHOLD:
            patterns.append(
                f"Patrón detectado: error_code='{code}' aparece {count} veces en logs de alta severidad."
            )

    # Detectar servicios con alta tasa de error
    service_errors: Counter[str] = Counter(
        log.service for log in logs if log.level in _HIGH_SEVERITY_LEVELS
    )
    for service, count in service_errors.most_common(3):
        if count >= _PATTERN_THRESHOLD:
            patterns.append(
                f"Servicio '{service}' con alta tasa de error: {count} eventos ERROR/CRITICAL."
            )

    return patterns


def _determine_root_cause(patterns: list[str], logs: list[LogEntry]) -> str:
    """
    Genera hipótesis de causa raíz basada en patrones y logs.

    Returns:
        String describiendo la causa raíz más probable.
    """
    if not logs:
        return "Sin logs disponibles para diagnóstico."

    if not patterns:
        return "Sin patrones de error significativos detectados. Causa raíz indeterminada."

    # Servicio más afectado
    service_errors: Counter[str] = Counter(
        log.service for log in logs if log.level in _HIGH_SEVERITY_LEVELS
    )
    most_affected = service_errors.most_common(1)[0][0] if service_errors else "desconocido"

    # Mensaje de error más reciente
    recent_errors = [
        log for log in sorted(logs, key=lambda l: l.timestamp, reverse=True)
        if log.level in _HIGH_SEVERITY_LEVELS
    ]
    last_error_msg = _truncate_pii(recent_errors[0].message) if recent_errors else "N/A"

    return (
        f"Causa raíz probable: falla en servicio '{most_affected}'. "
        f"Último error: '{last_error_msg}'. "
        f"Patrones detectados: {len(patterns)}."
    )


def diagnosis_node(state: IncidentState) -> dict[str, Any]:
    """
    Nodo DiagnosisNode: correlaciona logs y determina causa raíz del incidente.

    Returns:
        Parcial del estado con root_cause, error_patterns y status_messages.
    """
    logs: list[LogEntry] = state.get("logs", [])[:_MAX_LOGS]
    incident_id: str = state.get("incident_id", "INC-UNKNOWN")
    messages: list[str] = []

    if not logs:
        messages.append(f"[DIAGNOSIS] Incidente {incident_id}: sin logs para analizar.")
        return {
            "root_cause": "Sin logs disponibles.",
            "error_patterns": ["Sin logs para diagnóstico."],
            "status": IncidentStatus.DIAGNOSED,
            "status_messages": messages,
        }

    patterns = _detect_error_patterns(logs)
    root_cause = _determine_root_cause(patterns, logs)

    messages.append(
        f"[DIAGNOSIS] Incidente {incident_id}: {len(logs)} logs analizados, "
        f"{len(patterns)} patrones detectados."
    )
    messages.append(f"[DIAGNOSIS] Causa raíz: {root_cause}")

    return {
        "root_cause": root_cause,
        "error_patterns": patterns,
        "status": IncidentStatus.DIAGNOSED,
        "status_messages": messages,
    }
