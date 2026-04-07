"""
test_runner_agent.py
═══════════════════════════════════════════════════════════════════════════════
PURPOSE
───────
Step 5 of the pipeline (runs AFTER validator_agent).

  1. Runs `npx playwright test` against the generated spec file
  2. Parses stdout/stderr for test failures and their error details
  3. Populates state["test_run_result"] with structured failure data
  4. The pipeline uses a conditional edge:
       - If all tests pass  → END
       - If tests fail      → repair_node (generator_agent re-runs with errors injected)
  5. Tracks how many repair attempts have happened to prevent infinite loops
     (MAX_REPAIR_LOOPS, default 3)

PUBLIC API
──────────
    from test_runner_agent import run_test_runner_agent
    state = run_test_runner_agent(state)

STATE KEYS READ
───────────────
    state["generated_files"]     — to confirm spec file exists before running
    state["repair_loop_count"]   — how many times we've already repaired

STATE KEYS WRITTEN
──────────────────
    state["test_run_result"]  — dict (see schema below)
    state["repair_loop_count"] — incremented after each failed run
    state["errors"]

TEST RUN RESULT SCHEMA
───────────────────────
    {
        "passed":         bool,      # True = all tests green
        "return_code":    int,       # 0 = pass, 1 = fail
        "total_tests":    int,
        "failed_tests":   int,
        "passed_tests":   int,
        "failures": [
            {
                "test_name":   str,   # e.g. "end-to-end test"
                "file":        str,   # e.g. "output/tests/alltest.spec.js"
                "error_type":  str,   # e.g. "strict mode violation"
                "error_msg":   str,   # full error text
                "location":    str,   # e.g. "DashboardPage.js:9"
                "locator":     str,   # the bad locator if parseable
                "suggestion":  str,   # LLM-generated fix suggestion
            }
        ],
        "raw_output":     str,       # full stdout + stderr
    }

REPAIR LOOP DESIGN
───────────────────
The pipeline graph has a conditional edge after test_runner_node:

    test_runner_node
        │
        ├── (passed=True or loops exhausted) → END
        │
        └── (passed=False) → repair_node → generator_node → validator_node
                                                │
                                                └── (loops again) → test_runner_node

Each loop increments state["repair_loop_count"].
When repair_loop_count >= MAX_REPAIR_LOOPS, we go to END regardless.

PLAYWRIGHT ERROR PATTERNS RECOGNISED
──────────────────────────────────────
    strict mode violation         → add .first to the locator
    locator.click: Timeout        → element didn't appear — selector wrong
    Cannot read properties of undefined → import path wrong
    waiting for getBy...          → locator never resolved
    Error: locator ... 0 elements → locator found nothing
"""

from __future__ import annotations

import os
import re
import subprocess
import json
import time
import glob
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import load_dotenv
from langchain_groq import ChatGroq
from llm_utils import invoke_llm_with_retry

load_dotenv()

OUTPUT_DIR      = "output"
SPEC_PATH       = f"{OUTPUT_DIR}/tests/alltest.spec.js"
MAX_REPAIR_LOOPS = 3
TEST_RESULTS_DIR = "test-results"
FAILURE_LOG_PATH = f"{OUTPUT_DIR}/test_failure_log.json"

_llm = ChatGroq(
    model="llama-3.3-70b-versatile",
    temperature=0,
    groq_api_key=os.getenv("GROQ_API_KEY"),
)

if TYPE_CHECKING:
    from pipeline import PipelineState


# ═════════════════════════════════════════════════════════════════════════════
#  SECTION 1 — PLAYWRIGHT RUNNER
# ═════════════════════════════════════════════════════════════════════════════

def _run_playwright(spec_path: str) -> tuple[int, str]:
    """
    Execute `npx playwright test <spec_path>` and capture output.

    Windows note: `npx` is a .cmd script on Windows and cannot be found by
    subprocess unless shell=True is used OR the full `npx.cmd` name is used.
    We auto-detect the platform and handle both cases.

    Returns: (return_code: int, combined_output: str)
    """
    import sys
    import shutil

    # On Windows, npx is npx.cmd — use shell=True to let the OS resolve it
    is_windows = sys.platform.startswith("win")

    # Build the command string
    cmd_list = ["npx", "playwright", "test", spec_path,
                "--reporter=line", "--timeout=30000"]

    # On Windows join to a single string and use shell=True
    # On Unix keep as list with shell=False (more secure)
    if is_windows:
        cmd   = " ".join(cmd_list)
        shell = True
    else:
        # Verify npx exists on PATH on Unix
        if not shutil.which("npx"):
            return 1, "ERROR: npx not found on PATH — run: npm install && npx playwright install"
        cmd   = cmd_list
        shell = False

    print(f"\n  ▶  {cmd if isinstance(cmd, str) else ' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=os.getcwd(),
            shell=shell,
        )
        output = (result.stdout + "\n" + result.stderr).strip()

        # Always print raw output so issues are immediately visible
        print("\n  ── Playwright output ──────────────────────────────────")
        for line in output.splitlines()[:60]:
            print(f"  {line}")
        if len(output.splitlines()) > 60:
            print(f"  ... ({len(output.splitlines())} lines total — see output/test_run_output.txt)")
        print("  ───────────────────────────────────────────────────────")

        return result.returncode, output

    except subprocess.TimeoutExpired:
        return 1, "TIMEOUT: npx playwright test exceeded 120 seconds"
    except FileNotFoundError:
        return 1, "ERROR: npx not found — on Windows run from a terminal where `npm` works, or add Node.js to PATH"
    except Exception as e:
        return 1, f"ERROR running playwright: {e}"

    except subprocess.TimeoutExpired:
        return 1, "TIMEOUT: npx playwright test exceeded 120 seconds"
    except FileNotFoundError:
        return 1, "ERROR: npx not found — run `npm install` and `npx playwright install` first"
    except Exception as e:
        return 1, f"ERROR running playwright: {e}"


# ═════════════════════════════════════════════════════════════════════════════
#  SECTION 2 — OUTPUT PARSER
# ═════════════════════════════════════════════════════════════════════════════

# Map of known Playwright error text → short type label
_ERROR_TYPE_MAP = [
    (r"strict mode violation",                   "strict_mode_violation"),
    (r"resolved to \d+ elements",                "strict_mode_violation"),
    (r"Timeout.*waiting for",                    "locator_timeout"),
    (r"locator.*resolved to.*0 element",         "locator_not_found"),
    (r"Cannot read properties of undefined",     "undefined_property"),
    (r"Cannot read properties of null",          "null_property"),
    (r"import.*not found",                       "import_error"),
    (r"SyntaxError",                             "syntax_error"),
    (r"getBy.*0 elements",                       "locator_not_found"),
]


def _classify_error(error_text: str) -> str:
    for pattern, label in _ERROR_TYPE_MAP:
        if re.search(pattern, error_text, re.IGNORECASE):
            return label
    return "unknown_error"


# ═════════════════════════════════════════════════════════════════════════════
#  SECTION 2A — FAILURE LOG & ARTIFACT DISCOVERY
# ═════════════════════════════════════════════════════════════════════════════

def _find_test_artifacts(test_name: str) -> dict:
    """
    Discover Playwright-generated test artifacts (screenshots, HTML, traces).
    
    Playwright saves test results to test-results/ directory:
      - screenshots: .png files
      - HTML snapshots: .html files
      - Traces: full execution traces
    
    Returns a dict with paths to available artifacts:
    {
        "screenshot":     "path/to/screenshot.png" or "",
        "html_snapshot":  "path/to/snapshot.html" or "",
        "trace_file":     "path/to/trace.zip" or "",
    }
    """
    artifacts = {
        "screenshot": "",
        "html_snapshot": "",
        "trace_file": "",
    }
    
    if not os.path.exists(TEST_RESULTS_DIR):
        return artifacts
    
    # Search for screenshot (usually named after test)
    for ext in ["*.png", "*.jpg"]:
        screenshots = glob.glob(os.path.join(TEST_RESULTS_DIR, f"**/{ext}"), recursive=True)
        if screenshots:
            artifacts["screenshot"] = screenshots[0]
            break
    
    # Search for HTML snapshot
    snapshots = glob.glob(os.path.join(TEST_RESULTS_DIR, "**/*.html"), recursive=True)
    if snapshots:
        # Prefer snapshot.html
        for snap in snapshots:
            if "snapshot" in snap.lower():
                artifacts["html_snapshot"] = snap
                break
        if not artifacts["html_snapshot"]:
            artifacts["html_snapshot"] = snapshots[0]
    
    # Search for trace file
    traces = glob.glob(os.path.join(TEST_RESULTS_DIR, "**/trace.zip"), recursive=True)
    if traces:
        artifacts["trace_file"] = traces[0]
    
    return artifacts


def _append_failure_log(entry: dict) -> None:
    """
    Append a detailed failure record to output/test_failure_log.json.
    
    Similar to healer_agent's heal_log, this creates a persistent record
    of all test failures for post-run analysis.
    """
    existing = []
    if os.path.exists(FAILURE_LOG_PATH):
        try:
            existing = json.load(open(FAILURE_LOG_PATH, encoding="utf-8"))
        except Exception:
            existing = []
    
    existing.append(entry)
    os.makedirs(os.path.dirname(FAILURE_LOG_PATH), exist_ok=True)
    with open(FAILURE_LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2)


def _extract_stack_trace(error_text: str) -> str:
    """Extract the call stack from a Playwright error message."""
    lines = error_text.split("\n")
    stack = []
    in_stack = False
    
    for line in lines:
        if "at " in line or in_stack:
            in_stack = True
            stack.append(line.strip())
    
    return "\n".join(stack[:10]) if stack else ""  # limit to first 10 frames


# ═════════════════════════════════════════════════════════════════════════════
#  SECTION 2B — OUTPUT PARSER (continued)
# ═════════════════════════════════════════════════════════════════════════════

def _extract_locator(error_text: str) -> str:
    """Try to extract the bad locator string from a Playwright error message."""
    patterns = [
        r"(getBy\w+\([^)]+\))",
        r"(locator\(['\"][^'\"]+['\"]\))",
        r"waiting for (.*?)$",
    ]
    for pattern in patterns:
        m = re.search(pattern, error_text, re.MULTILINE)
        if m:
            return m.group(1).strip()
    return ""


def _extract_location(error_text: str) -> str:
    """Extract file:line from error (e.g. 'DashboardPage.js:9')."""
    m = re.search(r"([\w]+\.js):(\d+)", error_text)
    if m:
        return f"{m.group(1)}:{m.group(2)}"
    return ""


def _parse_failures(output: str) -> list[dict]:
    """
    Parse Playwright test output and return a list of structured failure dicts.

    Playwright --reporter=line format:
      ✘ [chromium] › file.spec.js:7:5 › test name
        Error: <message>
          at PageObject.method (path/file.js:line)
    """
    failures: list[dict] = []

    # Split on test failure headers
    # Each failure block starts with a number + ") [browser] › "
    blocks = re.split(r"\n\s*\d+\)\s+\[", output)

    for block in blocks[1:]:   # skip first (pre-failure output)
        # Extract test name from header line
        name_match = re.match(r"[^\]]+\]\s+›\s+[^\s]+\s+›\s+(.+?)(?:\n|$)", block)
        test_name  = name_match.group(1).strip() if name_match else "unknown test"

        # Extract error block (everything after "Error:")
        error_match = re.search(r"Error:\s+(.+?)(?:\n\n|\Z)", block, re.DOTALL)
        error_msg   = error_match.group(1).strip() if error_match else block[:400]

        # Extract file location
        location = _extract_location(block)

        # Extract file name from location
        file_match = re.search(r"at\s+\w+\.\w+\s+\(([^)]+)\)", block)
        file_path  = file_match.group(1) if file_match else ""

        error_type = _classify_error(error_msg)
        locator    = _extract_locator(error_msg)
        stack_trace = _extract_stack_trace(block)

        failures.append({
            "test_name":  test_name,
            "file":       file_path,
            "error_type": error_type,
            "error_msg":  error_msg[:600],
            "location":   location,
            "locator":    locator,
            "stack_trace": stack_trace,
            "suggestion": "",   # filled in by _suggest_fixes()
        })

    return failures


def _parse_summary(output: str) -> tuple[int, int, str]:
    """
    Extract (passed_count, failed_count, crash_reason) from Playwright output.

    Three scenarios:
      1. Normal test run:  "1 failed" / "2 passed" lines present
      2. Crash before run: rc=1 but no passed/failed counts — config/syntax error
      3. All passed:       rc=0, "X passed" present

    Returns (passed, failed, crash_reason).
    crash_reason is non-empty when playwright crashed before running any tests.
    """
    failed = 0
    passed = 0
    crash_reason = ""

    m = re.search(r"(\d+)\s+failed", output)
    if m:
        failed = int(m.group(1))
    m = re.search(r"(\d+)\s+passed", output)
    if m:
        passed = int(m.group(1))

    # Detect crash-before-run: rc will be 1 but no test counts found
    if failed == 0 and passed == 0:
        # Look for common crash signatures
        crash_patterns = [
            (r"Cannot find module",          "Missing module — run: npm install"),
            (r"SyntaxError",                 "JavaScript syntax error in generated file"),
            (r"Error: Cannot find",          "File or module not found"),
            (r"error TS\d+",               "TypeScript compilation error"),
            (r"ReferenceError",              "ReferenceError in generated JS"),
            (r"playwright.config",           "Playwright config issue"),
            (r"no tests found",              "No tests found — check spec file path"),
            (r"\bError\b.*\.js:\d+",     "JavaScript error in page object"),
        ]
        for pattern, reason in crash_patterns:
            if re.search(pattern, output, re.IGNORECASE):
                crash_reason = reason
                break
        if not crash_reason and failed == 0 and passed == 0:
            crash_reason = "Unknown crash — see raw output above"

    return passed, failed, crash_reason


# ═════════════════════════════════════════════════════════════════════════════
#  SECTION 3 — LLM FIX SUGGESTER
# ═════════════════════════════════════════════════════════════════════════════

def _read_current_file(rel_path: str) -> str:
    """Read the actual current content of a generated file from disk."""
    full_path = os.path.join(OUTPUT_DIR, rel_path)
    if os.path.exists(full_path):
        return open(full_path, encoding="utf-8").read()
    return ""


def _suggest_fixes(failures: list[dict], generated_files: dict) -> list[dict]:
    """
    For each failure, ask the LLM to suggest a concrete fix.
    Reads ACTUAL file content from disk so find_text is always accurate.

    Fix strategies by error type:
      element not visible / timeout → scrollIntoViewIfNeeded before click
      strict mode violation         → use .nth(0)
      locator not found             → different selector strategy
    """
    if not failures:
        return failures

    # Read ACTUAL file content from disk (not stale state dict)
    po_context = ""
    for rel_path in list(generated_files.keys()):
        if "spec" in rel_path:
            continue
        actual = _read_current_file(rel_path)
        if actual:
            po_context += f"\n\n// FILE: {rel_path}\n{actual}"

    failure_details = [
        {"error_type": f["error_type"], "error_msg": f["error_msg"][:400],
         "location": f["location"], "locator": f["locator"]}
        for f in failures
    ]

    prompt = f"""You are a Playwright Page Object expert fixing broken test files.

ACTUAL CURRENT PAGE OBJECT CODE (from disk right now):
{po_context[:4000]}

FAILURES:
{json.dumps(failure_details, indent=2)}

PLAYWRIGHT FIX RULES:
- "element is not visible" or timeout: element exists but is hidden.
  Add scrollIntoViewIfNeeded() before click, or remove the step if not needed for the query.
  Pattern: await this.page.locator("x").nth(0).scrollIntoViewIfNeeded();\n    await this.page.locator("x").nth(0).click();
- "strict mode violation": use .nth(0) to pick one element.
- "locator not found" or 0 elements: use getByRole or getByText instead.

CRITICAL: find_text must be copied EXACTLY from the ACTUAL CURRENT CODE above.
Do NOT guess — only use text you can literally see in the file content shown.

Return ONLY a JSON array (no markdown):
[
  {{
    "failure_index": 0,
    "fix_description": "brief description of fix",
    "find_text": "<exact existing line from file>",
    "replace_with": "<corrected line(s)>"
  }}
]"""

    try:
        text = invoke_llm_with_retry(_llm, prompt)
        text = text.replace("```json","").replace("```","").strip()
        fixes = json.loads(text)
        if isinstance(fixes, list):
            for fix in fixes:
                idx = fix.get("failure_index", 0)
                if 0 <= idx < len(failures):
                    failures[idx]["suggestion"]  = fix.get("fix_description", "")
                    failures[idx]["find_text"]   = fix.get("find_text", "")
                    failures[idx]["replace_with"]= fix.get("replace_with", "")
    except Exception as e:
        print(f"     ⚠️  Fix suggestion error: {e}")

    return failures


def _apply_patches(failures: list[dict], generated_files: dict) -> dict:
    """
    Apply find/replace patches. Reads actual file from disk before patching
    and logs clearly when find_text is not found (stale suggestion).
    """
    patched = dict(generated_files)

    for failure in failures:
        find_text    = (failure.get("find_text",    "") or "").strip()
        replace_with = (failure.get("replace_with", "") or "").strip()

        if not find_text or not replace_with or find_text == replace_with:
            continue

        applied = False
        for rel_path in list(patched.keys()):
            if "spec" in rel_path:
                continue
            actual_code = _read_current_file(rel_path)
            if not actual_code:
                continue
            if find_text in actual_code:
                new_code = actual_code.replace(find_text, replace_with)
                patched[rel_path] = new_code
                full_path = os.path.join(OUTPUT_DIR, rel_path)
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(new_code)
                print(f"     ✅  Patched {rel_path}: {failure.get('suggestion','')[:60]}")
                applied = True
                break

        if not applied:
            print(f"     ⚠️  find_text not found in any file (stale suggestion ignored):")
            print(f"         {find_text[:80]}")

    return patched


def _save_test_result(result: dict) -> None:
    """
    Write test run result to output/test_run_result.json and log failures.
    
    Also appends detailed failure records to output/test_failure_log.json with:
      - Timestamp when failure occurred
      - All error details (type, message, location, stack trace)
      - Visual artifacts (screenshots, HTML snapshots, traces)
      - Size and links to browser reports
    """
    with open(f"{OUTPUT_DIR}/test_run_result.json", "w", encoding="utf-8") as f:
        # Don't write raw_output to JSON (too large) — write to separate file
        slim = {k: v for k, v in result.items() if k != "raw_output"}
        json.dump(slim, f, indent=2)

    with open(f"{OUTPUT_DIR}/test_run_output.txt", "w", encoding="utf-8") as f:
        f.write(result.get("raw_output", ""))
    
    # ── Log failures to persistent failure log ─────────────────────────────
    if not result.get("passed", True) and result.get("failures"):
        timestamp = int(time.time())
        
        for i, failure in enumerate(result.get("failures", [])):
            # Discover visual artifacts for this failure
            artifacts = _find_test_artifacts(failure.get("test_name", ""))
            
            log_entry = {
                "timestamp":     timestamp,
                "attempt":       result.get("attempt_number", 0),
                "test_name":     failure.get("test_name", ""),
                "error_type":    failure.get("error_type", ""),
                "error_msg":     failure.get("error_msg", "")[:500],
                "location":      failure.get("location", ""),
                "locator":       failure.get("locator", ""),
                "stack_trace":   failure.get("stack_trace", ""),
                "suggestion":    failure.get("suggestion", ""),
                "artifacts": {
                    "screenshot":    artifacts.get("screenshot", ""),
                    "html_snapshot": artifacts.get("html_snapshot", ""),
                    "trace_file":    artifacts.get("trace_file", ""),
                },
                "browser_report": f"playwright-report/index.html",
            }
            _append_failure_log(log_entry)
            
            print(f"     📋  Logged failure to {FAILURE_LOG_PATH}")



# ═════════════════════════════════════════════════════════════════════════════
#  PUBLIC API
# ═════════════════════════════════════════════════════════════════════════════

def run_test_runner_agent(state: "PipelineState") -> "PipelineState":
    """
    LangGraph node. Runs npx playwright test, parses results, applies patches.

    On pass  → state["test_run_result"]["passed"] = True
    On fail  → failures parsed, LLM suggests fixes, patches applied,
               repair_loop_count incremented so pipeline knows to re-run generator
    """
    loop_count = state.get("repair_loop_count", 0)
    print(f'\n{"="*60}')
    print(f'  NODE 5 / TEST RUNNER  (attempt {loop_count + 1}/{MAX_REPAIR_LOOPS})')
    print(f'{"="*60}')

    errors = list(state.get("errors", []))

    # Guard: spec file must exist
    if not os.path.exists(SPEC_PATH):
        errors.append(f"TestRunner: spec file not found at {SPEC_PATH}")
        return {**state,
                "test_run_result": {"passed": False, "failures": [], "raw_output": ""},
                "errors": errors}

    # Run playwright
    rc, output = _run_playwright(SPEC_PATH)
    passed_count, failed_count, crash_reason = _parse_summary(output)

    print(f"\n  {'✅' if rc == 0 else '❌'}  Return code: {rc}  |  "
          f"Passed: {passed_count}  Failed: {failed_count}"
          + (f"  |  CRASH: {crash_reason}" if crash_reason else ""))

    # ── CRASH before tests ran (config/syntax error) ─────────────────────
    if rc != 0 and crash_reason and failed_count == 0 and passed_count == 0:
        print(f"\n  💥  Playwright crashed before running tests: {crash_reason}")
        print("  This is a config/syntax issue, not a test failure.")
        print("  Triggering one LLM repair attempt on the generated files...")

        generated_files = state.get("generated_files", {})
        # Treat the crash output as a single failure and ask LLM to fix it
        crash_failure = [{
            "test_name":   "startup crash",
            "file":        "",
            "error_type":  "crash",
            "error_msg":   output[:800],
            "location":    "",
            "locator":     "",
            "suggestion":  "",
        }]
        crash_failure = _suggest_fixes(crash_failure, generated_files)
        patched_files  = _apply_patches(crash_failure, generated_files)

        result = {
            "passed":       False,
            "return_code":  rc,
            "total_tests":  0,
            "failed_tests": 0,
            "passed_tests": 0,
            "attempt_number": loop_count + 1,
            "crash_reason": crash_reason,
            "failures":     crash_failure,
            "raw_output":   output,
        }
        _save_test_result(result)
        new_loop_count = state.get("repair_loop_count", 0) + 1
        if new_loop_count >= MAX_REPAIR_LOOPS:
            errors.append(f"TestRunner: max repair loops reached. Crash reason: {crash_reason}")
        return {**state,
                "test_run_result":   result,
                "generated_files":   patched_files,
                "repair_loop_count": new_loop_count,
                "errors": errors}

    if rc == 0:
        # All tests passed
        result = {
            "passed":      True,
            "return_code": 0,
            "total_tests": passed_count,
            "failed_tests": 0,
            "passed_tests": passed_count,
            "attempt_number": loop_count + 1,
            "failures":    [],
            "raw_output":  output,
        }
        _save_test_result(result)
        print("\n  🎉  All tests passed!")
        return {**state,
                "test_run_result":  result,
                "repair_loop_count": loop_count,
                "errors": errors}

    # Tests failed — parse and suggest fixes
    print(f"\n  Parsing {failed_count} failure(s) from test output...")
    failures = _parse_failures(output)

    for f in failures:
        print(f"  🔴  [{f['error_type']}]  {f['error_msg'][:100]}")

    # Ask LLM for fix suggestions
    print("\n  🧠  Generating fix suggestions...")
    generated_files = state.get("generated_files", {})
    failures = _suggest_fixes(failures, generated_files)

    # Apply fast-path patches (simple find/replace)
    print("\n  🔧  Applying patches...")
    patched_files = _apply_patches(failures, generated_files)

    result = {
        "passed":       False,
        "return_code":  rc,
        "total_tests":  passed_count + failed_count,
        "failed_tests": failed_count,
        "passed_tests": passed_count,
        "attempt_number": loop_count + 1,
        "failures":     failures,
        "raw_output":   output,
    }
    _save_test_result(result)

    new_loop_count = loop_count + 1
    if new_loop_count >= MAX_REPAIR_LOOPS:
        errors.append(
            f"TestRunner: reached max repair loops ({MAX_REPAIR_LOOPS}). "
            f"Manual fix required. See output/test_run_result.json"
        )

    return {**state,
            "test_run_result":  result,
            "generated_files":  patched_files,
            "repair_loop_count": new_loop_count,
            "errors": errors}