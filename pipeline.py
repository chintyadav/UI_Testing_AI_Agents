"""
pipeline.py
═══════════════════════════════════════════════════════════════════════════════
OVERVIEW
────────
This is the single entry point for the entire Playwright test generation
system.  It wires four independent agent modules into a LangGraph pipeline
and runs them in sequence:

    query_agent.py     → Playwright browser + real locator capture
    planner_agent.py   → LLM step-plan from locators
    generator_agent.py → LLM Page Objects + deterministic spec file
    validator_agent.py → static analysis on generated JS

USAGE
─────
  # Run with a query string:
  python pipeline.py --query "login and add ADIDAS ORIGINAL to cart"

  # Interactive mode (prompt loop):
  python pipeline.py

  # Import and call from another script:
  from pipeline import run_pipeline
  final_state = run_pipeline("login and verify order history")

PIPELINE GRAPH
──────────────
  [START]
     │
     ▼
  query_node         ← query_agent.run_query_agent()
     │
     ▼
  planner_node       ← planner_agent.run_planner_agent()
     │
     ▼
  generator_node     ← generator_agent.run_generator_agent()
     │
     ▼
  validator_node     ← validator_agent.run_validator_agent()
     │
     ▼
  [END]

Every node receives the full PipelineState and returns an updated copy.
No global state is mutated.  Errors are accumulated in state["errors"] and
printed at the end — they do not abort the pipeline unless a node explicitly
raises an exception.

PIPELINE STATE SCHEMA
──────────────────────
    {
        # ── Input ───────────────────────────────────────────────────────
        "query":              str,          # user query (plain English)

        # ── query_node outputs ──────────────────────────────────────────
        "actions":            list[str],    # raw action tokens from LLM
        "pages_data":         list[dict],   # page snapshots from Playwright
        "filtered_locators":  dict,         # LLM-filtered relevant locators

        # ── planner_node outputs ─────────────────────────────────────────
        "plan_text":          str,          # raw LLM plan text
        "parsed_pages":       dict,         # page_name → { class, methods }

        # ── generator_node outputs ───────────────────────────────────────
        "generated_files":    dict,         # rel_path → JS code string

        # ── validator_node outputs ───────────────────────────────────────
        "validation_report":  list[str],    # one entry per checked file
        "success":            bool,         # True if no ERROR-level issues

        # ── Shared ───────────────────────────────────────────────────────
        "errors":             list[str],    # non-fatal pipeline errors
    }

OUTPUT FILES
─────────────
After a successful run, the output/ directory will contain:

  output/
  ├── locator_map.md             # full locator table per page (human review)
  ├── raw_locators.json          # all captured elements as JSON
  ├── relevant_locators.json     # LLM-filtered relevant locators
  ├── planner_output.md          # step-by-step automation plan
  ├── validation_report.md       # static analysis results
  ├── pages/
  │   ├── LoginPage.js           # one Page Object per page
  │   ├── DashboardPage.js
  │   └── ...
  └── tests/
      └── alltest.spec.js        # end-to-end Playwright spec

RUNNING THE GENERATED TESTS
────────────────────────────
  # Install Playwright if not already done:
  npm init playwright@latest

  # Run with visible browser:
  npx playwright test ./output/tests/alltest.spec.js --headed

  # Run headless:
  npx playwright test ./output/tests/alltest.spec.js

  # Run with UI mode (recommended for debugging):
  npx playwright test ./output/tests/alltest.spec.js --ui

DEPENDENCIES
────────────
  pip install langgraph langchain-groq playwright python-dotenv
  playwright install chromium

ENVIRONMENT VARIABLES (.env)
─────────────────────────────
  GROQ_API_KEY      – required (get from https://console.groq.com)
  SITE_USERNAME     – optional (defaults to demo account)
  SITE_PASSWORD     – optional (defaults to demo password)
"""

from __future__ import annotations

import argparse
import os
from typing import TypedDict

from dotenv import load_dotenv
from langgraph.graph import StateGraph, START, END

# ── Import each agent module (each is independently runnable and testable) ──
from query_agent     import run_query_agent
from planner_agent   import run_planner_agent
from generator_agent import run_generator_agent
from validator_agent    import run_validator_agent
from test_runner_agent import run_test_runner_agent, MAX_REPAIR_LOOPS

load_dotenv()

os.makedirs("output", exist_ok=True)


# ═════════════════════════════════════════════════════════════════════════════
#  SHARED PIPELINE STATE
#  TypedDict keeps every node's contract explicit and IDE-friendly.
#  All four agent modules import this type for their own type annotations.
# ═════════════════════════════════════════════════════════════════════════════

class PipelineState(TypedDict):
    # Input
    query: str

    # Outputs from query_agent
    actions:            list[str]
    pages_data:         list[dict]
    filtered_locators:  dict

    # Outputs from planner_agent
    plan_text:          str
    parsed_pages:       dict

    # Outputs from generator_agent
    generated_files:    dict

    # Outputs from validator_agent
    validation_report:  list[str]
    success:            bool

    # Outputs from test_runner_agent
    test_run_result:    dict
    repair_loop_count:  int

    # Shared across all nodes
    errors:             list[str]


# ═════════════════════════════════════════════════════════════════════════════
#  PIPELINE BUILDER
#  Constructs and compiles the LangGraph DAG.  The compiled graph is
#  cached so repeated calls to run_pipeline() reuse the same graph object.
# ═════════════════════════════════════════════════════════════════════════════

_compiled_graph = None   # module-level cache


def _should_repair(state: PipelineState) -> str:
    """
    Conditional edge after test_runner_node.
    Returns "repair" if tests failed and we have repair attempts left.
    Returns "end"    if tests passed OR we have exhausted repair loops.
    """
    result     = state.get("test_run_result", {})
    loop_count = state.get("repair_loop_count", 0)

    if result.get("passed", False):
        return "end"
    if loop_count >= MAX_REPAIR_LOOPS:
        print(f"\n  ⚠️  Max repair loops ({MAX_REPAIR_LOOPS}) reached — exiting loop")
        return "end"
    print(f"\n  🔄  Tests failed — repair loop {loop_count}/{MAX_REPAIR_LOOPS}...")
    return "repair"


def _build_pipeline():
    """
    Build and compile the LangGraph StateGraph.

    Linear pipeline:
        START → query_node → planner_node → generator_node → validator_node
                                                                     ↓
                                                            test_runner_node
                                                            ↙           ↘
                                                     (pass→END)    (fail→repair)
                                                                        ↓
                                                               generator_node ← (loop)

    The repair loop re-runs generator_node with failure context injected into
    state["test_run_result"]["failures"], so the LLM can produce corrected
    Page Objects.  Loop is capped at MAX_REPAIR_LOOPS.
    """
    graph = StateGraph(PipelineState)

    graph.add_node("query_node",       run_query_agent)
    graph.add_node("planner_node",     run_planner_agent)
    graph.add_node("generator_node",   run_generator_agent)
    graph.add_node("validator_node",   run_validator_agent)
    graph.add_node("test_runner_node", run_test_runner_agent)

    # Linear path
    graph.add_edge(START,              "query_node")
    graph.add_edge("query_node",       "planner_node")
    graph.add_edge("planner_node",     "generator_node")
    graph.add_edge("generator_node",   "validator_node")
    graph.add_edge("validator_node",   "test_runner_node")

    # Conditional repair loop
    graph.add_conditional_edges(
        "test_runner_node",
        _should_repair,
        {
            "end":    END,
            "repair": "generator_node",   # re-run generator with failure context
        },
    )

    return graph.compile()


def _get_pipeline():
    """Return the cached compiled pipeline, building it on first call."""
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = _build_pipeline()
    return _compiled_graph


# ═════════════════════════════════════════════════════════════════════════════
#  INITIAL STATE FACTORY
#  Centralises the default values so callers never have to know the full
#  schema — they just pass the query string.
# ═════════════════════════════════════════════════════════════════════════════

def _make_initial_state(query: str) -> PipelineState:
    """Return a fully-initialised PipelineState with all keys set to defaults."""
    return PipelineState(
        query             = query,
        actions           = [],
        pages_data        = [],
        filtered_locators = {},
        plan_text         = "",
        parsed_pages      = {},
        generated_files   = {},
        validation_report = [],
        success           = False,
        test_run_result   = {},
        repair_loop_count = 0,
        errors            = [],
    )


# ═════════════════════════════════════════════════════════════════════════════
#  PUBLIC ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════

def run_pipeline(query: str) -> PipelineState:
    """
    Run the full Playwright test generation pipeline for the given query.

    Steps:
        1. QueryAgent  — browser + locator capture
        2. PlannerAgent — step plan
        3. GeneratorAgent — Page Objects + spec
        4. ValidatorAgent — static analysis

    Args:
        query: plain-English description of what to test, e.g.
               "login and add ADIDAS ORIGINAL to cart"

    Returns:
        The final PipelineState after all four nodes have run.
        Key fields of interest:
          final_state["success"]          — True if tests are ready to run
          final_state["generated_files"]  — dict of rel_path → JS code
          final_state["validation_report"]— list of report lines
          final_state["errors"]           — any non-fatal pipeline errors
    """
    banner = "═" * 60
    print(f"\n{banner}")
    print(f"  🚀  Playwright LangGraph Pipeline")
    print(f"  Query: {query}")
    print(f"{banner}")

    pipeline      = _get_pipeline()
    initial_state = _make_initial_state(query)
    final_state   = pipeline.invoke(initial_state)

    # Surface any accumulated errors
    if final_state["errors"]:
        print(f"\n{'─' * 60}")
        print("  ⚠️   Pipeline errors / warnings:")
        for err in final_state["errors"]:
            print(f"   • {err}")
        print(f"{'─' * 60}")

    return final_state


# ═════════════════════════════════════════════════════════════════════════════
#  CLI  —  run as a script or in interactive mode
# ═════════════════════════════════════════════════════════════════════════════

def _interactive_loop() -> None:
    """Read queries from stdin and run the pipeline for each one."""
    print("\n🤖  Playwright LangGraph Pipeline — interactive mode")
    print("    Type your query and press Enter.  Type 'exit' to quit.\n")

    while True:
        try:
            query = input("Query > ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
            break

        if not query:
            continue
        if query.lower() in ("exit", "quit", "q"):
            break

        run_pipeline(query)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Playwright LangGraph Pipeline — generate Page Objects and tests from a plain-English query",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python pipeline.py --query "login and add ADIDAS ORIGINAL to cart"
  python pipeline.py --query "verify order history shows previous purchases"
  python pipeline.py                    # interactive mode
""",
    )
    parser.add_argument(
        "--query", "-q",
        type=str,
        default=None,
        help="Test scenario in plain English",
    )
    args = parser.parse_args()

    if args.query:
        run_pipeline(args.query)
    else:
        _interactive_loop()