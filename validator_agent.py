"""
validator_agent.py
═══════════════════════════════════════════════════════════════════════════════
PURPOSE
───────
Step 4 of the pipeline (final step).  Performs static analysis on every
generated JavaScript file to catch common LLM mistakes BEFORE you run
`npx playwright test`.  Failing early here saves time debugging test runner
errors.

This module is self-contained.  It exposes ONE public entry point:

    from validator_agent import run_validator_agent

    state: PipelineState = run_validator_agent(state)

The return value is an updated copy of state with:

    state["validation_report"]  – list[str] one entry per checked file
    state["success"]            – bool: True if no ERROR-level issues found
    state["errors"]             – list[str] pipeline-level errors

OUTPUT FILES (written to output/)
──────────────────────────────────
  output/validation_report.md   – full human-readable report with pass/fail

CHECKS PERFORMED
─────────────────
For every .js file under output/pages/ and output/tests/:

  ERROR-level (will break tests at runtime):
  ──────────────────────────────────────────
  [E1]  `import { test }` in a page object file
        Page objects must never import `test` — only the spec file should.
        If this sneaks in, Playwright will error on duplicate test declarations.

  [E2]  Method accepts `page` as a parameter: `async someMethod(page)`
        Page objects store `this.page` in the constructor.  Accepting `page`
        as a method argument is a sign the LLM forgot the POM pattern.

  [E3]  Missing `await` before `this.page.` calls
        Playwright calls are async — forgetting `await` causes silent failures
        where the assertion or action fires but the test doesn't wait.

  [E4]  `export default class` missing from page object
        Every page object must export its class as default so the spec can
        import it.

  WARNING-level (may cause failures, should be reviewed):
  ───────────────────────────────────────────────────────
  [W1]  `querySelector` used as a locator
        LLM invented a CSS selector instead of using a Playwright locator.
        May be fragile — prefer getByRole / getByLabel.

  [W2]  `NO_LOCATOR` placeholder present
        GeneratorAgent emitted a TODO comment for a step with no locator.
        This step will silently do nothing at runtime.

  [W3]  `.then(` chained Playwright call
        Promise chaining instead of async/await — causes race conditions.

  [W4]  `page.` called directly in a page object (without `this.`)
        Means `page` was used as a free variable — will throw ReferenceError.

INFO-level (informational — no action required):
─────────────────────────────────────────────────
  [I1]  Line count per file
  [I2]  Method count per page object

RUN-INSTRUCTIONS APPENDED
──────────────────────────
After validation, the agent prints the exact npx playwright test command to
run the generated tests, so you never have to look it up.

DESIGN NOTES
────────────
• All checks are pure regex — no AST parser needed for these patterns.
• Page object files and the spec file are checked with different rule sets
  (e.g. E1 only applies to page objects, not the spec).
• The `success` flag is True only when zero ERROR-level issues are found.
  Warnings do not affect the flag.
• The validation_report is a list of strings for easy serialisation into
  state — it is also written to output/validation_report.md.
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pipeline import PipelineState

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────
OUTPUT_DIR = "output"


# ═════════════════════════════════════════════════════════════════════════════
#  SECTION 1 – RULE DEFINITIONS
# ═════════════════════════════════════════════════════════════════════════════

# Each rule is a tuple:
#   ( id, description, regex_pattern, severity, apply_to_spec )
#
# apply_to_spec: True → also run on alltest.spec.js
#                False → only run on page object files

_PAGE_OBJECT_RULES = [
    # ERROR level
    (
        "E1",
        "imports `test` (page objects must only import `expect`)",
        r"import\s*\{[^}]*\btest\b[^}]*\}",
        "ERROR",
        False,   # only for page objects
    ),
    (
        "E2",
        "method accepts `page` as a parameter (should use `this.page`)",
        r"async\s+\w+\s*\(\s*page\s*[\),]",
        "ERROR",
        False,
    ),
    (
        "E3",
        "Playwright call without `await` (and not inside expect())",
        r"__E3_CUSTOM__",
        "ERROR",
        False,
    ),
    (
        "E4",
        "missing `export default class` declaration",
        r"export\s+default\s+class",
        "ERROR_ABSENT",   # error if this PATTERN IS ABSENT (inverted check)
        False,
    ),
    # WARNING level
    (
        "W1",
        "uses `querySelector` — fragile CSS; prefer getByRole/getByLabel",
        r"querySelector",
        "WARN",
        False,
    ),
    (
        "W2",
        "NO_LOCATOR placeholder — locator was not captured; step is a no-op",
        r"NO_LOCATOR",
        "WARN",
        False,
    ),
    (
        "W3",
        "uses `.then(` promise chaining instead of async/await",
        r"\.then\s*\(",
        "WARN",
        True,   # also check spec
    ),
    (
        "W4",
        "bare `page.` call without `this.` — will throw ReferenceError",
        r"(?<![.\w])page\.\w+\(",
        "WARN",
        False,
    ),
]

# Spec-specific rules
_SPEC_RULES = [
    (
        "S1",
        "spec file missing `import { test, expect }`",
        r"import\s*\{[^}]*\btest\b[^}]*\}",
        "ERROR_ABSENT",   # error if absent
        True,
    ),
    (
        "S2",
        "spec file missing `test(` call",
        r"\btest\s*\(",
        "ERROR_ABSENT",
        True,
    ),
]


# ═════════════════════════════════════════════════════════════════════════════
#  SECTION 2 – CHECKER
# ═════════════════════════════════════════════════════════════════════════════

def _check_e3(code: str) -> bool:
    """
    Line-by-line check: find any `this.page.<x>` call that is NOT preceded
    by `await` on the same line AND is NOT wrapped in expect().
    A single regex lookbehind cannot handle this because expect() can appear
    anywhere on the line between `await` and `this.page`.
    """
    for line in code.splitlines():
        stripped = line.strip()
        if stripped.startswith("//") or "this.page." not in stripped:
            continue
        has_await = "await this.page" in stripped or stripped.startswith("await ")
        in_expect = "expect(this.page" in stripped
        if not has_await and not in_expect:
            return True
    return False


def _check_file(rel_path: str, code: str) -> list[dict]:
    """
    Run all applicable rules against a single file's code.

    Returns a list of issue dicts:
        { "id": "E1", "severity": "ERROR", "message": "..." }

    Empty list = no issues.
    """
    is_spec = "spec" in rel_path.lower()
    issues:  list[dict] = []

    # Select which rules to apply
    rules_to_run = []
    if not is_spec:
        rules_to_run.extend(_PAGE_OBJECT_RULES)
    rules_to_run.extend(r for r in _SPEC_RULES if r[4])  # spec rules that apply

    for rule_id, description, pattern, severity, _ in rules_to_run:
        # Skip page-object-only rules on the spec file
        if not is_spec and rule_id.startswith("S"):
            continue
        if is_spec and rule_id.startswith("E") and _ is False:
            continue

        # Custom E3: line-by-line await check
        if pattern == "__E3_CUSTOM__":
            if not is_spec and _check_e3(code):
                issues.append({"id": rule_id, "severity": "ERROR", "message": description})
            continue

        match_found = bool(re.search(pattern, code, re.IGNORECASE | re.MULTILINE))

        if severity == "ERROR_ABSENT":
            if not match_found:
                issues.append({"id": rule_id, "severity": "ERROR", "message": description})
        else:
            if match_found:
                issues.append({"id": rule_id, "severity": severity, "message": description})

    return issues


def _count_methods(code: str) -> int:
    """Count the number of async method declarations in a JS file."""
    return len(re.findall(r"\basync\s+\w+\s*\(", code))


# ═════════════════════════════════════════════════════════════════════════════
#  SECTION 3 – REPORT BUILDER
# ═════════════════════════════════════════════════════════════════════════════

def _build_report(generated_files: dict[str, str]) -> tuple[list[str], bool]:
    """
    Run all checks on all generated files and build a report.

    Returns:
        report_lines: list[str]   – one entry per file, with issue details
        success:      bool        – True if no ERROR-level issues found
    """
    report_lines: list[str] = []
    global_success          = True

    for rel_path, code in generated_files.items():
        issues    = _check_file(rel_path, code)
        n_lines   = len(code.splitlines())
        n_methods = _count_methods(code)

        errors   = [i for i in issues if i["severity"] == "ERROR"]
        warnings = [i for i in issues if i["severity"] == "WARN"]

        if errors:
            global_success = False

        # File header
        status = "❌ ERRORS" if errors else ("⚠️  WARNINGS" if warnings else "✅ PASS")
        report_lines.append(
            f"\n{'─' * 55}\n"
            f"  {status}  |  {rel_path}\n"
            f"  {n_lines} lines  |  {n_methods} methods\n"
            f"{'─' * 55}"
        )

        if not issues:
            report_lines.append("  No issues found.")
        else:
            for issue in issues:
                icon = "🔴" if issue["severity"] == "ERROR" else "🟡"
                report_lines.append(
                    f"  {icon} [{issue['id']}] {issue['message']}"
                )

    # Summary line
    total_errors   = sum(1 for line in report_lines if "🔴" in line)
    total_warnings = sum(1 for line in report_lines if "🟡" in line)
    total_files    = len(generated_files)

    report_lines.append(
        f"\n{'═' * 55}\n"
        f"  SUMMARY: {total_files} file(s)  |  "
        f"{total_errors} error(s)  |  {total_warnings} warning(s)\n"
        f"{'═' * 55}"
    )

    return report_lines, global_success


# ═════════════════════════════════════════════════════════════════════════════
#  SECTION 4 – OUTPUT WRITER
# ═════════════════════════════════════════════════════════════════════════════

def _save_report(report_lines: list[str], success: bool) -> None:
    """Write the validation report to output/validation_report.md."""
    with open(f"{OUTPUT_DIR}/validation_report.md", "w", encoding="utf-8") as f:
        f.write("# Validation Report\n\n")
        f.write("```\n")
        f.write("\n".join(report_lines))
        f.write("\n```\n\n")
        if success:
            f.write("## ✅ All checks passed\n\n")
            f.write("Run tests:\n```bash\n")
            f.write(f"npx playwright test ./{OUTPUT_DIR}/tests/alltest.spec.js --headed\n")
            f.write("```\n")
        else:
            f.write("## ❌ Errors found — fix before running tests\n\n")
            f.write("Review the issues above, then re-run the pipeline or fix files manually.\n")


# ═════════════════════════════════════════════════════════════════════════════
#  PUBLIC API  —  this is the only function the pipeline imports
# ═════════════════════════════════════════════════════════════════════════════

def run_validator_agent(state: "PipelineState") -> "PipelineState":
    """
    LangGraph node function.  Receives pipeline state with generated_files
    populated by generator_agent.  Runs static checks and returns updated state.

    Mutates (returns updated copy of):
        state["validation_report"]
        state["success"]
        state["errors"]
    """
    print(f'\n{"=" * 60}\n  NODE 4 / VALIDATOR AGENT\n{"=" * 60}')

    generated_files: dict = state.get("generated_files", {})
    errors = list(state.get("errors", []))

    if not generated_files:
        errors.append("ValidatorAgent: skipped — no generated files to validate")
        return {
            **state,
            "validation_report": ["No files generated."],
            "success":           False,
            "errors":            errors,
        }

    report_lines, success = _build_report(generated_files)

    # Print to console
    print("\n".join(report_lines))

    # Persist to disk
    _save_report(report_lines, success)

    # Final instructions
    if success:
        print(
            f"\n🎉  All checks passed — tests are ready to run:\n"
            f"     npx playwright test ./{OUTPUT_DIR}/tests/alltest.spec.js --headed\n"
            f"\n   Or run headless:\n"
            f"     npx playwright test ./{OUTPUT_DIR}/tests/alltest.spec.js\n"
        )
    else:
        print(
            f"\n⚠️   Errors found — review output/validation_report.md "
            f"before running tests\n"
        )

    return {
        **state,
        "validation_report": report_lines,
        "success":           success,
        "errors":            errors,
    }