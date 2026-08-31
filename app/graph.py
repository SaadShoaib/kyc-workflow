"""
LangGraph orchestration for the extract -> screen -> score -> review pipeline.

The graph state only tracks *where we are* (applicant_id, and later the
reviewer's decision) — the database rows written by extract_applicant,
screen_applicant, and synthesize_risk remain the actual source of truth.
Nothing about those functions changes; each node here is a thin wrapper that
opens its own DB session, calls the existing plain function, and closes it.

Checkpointing uses a separate langgraph_state.db (orchestration state), kept
apart from kyc.db (applicant data) on purpose.

The interrupt in review_node is the only new behavior: it pauses execution
before a human decision and persists everything to the checkpointer. The
graph does not resume past it until something explicitly calls
graph.invoke(Command(resume=...), config=...) — see the /decision endpoint
in main.py. The /decision endpoint still does the actual status-changing
work; the resume call is bookkeeping so the checkpoint shows the thread
completed.
"""
from contextlib import ExitStack
from typing import Callable, Optional, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.db import SessionLocal
from app.services.extraction import extract_applicant
from app.services.screening import screen_applicant
from app.services.synthesis import synthesize_risk


class GraphState(TypedDict):
    applicant_id: int
    decision: Optional[dict]


def thread_config(applicant_id: int) -> dict:
    return {"configurable": {"thread_id": str(applicant_id)}}


def is_paused_at(state, node_name: str) -> bool:
    """True if the graph is genuinely interrupted at `node_name` (not just
    mid-execution or crashed there)."""
    return state.next == (node_name,) and any(task.interrupts for task in state.tasks)


def has_crashed(state) -> bool:
    """True if the last invoke() attempt raised inside a node — safe to
    retry via graph.invoke(None, config=...), which resumes from the
    checkpoint rather than restarting from START."""
    return any(task.error for task in state.tasks)


def _make_node(service_fn: Callable) -> Callable:
    """Wraps a plain service function (db, applicant_id) -> ... as a graph
    node: opens its own session (nodes run outside FastAPI's Depends(get_db)
    lifecycle), calls the function, closes the session."""
    def node(state: GraphState):
        db = SessionLocal()
        try:
            service_fn(db, state["applicant_id"])
        finally:
            db.close()
        return {}
    return node


extract_node = _make_node(extract_applicant)
screen_node = _make_node(screen_applicant)
score_node = _make_node(synthesize_risk)


def review_node(state: GraphState):
    # Pauses here until something calls graph.invoke(Command(resume=...), config=...).
    decision = interrupt({"applicant_id": state["applicant_id"]})
    return {"decision": decision}


# Held open for the app's lifetime: SqliteSaver.from_conn_string() is a
# context manager, and the connection needs to stay alive across separate
# HTTP requests (e.g. /run now, /decision minutes later) rather than
# closing the instant a `with` block would exit. shutdown_graph() closes it
# on app shutdown.
_stack = ExitStack()


def build_graph():
    checkpointer = _stack.enter_context(
        SqliteSaver.from_conn_string("langgraph_state.db")
    )
    builder = StateGraph(GraphState)
    builder.add_node("extract", extract_node)
    builder.add_node("screen", screen_node)
    builder.add_node("score", score_node)
    builder.add_node("review", review_node)
    builder.add_edge(START, "extract")
    builder.add_edge("extract", "screen")
    builder.add_edge("screen", "score")
    builder.add_edge("score", "review")
    builder.add_edge("review", END)
    return builder.compile(checkpointer=checkpointer)


graph = build_graph()


def shutdown_graph():
    _stack.close()
