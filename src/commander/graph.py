"""
Grafo SRE — graph.py
======================
StateGraph cíclico con InMemorySaver y Human-in-the-Loop interrupt.

Flujo:
  triage → diagnosis → proposer
                          ↓
                   [awaiting_human_approval?]
                       ↙           ↘
                 postmortem      postmortem (auto)
"""
from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from commander.nodes.diagnosis import diagnosis_node
from commander.nodes.postmortem import postmortem_node
from commander.nodes.proposer import proposer_node
from commander.nodes.triage import triage_node
from commander.state import IncidentState


def _route_after_proposer(state: IncidentState) -> str:
    """
    Enruta hacia postmortem directamente si no requiere aprobación humana,
    o termina el ciclo explícito para que el interrupt_before tome efecto.
    """
    awaiting: bool = state.get("awaiting_human_approval", False)
    if awaiting:
        return "postmortem"  # interrupt_before detendrá antes de aquí
    return "postmortem"


def build_graph() -> StateGraph:
    """Construye el StateGraph SRE sin compilar."""
    graph = StateGraph(IncidentState)

    graph.add_node("triage", triage_node)
    graph.add_node("diagnosis", diagnosis_node)
    graph.add_node("proposer", proposer_node)
    graph.add_node("postmortem", postmortem_node)

    graph.add_edge(START, "triage")
    graph.add_edge("triage", "diagnosis")
    graph.add_edge("diagnosis", "proposer")
    graph.add_edge("proposer", "postmortem")
    graph.add_edge("postmortem", END)

    return graph


def compile_graph() -> object:
    """Compila el grafo con InMemorySaver y interrupt_before=['postmortem'] para HITL."""
    graph = build_graph()
    memory = InMemorySaver()
    return graph.compile(
        checkpointer=memory,
        interrupt_before=["postmortem"],  # HITL: pausa antes de generar postmortem
    )
