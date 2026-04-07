# PlaywrightAgent 🤖

> **AI-powered end-to-end test generation pipeline** — describe what you want to test in plain English, get production-ready Playwright test files with zero manual locator writing.

```bash
python pipeline.py --query "login and add ADIDAS ORIGINAL to cart"
# → Runs a real browser, captures live locators, generates Page Objects,
#   runs tests, self-heals failures, and loops until green ✅
```

---

## What It Does

PlaywrightAgent takes a plain-English query and runs a 5-node LangGraph pipeline that produces working Playwright tests — including a self-healing repair loop that fixes its own failures and retries automatically.

```
Your query
    │
    ▼
┌─────────────────────────────────────────────────────┐
│                  LangGraph Pipeline                  │
│                                                      │
│  QueryAgent → PlannerAgent → GeneratorAgent          │
│                                   │                  │
│                              ValidatorAgent          │
│                                   │                  │
│                             TestRunnerAgent          │
│                              ↙         ↘            │
│                         (pass→END)  (fail→repair)   │
│                                         ↓            │
│                              GeneratorAgent ← loop  │
└─────────────────────────────────────────────────────┘
```

---

## Architecture — 5 Agents

### 1. `query_agent.py` — Vision-Enabled Browser Agent
Runs a real Chromium browser via Playwright, executes your query as browser actions, and captures live DOM locators from the actual page.

- **Product card extractor** — dedicated JS that finds card containers (`.card`, `li.ng-star-inserted`, etc.) and pairs each card's title with the button *inside that specific card* — solves the "clicked wrong product's Add to Cart" problem
- **Vision confirmation** — screenshots sent to `llama-4-scout` vision model to visually confirm the correct product before every click
- **Smart click resolver** — before every click, scans live DOM and asks the LLM to match intent to real element text
- **New action tokens**: `CLICK_PRODUCT <title>` and `ADD_TO_CART <title>` — scoped to the matched card, not page-wide
- **Healer integration** — failed actions trigger `healer_agent.py` with a screenshot diagnosis

### 2. `planner_agent.py` — QA Architect Agent
Reads the captured locators and produces a machine-parseable step plan — one block per step in a rigid `PAGE/TYPE/METHOD/LOCATOR/DESCRIPTION/NAVIGATES_TO` format.

- Only includes steps that **directly serve the query** — won't generate `clickSearch` for a "login and add to cart" query
- Only includes elements **visible on page load** — skips hidden/collapsed UI panels
- Parses the plan into a structured `OrderedDict` with deduplication

### 3. `generator_agent.py` — Code Generation Agent
Generates one Playwright Page Object `.js` file per page, then assembles the test spec deterministically in Python (zero LLM involvement for the spec).

- Loads `playwright_syntax_reference.md` at startup — full Playwright syntax guide injected into every LLM prompt
- `_fix_strict_mode()` post-processor runs 7 regex passes on every generated file before writing to disk
- Converts all `.first`/`.last` to `.nth(0)`/`.nth(-1)` — solves version compatibility issues
- Injects previous test failure context when regenerating — LLM knows what broke last time

### 4. `validator_agent.py` — Static Analysis Agent
Runs 8 checks on every generated `.js` file before Playwright even runs.

| Code | Severity | Check |
|------|----------|-------|
| E1 | ERROR | `import { test }` in a page object |
| E2 | ERROR | `page` accepted as method parameter |
| E3 | ERROR | Playwright call without `await` (line-by-line check) |
| E4 | ERROR | Missing `export default class` |
| W1 | WARN | `querySelector` used instead of semantic locator |
| W2 | WARN | `NO_LOCATOR` placeholder present |
| W3 | WARN | Promise chaining (`.then(`) instead of async/await |
| W4 | WARN | Bare `page.` call without `this.` |

### 5. `test_runner_agent.py` — Self-Healing Test Runner
Runs `npx playwright test`, parses failures, suggests and applies fixes, then triggers a regeneration loop.

- Auto-detects Windows (`shell=True` + `npx.cmd`) vs Unix
- Distinguishes test failures from pre-run crashes (config/syntax errors)
- Reads **actual file content from disk** before suggesting patches — prevents stale find/replace failures
- Suggests `scrollIntoViewIfNeeded()` for hidden-element timeouts
- Loops up to `MAX_REPAIR_LOOPS` (default: 3) before exiting

### `healer_agent.py` — Failure Diagnosis Agent
Called automatically when any browser action fails during test recording.

- Classifies failure: `ELEMENT_NOT_FOUND`, `TIMEOUT`, `WRONG_PAGE`, `CART_FAILURE`, `UNKNOWN`
- Takes a screenshot of the failure state → sends to vision LLM for diagnosis
- Generates 3 alternative Playwright strategies and executes them live
- Logs every heal attempt to `output/heal_log.json`

---

## Output Structure

After a successful run, `output/` contains:

```
output/
├── pages/
│   ├── LoginPage.js          ← Page Object per page
│   ├── DashboardPage.js
│   └── AdidasOriginal.js
├── tests/
│   └── alltest.spec.js       ← Ready-to-run Playwright spec
├── locator_map.md            ← Full locator table per page
├── planner_output.md         ← Step-by-step automation plan
├── validation_report.md      ← Static analysis results
├── test_run_result.json      ← Test run results + failure details
├── heal_log.json             ← All healer attempts + outcomes
├── screenshots/              ← Vision confirmation screenshots
├── raw_locators.json
└── relevant_locators.json
```

---

## Installation

```bash
# 1. Clone
git clone https://github.com/your-username/PlaywrightAgent
cd PlaywrightAgent

# 2. Python environment
python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate      # Mac/Linux

pip install langgraph langchain-groq playwright python-dotenv pillow

# 3. Playwright browsers
playwright install chromium

# 4. Node.js (for running generated tests)
npm init -y
npm install @playwright/test
npx playwright install

# 5. Environment variables
# Create a .env file:
GROQ_API_KEY=your_groq_api_key_here
```

---

## Usage

```bash
# Single query
python pipeline.py --query "login and add ADIDAS ORIGINAL to cart"

# Interactive mode
python pipeline.py

# Run generated tests manually
npx playwright test ./output/tests/alltest.spec.js --headed
npx playwright test ./output/tests/alltest.spec.js --ui
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Pipeline orchestration | LangGraph (StateGraph with conditional edges) |
| LLM | Groq — `llama-3.3-70b-versatile` |
| Vision | Groq — `meta-llama/llama-4-scout-17b-16e-instruct` |
| Browser automation | Playwright (Python sync API) |
| Generated tests | Playwright (JavaScript / Node.js) |
| State management | TypedDict — fully typed pipeline state |

---

## Key Design Decisions

**Why deterministic spec assembly?**
The `alltest.spec.js` is assembled in Python, not by the LLM. The LLM cannot see the full set of generated Page Object files and tends to duplicate imports, invent method names, and use wrong variable names. Python string manipulation from the parsed plan is 100% reliable.

**Why `playwright_syntax_reference.md`?**
LLMs forget Playwright-specific rules (`.first` vs `.nth()`, `await expect()` syntax) when given only abstract instructions. A concrete reference document with correct and incorrect code examples, injected into every generation prompt, reduces syntax errors significantly.

**Why card-scoped `ADD_TO_CART`?**
The original agent clicked the first "Add to Cart" button on the page — which could be any product. The `ADD_TO_CART <title>` action extracts all product cards, matches the title with LLM text-matching, then clicks the button *inside that specific card container* using `.nth(index)` scoping.

---

## Project Context

Built as a personal side project to explore LLM-driven QA automation. The target application is [Rahul Shetty Academy's demo e-commerce site](https://rahulshettyacademy.com/client), a realistic Angular SPA used for Playwright/Selenium training.

The pipeline evolved through multiple iterations of real failure debugging — each major bug in the output (`.first()` TypeError, strict mode violations, wrong product clicks, hidden element timeouts) resulted in a targeted agent improvement rather than a prompt-only fix.

---

## License

MIT
