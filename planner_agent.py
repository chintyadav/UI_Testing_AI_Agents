"""
planner_agent.py
═══════════════════════════════════════════════════════════════════════════════
PURPOSE
───────
Step 2 of the pipeline.  Receives captured page snapshots + raw locators from
query_agent.py and produces a machine-parseable automation plan that the
generator can consume without any ambiguity.

This module is self-contained.  It exposes ONE public entry point:

    from planner_agent import run_planner_agent

    state: PipelineState = run_planner_agent(state)

The return value is an updated copy of state with:

    state["plan_text"]     – raw LLM output (the step-plan markdown)
    state["parsed_pages"]  – OrderedDict: page_name → page_dict (see schema)
    state["errors"]        – list[str] any non-fatal error messages

OUTPUT FILES (written to output/)
──────────────────────────────────
  output/planner_output.md   – full plan for human review and debugging

PARSED PAGES SCHEMA
────────────────────
The parsed_pages OrderedDict has this shape:

    {
        "Login Page": {
            "page_name":  "Login Page",
            "class_name": "LoginPage",      # PascalCase, safe for JS class name
            "file_name":  "LoginPage.js",   # output file name
            "methods": [
                {
                    "method_name":  "enterEmail",     # camelCase verb
                    "type":         "action",         # "action" | "validation"
                    "locator":      'getByLabel("Email")',  # verbatim from registry
                    "description":  "Fill in the email field",
                    "navigates_to": None,             # None or page name string
                },
                ...
            ],
        },
        "Dashboard Page": { ... },
    }

STEP FORMAT  (what the LLM is instructed to produce)
──────────────────────────────────────────────────────
Each step block in planner_output.md looks like:

    PAGE: Login Page
    TYPE: ACTION
    METHOD: enterEmail
    LOCATOR: getByLabel("Email")
    DESCRIPTION: Fill in the email field with the test user's email address
    NAVIGATES_TO: NONE
    ---

The parser splits on blank lines and reads key:value pairs.  The rigid format
means GeneratorAgent never has to deal with ambiguous output.

DESIGN NOTES
────────────
• The planner receives pages_data directly from state — no file I/O required
  between QueryAgent and PlannerAgent.
• Pages are deduplicated by page_name in parse order; methods are appended in
  first-occurrence order within each page.  This preserves the natural
  execution sequence.
• Method names are made unique within a page if the LLM repeats one (suffix
  _2, _3, …).
• class_name and file_name are derived deterministically from page_name by
  stripping non-alphanumeric characters and PascalCasing each word.
"""

from __future__ import annotations

import os
import re
from collections import OrderedDict
from typing import TYPE_CHECKING

from dotenv import load_dotenv
from langchain_groq import ChatGroq
from llm_utils import invoke_llm_with_retry

load_dotenv()

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────
OUTPUT_DIR = "output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

_llm = ChatGroq(
    model="llama-3.3-70b-versatile",
    temperature=0,
    groq_api_key=os.getenv("GROQ_API_KEY"),
)

if TYPE_CHECKING:
    from pipeline import PipelineState


# ═════════════════════════════════════════════════════════════════════════════
#  SECTION 1 – LOCATOR REGISTRY BUILDER
#  Formats pages_data into a concise text block the LLM can scan to pick
#  locators.  Every locator is shown with its quality rating so the LLM
#  prefers high-quality ones.
# ═════════════════════════════════════════════════════════════════════════════

def _build_locator_registry(pages_data: list[dict]) -> str:
    """
    Convert a list of page snapshot dicts into a human + LLM readable registry
    string like:

        PAGE: Login Page
        ──────────────────────────────────────────────────
          [input]  page.getByLabel("Email")  (high)  — "Email"
          [button] page.getByRole("button", { name: "Login" }) (high)  — "Login"
          ...

    This registry is embedded verbatim in the planner prompt.  The LLM is
    instructed to copy locators character-for-character from here.
    """
    lines: list[str] = []

    for pg in pages_data:
        lines.append(f"\nPAGE: {pg['page']}")
        lines.append("─" * 50)

        for el in pg.get("elements", []):
            loc  = el.get("locator", {})
            code = loc.get("code")
            if not code:
                continue
            label   = el.get("label") or "?"
            quality = loc.get("quality", "?")
            lines.append(
                f'  [{el["tag"]}]  page.{code}  ({quality})  — "{label}"'
            )

    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════════
#  SECTION 2 – LLM PLAN GENERATOR
#  Sends the locator registry + user query to the LLM and gets back a rigid
#  step-by-step plan in the canonical block format.
# ═════════════════════════════════════════════════════════════════════════════

def _generate_plan(query: str, pages_data: list[dict]) -> str:
    """
    Ask the LLM to produce a machine-parseable automation plan.

    The prompt enforces:
      - Only page names from the captured pages list
      - Only locators copied verbatim from the registry
      - Exactly one validation step per page
      - NAVIGATES_TO set correctly on navigation steps

    Returns the raw LLM response string (plan_text).
    """
    registry   = _build_locator_registry(pages_data)
    page_names = [pg["page"] for pg in pages_data]   # in visit order

    prompt = f"""You are a Senior QA Architect.  Produce a machine-parseable test automation plan.

USER QUERY: "{query}"

PAGES VISITED — use ONLY these names, in this order:
{chr(10).join(f"  {i + 1}. {name}" for i, name in enumerate(page_names))}

REAL LOCATOR REGISTRY — copy locators VERBATIM from here, character-for-character:
{registry}

═══════════════════════════════════════════════════════════════════════════════
OUTPUT FORMAT — use EXACTLY this block structure for EVERY step:

PAGE: <exact page name from the PAGES VISITED list>
TYPE: ACTION or VALIDATION
METHOD: <camelCase method name — must start with a verb, e.g. clickLogin, enterEmail, verifyDashboard>
LOCATOR: <copy the locator code from the REAL LOCATOR REGISTRY, e.g. getByRole("button", {{ name: "Login" }})>
DESCRIPTION: <one clear sentence describing what this step does>
NAVIGATES_TO: <destination page name if this step causes navigation, otherwise NONE>
---

═══════════════════════════════════════════════════════════════════════════════
RULES:
  1. Use ONLY page names from the PAGES VISITED list — never invent new ones
  2. Copy locators VERBATIM from the REAL LOCATOR REGISTRY — no modifications
  3. Every ACTION that clicks a link/button AND causes navigation must have
     NAVIGATES_TO set to the destination page name
  4. After a NAVIGATES_TO step, do NOT write more steps for that page —
     the next steps belong to the destination page section
  5. Add exactly ONE VALIDATION step per page (TYPE: VALIDATION) that calls
     toBeVisible on a heading or key element to confirm page load
  6. Do NOT invent methods, locators, page names, or steps not supported by
     the locator registry
  7. camelCase method names only — e.g. clickAddToCart, verifyProductPage
  8. Steps within a page section must be in logical execution order

  9. QUERY RELEVANCE — CRITICAL:
     Only include steps that DIRECTLY serve the user query: "{query}"
     Do NOT add steps the query does not ask for.
     Examples:
       Query "login and add X to cart" → steps: login, find product, click Add to Cart
       Query "login and add X to cart" → DO NOT add: search bar click, search input, filter steps
       Query "verify order history"    → steps: login, navigate to orders, verify list
     If a locator is for a feature NOT mentioned in the query, skip it entirely.

  10. VISIBILITY — only include steps for elements that are normally VISIBLE
      on page load without requiring extra user actions (like expanding panels).
      The search/filter bar on dashboard is hidden by default — do NOT include
      clickSearch or enterSearchTerm unless the query explicitly asks to search.

Now write the complete plan:"""

    print("\n🧠  Generating automation plan...")
    plan_text = invoke_llm_with_retry(_llm, prompt)
    return plan_text


# ═════════════════════════════════════════════════════════════════════════════
#  SECTION 3 – PLAN PARSER
#  Converts the raw LLM text into the structured parsed_pages OrderedDict.
#  Uses regex on individual blocks — tolerant of minor whitespace variation.
# ═════════════════════════════════════════════════════════════════════════════

def _parse_plan(plan_text: str) -> OrderedDict:
    """
    Parse the LLM plan text into an OrderedDict of page objects.

    Parsing strategy:
      1. Strip the header section (everything up to the first ---)
      2. Split remaining text on blank lines to get individual step blocks
      3. For each block, extract key:value pairs with regex
      4. Accumulate methods per page, deduplicating method names

    Returns an OrderedDict:
        { page_name: { page_name, class_name, file_name, methods: [...] } }

    Pages are in first-occurrence (visit) order.  Each page appears exactly
    once — methods are merged if the LLM splits a page across multiple blocks.
    """
    pages: OrderedDict[str, dict] = OrderedDict()

    # Strip everything before the first "---" separator (the plan header)
    plan_text = re.sub(r"^.*?---\n", "", plan_text, flags=re.DOTALL)

    # Split on blank lines — each step block is separated by one or more blank lines
    blocks = re.split(r"\n\n+", plan_text)

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        def _get(key: str) -> str:
            """Extract value for key: from a block string."""
            m = re.search(rf"^{key}:\s*(.+)$", block, re.MULTILINE | re.IGNORECASE)
            return m.group(1).strip() if m else ""

        page_name   = _get("PAGE")
        method_name = _get("METHOD")
        mtype       = _get("TYPE").lower()        # "action" or "validation"
        locator     = _get("LOCATOR")
        description = _get("DESCRIPTION")
        nav_to_raw  = _get("NAVIGATES_TO")
        nav_to      = None if (not nav_to_raw or nav_to_raw.upper() == "NONE") else nav_to_raw

        if not page_name or not method_name:
            continue   # malformed block — skip

        # Derive JS class name: "Login Page" → "LoginPage"
        class_name = re.sub(r"[^a-zA-Z0-9 ]", "", page_name)
        class_name = "".join(word.capitalize() for word in class_name.split())
        file_name  = class_name + ".js"

        # Register page (first occurrence)
        if page_name not in pages:
            pages[page_name] = {
                "page_name":     page_name,
                "class_name":    class_name,
                "file_name":     file_name,
                "methods":       [],
                "_method_counts": {},   # internal dedup tracker (removed before return)
            }

        pg = pages[page_name]

        # Ensure unique method names within a page (_2, _3, … suffix on collision)
        unique_name = method_name
        counts      = pg["_method_counts"]
        if method_name in counts:
            counts[method_name] += 1
            unique_name = f"{method_name}_{counts[method_name]}"
        else:
            counts[method_name] = 1

        pg["methods"].append({
            "method_name":  unique_name,
            "type":         mtype,
            "locator":      locator or "NO_LOCATOR",
            "description":  description,
            "navigates_to": nav_to,
        })

    # Remove internal dedup tracker before returning
    for pg in pages.values():
        pg.pop("_method_counts", None)

    return pages


# ═════════════════════════════════════════════════════════════════════════════
#  SECTION 4 – OUTPUT WRITER
# ═════════════════════════════════════════════════════════════════════════════

def _save_plan(query: str, page_names: list[str], plan_text: str) -> None:
    """Write the raw plan text to output/planner_output.md for human review."""
    with open(f"{OUTPUT_DIR}/planner_output.md", "w", encoding="utf-8") as f:
        f.write("# Automation Plan\n\n")
        f.write(f"**Query:** {query}\n\n")
        f.write(f"**Pages captured:** {', '.join(page_names)}\n\n")
        f.write("---\n\n")
        f.write(plan_text)

    print(f"✅  output/planner_output.md written")


# ═════════════════════════════════════════════════════════════════════════════
#  PUBLIC API  —  this is the only function the pipeline imports
# ═════════════════════════════════════════════════════════════════════════════

def run_planner_agent(state: "PipelineState") -> "PipelineState":
    """
    LangGraph node function.  Receives pipeline state with pages_data populated
    by query_agent.  Produces a structured automation plan and parses it into
    per-page method collections.

    Mutates (returns updated copy of):
        state["plan_text"]
        state["parsed_pages"]
        state["errors"]
    """
    print(f'\n{"=" * 60}\n  NODE 2 / PLANNER AGENT\n{"=" * 60}')

    pages_data = state["pages_data"]
    query      = state["query"]
    errors     = list(state.get("errors", []))

    if not pages_data:
        errors.append("PlannerAgent: skipped — pages_data is empty (QueryAgent failed?)")
        return {**state, "plan_text": "", "parsed_pages": {}, "errors": errors}

    page_names = [pg["page"] for pg in pages_data]

    # Generate plan via LLM
    plan_text = _generate_plan(query, pages_data)

    # Persist to disk for debugging
    _save_plan(query, page_names, plan_text)

    # Parse into structured dict
    parsed_pages = _parse_plan(plan_text)

    if not parsed_pages:
        errors.append(
            "PlannerAgent: could not parse any steps from LLM output.\n"
            "  Check output/planner_output.md to see what the LLM produced.\n"
            "  Expected format per step:\n"
            "    PAGE: ...\n    TYPE: ...\n    METHOD: ...\n    LOCATOR: ...\n    ---"
        )
        return {**state, "plan_text": plan_text, "parsed_pages": {}, "errors": errors}

    # Log parsed summary
    print(f"\n📋  Parsed plan: {len(parsed_pages)} unique page(s)")
    for pname, pg in parsed_pages.items():
        print(f"   {pname} → {pg['class_name']} ({len(pg['methods'])} methods)")
        for m in pg["methods"]:
            nav = f" → {m['navigates_to']}" if m["navigates_to"] else ""
            print(f"      {m['method_name']}(){nav}")

    return {**state, "plan_text": plan_text, "parsed_pages": dict(parsed_pages), "errors": errors}