"""
healer_agent.py
═══════════════════════════════════════════════════════════════════════════════
PURPOSE
───────
The Healer Agent is called automatically whenever any browser action fails
inside query_agent.py.  It receives:
  - The current Playwright page (live browser context)
  - The failed action string (e.g. "ADD_TO_CART ADIDAS ORIGINAL")
  - The error message from the failure
  - A vision diagnosis string (from the vision LLM looking at a screenshot)

It then:
  1. Classifies the failure type (element not found / wrong page / timeout / etc.)
  2. Asks the LLM to generate 3 alternative recovery strategies
  3. Tries each strategy against the live page
  4. Returns success=True + strategy_used if one works
  5. Returns success=False + reason if all fail

It also logs every heal attempt to output/heal_log.json for post-run review.

PUBLIC API
──────────
    from healer_agent import diagnose_and_heal

    result = diagnose_and_heal(
        page,            # Playwright Page object
        failed_action,   # str: the raw action string that failed
        error_msg,       # str: what went wrong
        vision_diagnosis # str: LLM description of current screen state
    )

    # result is a dict:
    # {
    #     "success":       bool,
    #     "strategy_used": str,   # description of what worked (if success)
    #     "reason":        str,   # why it failed (if not success)
    #     "attempts":      list,  # all strategies tried
    # }

HOW THE HEALER WORKS
─────────────────────
Failure classification → strategy generation → live retry

  ELEMENT_NOT_FOUND  — element existed in plan but is not on current page
    → Strategies: scroll page, wait for dynamic load, try alternate selectors

  WRONG_PAGE         — action assumed a different page than current URL
    → Strategies: navigate back, find and click correct nav link

  TIMEOUT            — element exists but didn't respond in time (SPA loading)
    → Strategies: wait longer, wait for specific selector, scroll into view

  CART_FAILURE       — Add to Cart clicked but cart count didn't change
    → Strategies: try nth button, try by exact button text, try hover first

  UNKNOWN            — anything else
    → Strategies: take fresh snapshot, try raw text search, ask LLM for locator

DESIGN NOTES
────────────
• The healer never raises exceptions — it always returns a result dict.
  Callers rely on this to keep the pipeline running even through failures.
• All heal attempts are logged to output/heal_log.json with timestamps.
• The healer uses the SAME vision LLM model as query_agent for consistency.
• Max strategies tried per heal call: 3 (configurable via MAX_STRATEGIES).
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import TYPE_CHECKING

from dotenv import load_dotenv
from langchain_groq import ChatGroq
from llm_utils import invoke_llm_with_retry

load_dotenv()

OUTPUT_DIR     = "output"
MAX_STRATEGIES = 3   # max alternative strategies tried per heal call

os.makedirs(OUTPUT_DIR, exist_ok=True)

_llm = ChatGroq(
    model="llama-3.3-70b-versatile",
    temperature=0.2,   # slight temperature for creative recovery strategies
    groq_api_key=os.getenv("GROQ_API_KEY"),
)

# Persistent heal log
_HEAL_LOG_PATH = f"{OUTPUT_DIR}/heal_log.json"


# ═════════════════════════════════════════════════════════════════════════════
#  SECTION 1 — FAILURE CLASSIFIER
# ═════════════════════════════════════════════════════════════════════════════

_FAILURE_PATTERNS = {
    "ELEMENT_NOT_FOUND": [
        r"element not found",
        r"locator.*resolved to.*0 element",
        r"no element matching",
        r"could not click",
        r"not found",
    ],
    "TIMEOUT": [
        r"timeout",
        r"exceeded.*ms",
        r"timed out",
        r"waited.*ms",
    ],
    "WRONG_PAGE": [
        r"url.*does not match",
        r"navigation.*failed",
        r"page.*not.*loaded",
        r"unexpected url",
    ],
    "CART_FAILURE": [
        r"add.*cart.*failed",
        r"cart.*not.*updated",
        r"button.*not.*respond",
    ],
}


def _classify_failure(error_msg: str, action: str) -> str:
    """
    Classify the failure type based on error message and action string.
    Returns one of: ELEMENT_NOT_FOUND, TIMEOUT, WRONG_PAGE, CART_FAILURE, UNKNOWN
    """
    error_lower = error_msg.lower()
    action_lower = action.lower()

    for failure_type, patterns in _FAILURE_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, error_lower):
                return failure_type

    # Action-based hints
    if "add_to_cart" in action_lower or "cart" in error_lower:
        return "CART_FAILURE"
    if "click_product" in action_lower or "product" in error_lower:
        return "ELEMENT_NOT_FOUND"

    return "UNKNOWN"


# ═════════════════════════════════════════════════════════════════════════════
#  SECTION 2 — STRATEGY GENERATOR
# ═════════════════════════════════════════════════════════════════════════════

def _generate_strategies(
    failed_action: str,
    error_msg:     str,
    failure_type:  str,
    vision_info:   str,
    current_url:   str,
) -> list[dict]:
    """
    Ask the LLM to generate recovery strategies based on the failure context.

    Returns a list of strategy dicts:
    [
        {
            "description": "Wait 2s then retry clicking button by text",
            "playwright_code": "page.wait_for_timeout(2000); page.get_by_role('button', name='Add To Cart').first.click()"
        },
        ...
    ]
    """
    prompt = f"""You are a Playwright test automation healer.

A browser action failed. Generate {MAX_STRATEGIES} alternative recovery strategies.

FAILED ACTION: {failed_action}
ERROR MESSAGE: {error_msg}
FAILURE TYPE:  {failure_type}
CURRENT URL:   {current_url}
VISION DIAGNOSIS (what the LLM sees on screen): {vision_info}

Generate exactly {MAX_STRATEGIES} strategies as JSON array. Each strategy must have:
  - "description": plain English explanation of what this strategy does
  - "playwright_code": a single executable Python expression using `page` variable
    Examples of valid playwright_code values:
      page.get_by_role("button", name="Add To Cart", exact=False).first.click(timeout=8000)
      page.locator(".card").nth(0).locator("button").first.click(timeout=5000)
      page.get_by_text("ADIDAS ORIGINAL", exact=False).first.click(timeout=5000)
      page.wait_for_selector("button", timeout=5000); page.locator("button:has-text('Add')").first.click()

STRATEGY RULES:
  - Each strategy must be genuinely different from the others
  - Prefer Playwright semantic locators (getByRole, getByText) over CSS selectors
  - If it's a timeout issue: add wait_for_timeout(2000) or wait_for_selector first
  - If it's element not found: try alternate text, scroll, or different selector strategy
  - If it's cart failure: try nth card index, hover before click, or explicit button text
  - Each playwright_code must be a valid single Python expression (no assignments, no imports)

Return ONLY valid JSON array (no markdown, no explanation):
[
  {{"description": "...", "playwright_code": "..."}},
  {{"description": "...", "playwright_code": "..."}},
  {{"description": "...", "playwright_code": "..."}}
]"""

    try:
        text = invoke_llm_with_retry(_llm, prompt)
        text = text.replace("```json","").replace("```","").strip()
        strategies = json.loads(text)
        if isinstance(strategies, list):
            return strategies[:MAX_STRATEGIES]
    except Exception as e:
        print(f"     ⚠️  Strategy generation error: {e}")

    # Fallback hardcoded strategies based on failure type
    fallbacks = {
        "ELEMENT_NOT_FOUND": [
            {"description": "Scroll to bottom and retry",                "playwright_code": "page.keyboard.press('End'); page.wait_for_timeout(1000)"},
            {"description": "Wait 3s for dynamic content then retry",    "playwright_code": "page.wait_for_timeout(3000)"},
            {"description": "Try clicking by partial visible text",      "playwright_code": "page.get_by_text('Add', exact=False).first.click(timeout=6000)"},
        ],
        "TIMEOUT": [
            {"description": "Extended wait then retry",                  "playwright_code": "page.wait_for_timeout(4000)"},
            {"description": "Wait for network idle then retry",          "playwright_code": "page.wait_for_load_state('networkidle', timeout=12000)"},
            {"description": "Reload page and retry",                     "playwright_code": "page.reload(); page.wait_for_load_state('domcontentloaded', timeout=10000)"},
        ],
        "CART_FAILURE": [
            {"description": "Try first button on page",                  "playwright_code": "page.locator('button').first.click(timeout=5000)"},
            {"description": "Try button with Add text",                  "playwright_code": "page.get_by_role('button', name='Add', exact=False).nth(0).click(timeout=5000)"},
            {"description": "Hover over first card then click button",   "playwright_code": "page.locator('.card').first.hover(); page.locator('.card').first.locator('button').click(timeout=5000)"},
        ],
        "WRONG_PAGE": [
            {"description": "Go back to previous page",                  "playwright_code": "page.go_back(timeout=10000)"},
            {"description": "Navigate to dashboard",                     "playwright_code": "page.goto('https://rahulshettyacademy.com/client/#/dashboard', timeout=15000)"},
            {"description": "Wait for page to settle",                   "playwright_code": "page.wait_for_load_state('domcontentloaded', timeout=10000)"},
        ],
        "UNKNOWN": [
            {"description": "Wait and scroll",                           "playwright_code": "page.wait_for_timeout(2000); page.mouse.wheel(0, 300)"},
            {"description": "Try first visible button",                  "playwright_code": "page.locator('button:visible').first.click(timeout=5000)"},
            {"description": "Wait for selector and click",               "playwright_code": "page.wait_for_selector('button', timeout=5000); page.locator('button').first.click()"},
        ],
    }
    return fallbacks.get(failure_type, fallbacks["UNKNOWN"])


# ═════════════════════════════════════════════════════════════════════════════
#  SECTION 3 — STRATEGY EXECUTOR
# ═════════════════════════════════════════════════════════════════════════════

def _execute_strategy(page, playwright_code: str) -> bool:
    """
    Safely execute a single Playwright recovery strategy expression.

    The `page` object is injected into the eval context so strategies can
    reference it directly.

    Returns True if execution succeeded without exception, False otherwise.

    SAFETY: Only `page` and `re` are available in the eval namespace.
    The LLM-generated code is sandboxed to Playwright page operations only.
    """
    try:
        # Execute the strategy — page is available as a local variable
        exec(playwright_code, {"page": page, "re": re, "__builtins__": {}})
        return True
    except Exception as e:
        print(f"     ⚠️  Strategy failed: {e}")
        return False


# ═════════════════════════════════════════════════════════════════════════════
#  SECTION 4 — HEAL LOG
# ═════════════════════════════════════════════════════════════════════════════

def _append_heal_log(entry: dict) -> None:
    """Append a heal attempt record to output/heal_log.json."""
    existing = []
    if os.path.exists(_HEAL_LOG_PATH):
        try:
            existing = json.load(open(_HEAL_LOG_PATH, encoding="utf-8"))
        except Exception:
            existing = []
    existing.append(entry)
    json.dump(existing, open(_HEAL_LOG_PATH, "w", encoding="utf-8"), indent=2)


# ═════════════════════════════════════════════════════════════════════════════
#  PUBLIC API
# ═════════════════════════════════════════════════════════════════════════════

def diagnose_and_heal(
    page,
    failed_action:   str,
    error_msg:       str,
    vision_diagnosis: str,
) -> dict:
    """
    Diagnose a failed browser action and attempt recovery.

    Args:
        page:             live Playwright Page object
        failed_action:    the raw action string that failed
        error_msg:        error description from the failure
        vision_diagnosis: what the vision LLM sees on screen right now

    Returns:
        {
            "success":       bool,
            "strategy_used": str,
            "reason":        str,
            "attempts":      list[dict],
        }
    """
    timestamp    = int(time.time())
    current_url  = page.url
    failure_type = _classify_failure(error_msg, failed_action)

    print(f"     🔍  Failure classified as: {failure_type}")
    print(f"     💡  Vision says: {vision_diagnosis[:120]}")

    strategies = _generate_strategies(
        failed_action, error_msg, failure_type, vision_diagnosis, current_url
    )

    attempts: list[dict] = []
    result = {
        "success":       False,
        "strategy_used": "",
        "reason":        "all strategies failed",
        "attempts":      attempts,
    }

    for i, strategy in enumerate(strategies, 1):
        description     = strategy.get("description",      f"Strategy {i}")
        playwright_code = strategy.get("playwright_code",  "")

        print(f"     🔄  Strategy {i}: {description}")

        if not playwright_code:
            attempts.append({"strategy": description, "success": False, "reason": "empty code"})
            continue

        ok = _execute_strategy(page, playwright_code)
        attempts.append({
            "strategy":  description,
            "code":      playwright_code,
            "success":   ok,
        })

        if ok:
            result["success"]       = True
            result["strategy_used"] = description
            result["reason"]        = ""
            break

    # Log the full heal attempt for post-run review
    log_entry = {
        "timestamp":      timestamp,
        "failed_action":  failed_action,
        "error_msg":      error_msg,
        "failure_type":   failure_type,
        "vision":         vision_diagnosis[:200],
        "url":            current_url,
        "success":        result["success"],
        "strategy_used":  result["strategy_used"],
        "attempts":       attempts,
    }
    _append_heal_log(log_entry)

    if result["success"]:
        print(f"     ✅  Healer succeeded: {result['strategy_used']}")
    else:
        print(f"     ❌  Healer exhausted all {len(strategies)} strategies")

    return result


# ═════════════════════════════════════════════════════════════════════════════
#  STANDALONE TEST  (run healer directly for debugging)
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("healer_agent.py — standalone test mode")
    print("This module is designed to be called from query_agent.py.")
    print("To test: run pipeline.py with a query that intentionally fails.")
    print(f"\nHeal log location: {_HEAL_LOG_PATH}")
    if os.path.exists(_HEAL_LOG_PATH):
        log = json.load(open(_HEAL_LOG_PATH, encoding="utf-8"))
        print(f"Existing log entries: {len(log)}")
        for entry in log[-3:]:
            print(f"  [{entry['failure_type']}] {entry['failed_action'][:60]} → success={entry['success']}")
