"""
query_agent.py  (v2 — Vision + Product-Aware + Healer-Ready)
═══════════════════════════════════════════════════════════════════════════════
WHAT CHANGED FROM v1  (and WHY)
────────────────────────────────
Problem 1 — Product cards were INVISIBLE to the agent
  Old code: querySelectorAll('input,button,a,select,textarea,…')
  These are INTERACTIVE elements only. A product card is a <div>/<li> with
  a title inside <h4>/<h5> and a button inside it. The old extractor saw the
  button but had NO IDEA which product it belonged to — so "Add to Cart"
  could target any product on the page.

  Fix: _extract_product_cards() — dedicated JS that finds card containers,
  reads their title text + price, and returns the locator of the button
  INSIDE that specific card. Now the agent knows:
    card[0].title = "ADIDAS ORIGINAL"
    card[0].btn_locator = 'locator(".card").nth(0).getByRole("button"…)'

Problem 2 — No visual confirmation before clicking
  Old code: resolved click target from text matching alone.
  Fix: _take_screenshot() + _vision_confirm_product() — takes a real PNG,
  encodes it as base64, sends it to llama-4 vision model. The LLM confirms
  which product is visible and where before any click fires.

Problem 3 — Failures were silent (pipeline continued with bad state)
  Old code: printed "❌ Could not click" and moved on.
  Fix: every failed action calls healer_agent.diagnose_and_heal(), passing
  the page screenshot + action + error. The healer returns an alternative
  strategy that is immediately retried. Max 2 attempts per action.

NEW ACTION TOKENS (supported by the LLM planner)
─────────────────────────────────────────────────
  CLICK_PRODUCT  <title>  — navigate INTO a product card
  ADD_TO_CART    <title>  — click that card's specific Add to Cart button

PUBLIC API (unchanged — pipeline.py imports this)
──────────────────────────────────────────────────
    from query_agent import run_query_agent
    state = run_query_agent(state)
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from typing import TYPE_CHECKING

from dotenv import load_dotenv
from langchain_groq import ChatGroq
from playwright.sync_api import sync_playwright, Page
from llm_utils import invoke_llm_with_retry

load_dotenv()

BASE_URL   = "https://rahulshettyacademy.com/client/#/auth/login"
USERNAME   = os.getenv("SITE_USERNAME", "harsh.yadav262002@gmail.com")
PASSWORD   = os.getenv("SITE_PASSWORD", "Harsh@123")
OUTPUT_DIR = "output"
MAX_HEAL_ATTEMPTS = 2

os.makedirs(OUTPUT_DIR,                  exist_ok=True)
os.makedirs(f"{OUTPUT_DIR}/screenshots", exist_ok=True)

_llm = ChatGroq(model="llama-3.3-70b-versatile", temperature=0,
                groq_api_key=os.getenv("GROQ_API_KEY"))

_vision_llm = ChatGroq(model="meta-llama/llama-4-scout-17b-16e-instruct", temperature=0,
                        groq_api_key=os.getenv("GROQ_API_KEY"))

if TYPE_CHECKING:
    from pipeline import PipelineState


# ═════════════════════════════════════════════════════════════════════════════
#  SECTION 1 — SCREENSHOT + VISION
# ═════════════════════════════════════════════════════════════════════════════

def _take_screenshot(page: Page, label: str = "shot") -> str:
    """Capture viewport PNG, save to output/screenshots/, return base64 string."""
    slug      = re.sub(r"[^a-zA-Z0-9]", "_", label)[:40]
    path      = f"{OUTPUT_DIR}/screenshots/{int(time.time())}_{slug}.png"
    png_bytes = page.screenshot(full_page=False, type="png")
    with open(path, "wb") as f:
        f.write(png_bytes)
    print(f"     📷  Screenshot → {path}")
    return base64.b64encode(png_bytes).decode("utf-8")


def _vision_ask(page: Page, question: str, label: str = "vision") -> str:
    """
    Take a screenshot and ask the vision LLM a free-form question about it.
    Returns the LLM's plain-text answer, or an error string on failure.
    """
    b64 = _take_screenshot(page, label)
    try:
        from langchain_core.messages import HumanMessage
        msg = HumanMessage(content=[
            {"type": "text",      "text": question},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ])
        return _vision_llm.invoke([msg]).content.strip()
    except Exception as e:
        return f"vision_error: {e}"


def _vision_confirm_product(page: Page, product_title: str) -> dict:
    """
    Ask the vision LLM whether a specific product is visible on screen and
    where it is.  Returns a dict with found/position_hint/button_text/confidence.
    """
    question = f"""You are a QA test assistant analyzing a screenshot of an e-commerce website.

Find the product: "{product_title}"

Answer in this EXACT JSON format (no markdown fences):
{{
  "found": true or false,
  "position_hint": "e.g. second card from left in first row",
  "button_text": "exact text on the Add to Cart button for this product",
  "confidence": "high or medium or low",
  "description": "one sentence of what you see"
}}"""

    raw = _vision_ask(page, question, f"confirm_{product_title}")
    raw = raw.replace("```json","").replace("```","").strip()
    try:
        result = json.loads(raw)
        print(f"     👁️  Vision: {result.get('description','')}")
        return result
    except Exception:
        return {"found": False, "position_hint": "", "button_text": "", "confidence": "low", "description": raw[:120]}


def _vision_diagnose_failure(page: Page, action: str, error: str) -> str:
    """
    Take a screenshot of the current (failed) state and ask the vision LLM
    to diagnose what went wrong.  Result passed to healer_agent.
    """
    question = f"""You are a QA automation debugger.

Failed action: {action}
Error: {error}

Look at the screenshot and tell me in under 80 words:
1. What is visible on screen right now?
2. Why did the action likely fail?
3. What specific element or text should be targeted instead?"""

    return _vision_ask(page, question, "failure_state")


# ═════════════════════════════════════════════════════════════════════════════
#  SECTION 2 — PRODUCT CARD EXTRACTOR
#  Finds card containers, pairs each card's title with its own button.
# ═════════════════════════════════════════════════════════════════════════════

_PRODUCT_CARD_JS = """
() => {
    const cards = [];

    // Strategy A: find elements that contain both a title AND a button
    const candidateSelectors = [
        '.card', '[class*="card"]', '[class*="product"]',
        'li.ng-star-inserted', '.col-md-4', '.col-sm-4',
    ];

    let cardEls = [];
    for (const sel of candidateSelectors) {
        const found = [...document.querySelectorAll(sel)].filter(el => {
            const r = el.getBoundingClientRect();
            if (r.width < 50 || r.height < 50) return false;
            const cs = window.getComputedStyle(el);
            if (cs.display === 'none' || cs.visibility === 'hidden') return false;
            const hasTitle  = !!el.querySelector('h1,h2,h3,h4,h5,h6,b,strong,[class*="title"],[class*="name"]');
            const hasButton = !!el.querySelector('button,a[class*="btn"],[role="button"]');
            return hasTitle && hasButton;
        });
        if (found.length > 0) { cardEls = found; break; }
    }

    // Strategy B: walk up from cart buttons to find their parent card
    if (cardEls.length === 0) {
        const cartBtns = [...document.querySelectorAll('button')].filter(b => {
            const t = (b.innerText || '').toLowerCase();
            return t.includes('cart') || t.includes('add') || t.includes('buy');
        });
        cartBtns.forEach(btn => {
            let el = btn.parentElement;
            for (let i = 0; i < 6; i++) {
                if (!el) break;
                if (el.querySelector('h1,h2,h3,h4,h5,h6,b,strong')) { cardEls.push(el); break; }
                el = el.parentElement;
            }
        });
    }

    cardEls.forEach((card, idx) => {
        const titleEl = (
            card.querySelector('h5,h4,[class*="title"],[class*="name"],b,strong') ||
            card.querySelector('h1,h2,h3,h6')
        );
        const title = titleEl ? titleEl.innerText.trim() : '';
        if (!title) return;

        const priceEl = card.querySelector('[class*="price"],b,[class*="amount"]');
        const price   = priceEl ? priceEl.innerText.trim() : '';

        const imgEl  = card.querySelector('img');
        const imgAlt = imgEl ? (imgEl.getAttribute('alt') || '') : '';

        const btn     = card.querySelector('button[class*="cart"],button[class*="add"]') || card.querySelector('button');
        const btnText = btn ? btn.innerText.trim() : '';

        // Build a scoped locator for this card's button
        let btnLocator = null;
        if (btn) {
            const tid = btn.getAttribute('data-testid') || btn.getAttribute('data-test');
            const bid = btn.id;
            if (tid)      btnLocator = `getByTestId("${tid}")`;
            else if (bid) btnLocator = `locator("#${bid}")`;
            else          btnLocator = `locator(".card").nth(${idx}).getByRole("button", { name: "${btnText.replace(/"/g,'\\"')}" })`;
        }

        cards.push({ index: idx, title, price, img_alt: imgAlt, button_text: btnText, btn_locator: btnLocator });
    });

    return cards;
}
"""


def _extract_product_cards(page: Page) -> list[dict]:
    """Return all visible product cards with title, price, and scoped button locator."""
    try:
        cards = page.evaluate(_PRODUCT_CARD_JS)
        if cards:
            print(f"     🃏  {len(cards)} product card(s):")
            for c in cards:
                print(f"        [{c['index']}] \"{c['title']}\"  {c['price']}")
        return cards
    except Exception as e:
        print(f"     ⚠️  Card extraction error: {e}")
        return []


def _match_product_card(cards: list[dict], intent: str) -> dict | None:
    """Ask the LLM to pick the best-matching card from a list."""
    if not cards:
        return None
    card_list = "\n".join(
        f'  [{c["index"]}] "{c["title"]}"  price={c["price"]}  img={c["img_alt"]}'
        for c in cards
    )
    resp = invoke_llm_with_retry(_llm,
        f'Product to find: "{intent}"\n\nAvailable cards:\n{card_list}\n\n'
        f'Which index best matches? Respond with ONLY the number, or NONE.'
    )
    if resp.upper() == "NONE":
        return None
    try:
        idx   = int(re.search(r"\d+", resp).group())
        match = next((c for c in cards if c["index"] == idx), None)
        if match:
            print(f'     🎯  Matched card [{idx}]: "{match["title"]}"')
        return match
    except Exception:
        return None


# ═════════════════════════════════════════════════════════════════════════════
#  SECTION 3 — STANDARD DOM EXTRACTOR
# ═════════════════════════════════════════════════════════════════════════════

_EXTRACT_JS = """
() => {
    function getLabel(el) {
        if (el.id) { const l = document.querySelector('label[for="' + el.id + '"]'); if (l) return l.innerText.trim(); }
        const w = el.closest('label');
        if (w) return w.innerText.replace(el.value || '', '').trim();
        const id = el.getAttribute('aria-labelledby');
        if (id) { const r = document.getElementById(id); if (r) return r.innerText.trim(); }
        return '';
    }
    const sel = ['input','button','a','select','textarea',
        '[role="button"]','[role="link"]','[role="checkbox"]',
        '[role="radio"]','[role="combobox"]','[role="tab"]','[role="searchbox"]'];
    const seen = new Set(), items = [];
    document.querySelectorAll(sel.join(',')).forEach(el => {
        if (seen.has(el)) return; seen.add(el);
        const r = el.getBoundingClientRect();
        if (r.width === 0 && r.height === 0) return;
        const cs = window.getComputedStyle(el);
        if (cs.display === 'none' || cs.visibility === 'hidden') return;
        items.push({
            tag: el.tagName.toLowerCase(), type: el.getAttribute('type') || '',
            id: el.id || '', role: el.getAttribute('role') || '',
            aria_label: el.getAttribute('aria-label') || '',
            placeholder: el.getAttribute('placeholder') || '',
            inner_text: (el.innerText || '').trim().substring(0, 120),
            label_text: getLabel(el),
            test_id: el.getAttribute('data-testid') || el.getAttribute('data-test') || '',
            href: el.getAttribute('href') || '',
            disabled: el.disabled || false, required: el.required || false,
        });
    });
    return items;
}
"""


def _best_locator(el: dict) -> dict:
    tag=el.get("tag","").lower(); itype=el.get("type","").lower(); role=el.get("role","")
    aria=(el.get("aria_label","") or "").strip(); label=(el.get("label_text","") or "").strip()
    ph=(el.get("placeholder","") or "").strip(); text=(el.get("inner_text","") or "").strip()
    tid=(el.get("test_id","") or "").strip(); eid=(el.get("id","") or "").strip()
    if not role:
        if tag=="button" or itype in ("button","submit","reset"): role="button"
        elif tag=="a": role="link"
        elif itype=="checkbox": role="checkbox"
        elif itype=="radio": role="radio"
        elif tag=="select": role="combobox"
        elif tag in ("input","textarea"): role="textbox"
    name=aria or label or text
    if role and name:
        n=name.replace('"','\\"')
        return {"strategy":"getByRole","code":f'getByRole("{role}", {{ name: "{n}" }})','quality':'high'}
    if label: return {"strategy":"getByLabel","code":f'getByLabel("{label}")','quality':'high'}
    if ph: return {"strategy":"getByPlaceholder","code":f'getByPlaceholder("{ph}")','quality':'high'}
    if tid: return {"strategy":"getByTestId","code":f'getByTestId("{tid}")','quality':'medium'}
    if text and tag in ("button","a"): return {"strategy":"getByText","code":f'getByText("{text[:60]}")','quality':'medium'}
    if eid: return {"strategy":"locator","code":f'locator("#{eid}")','quality':'low','warning':'CSS fallback'}
    return {"strategy":"unknown","code":None,"quality":"none"}


def _snapshot(page: Page, page_name: str) -> dict:
    raw      = page.evaluate(_EXTRACT_JS)
    enriched = []
    for el in raw:
        loc = _best_locator(el)
        if loc["code"]:
            enriched.append({
                "tag": el["tag"], "type": el["type"],
                "inner_text": el.get("inner_text",""),
                "label": (el.get("label_text") or el.get("aria_label") or
                          el.get("placeholder") or el.get("inner_text") or el.get("id")),
                "required": el["required"], "disabled": el["disabled"], "locator": loc,
            })
    product_cards = _extract_product_cards(page)
    meta = page.evaluate("""() => ({
        title: document.title,
        h1: [...document.querySelectorAll('h1')].map(e=>e.innerText.trim()),
        h2: [...document.querySelectorAll('h2')].map(e=>e.innerText.trim()),
    })""")
    return {"page": page_name, "url": page.url, "meta": meta,
            "elements": enriched, "product_cards": product_cards, "count": len(enriched)}


# ═════════════════════════════════════════════════════════════════════════════
#  SECTION 4 — CLICK STRATEGIES
# ═════════════════════════════════════════════════════════════════════════════

def _resolve_click_target(page: Page, intent: str) -> str | None:
    raw = page.evaluate(_EXTRACT_JS)
    clickables = []
    for el in raw:
        tag=el.get("tag",""); itype=el.get("type",""); role=el.get("role","")
        if tag not in ("button","a") and itype not in ("button","submit") and role not in ("button","link","menuitem","tab"):
            continue
        text=(el.get("inner_text") or el.get("aria_label") or el.get("label_text") or "").strip()
        if text: clickables.append({"tag":tag,"text":text,"href":el.get("href","")})
    if not clickables: return None
    lst="\n".join(f'  [{i+1}] <{c["tag"]}> "{c["text"]}"' for i,c in enumerate(clickables))
    resp=invoke_llm_with_retry(_llm, f'Click intent: "{intent}"\nElements:\n{lst}\nWhich number? (or NONE)')
    if resp.upper()=="NONE": return None
    try:
        idx=int(re.search(r"\d+",resp).group())-1
        if 0<=idx<len(clickables):
            m=clickables[idx]["text"]
            print(f'     🎯  Resolved "{intent}" → "{m}"')
            return m
    except Exception: pass
    return None


def _try_click(page: Page, target: str) -> bool:
    for fn in [
        lambda t=target: page.get_by_role("link",   name=t,exact=False).first.click(timeout=5000),
        lambda t=target: page.get_by_role("button", name=t,exact=False).first.click(timeout=5000),
        lambda t=target: page.get_by_text(t,         exact=False).first.click(timeout=5000),
        lambda t=target: page.locator(f"a:has-text('{t}')").first.click(timeout=4000),
        lambda t=target: page.locator(f"button:has-text('{t}')").first.click(timeout=4000),
    ]:
        try: fn(); page.wait_for_load_state("domcontentloaded",timeout=8000); return True
        except Exception: pass
    return False


def _smart_click(page: Page, intent: str) -> bool:
    real = _resolve_click_target(page, intent)
    for target in ([real, intent] if real and real!=intent else [intent]):
        if target and _try_click(page, target): return True
    return False


def _click_product_by_title(page: Page, product_intent: str) -> bool:
    """Navigate INTO a product card (go to product detail page)."""
    cards  = _extract_product_cards(page)
    vision = _vision_confirm_product(page, product_intent)
    if vision.get("found"):
        print(f'     ✅  Vision confirmed product on screen')

    matched = _match_product_card(cards, product_intent)
    if matched:
        idx   = matched["index"]
        title = matched["title"]
        for fn in [
            lambda: page.get_by_text(title,exact=False).first.click(timeout=5000),
            lambda: page.locator(".card").nth(idx).click(timeout=5000),
            lambda: page.locator(".card-title").nth(idx).click(timeout=5000),
            lambda: page.locator(f"h5:has-text('{title}')").first.click(timeout=5000),
            lambda: page.locator(f"h4:has-text('{title}')").first.click(timeout=5000),
        ]:
            try:
                fn(); page.wait_for_load_state("domcontentloaded",timeout=8000)
                print(f'     ✅  Navigated into: "{title}"'); return True
            except Exception: pass

    return _try_click(page, product_intent)


def _add_product_to_cart(page: Page, product_intent: str) -> bool:
    """
    Click the Add to Cart button INSIDE the matching product card only.
    This is the core fix — scoped to the specific card, not page-wide.
    """
    cards   = _extract_product_cards(page)
    matched = _match_product_card(cards, product_intent)

    if matched:
        idx      = matched["index"]
        title    = matched["title"]
        btn_text = matched.get("button_text","Add To Cart")

        # Take screenshot + visual confirm before clicking
        vision = _vision_confirm_product(page, product_intent)
        if vision.get("found"):
            print(f'     👁️  Vision: "{title}" at {vision.get("position_hint","")}')

        # Card-scoped strategies — these ONLY click inside the matched card
        for fn in [
            lambda: page.locator(".card").nth(idx).get_by_role("button",name=btn_text,exact=False).click(timeout=5000),
            lambda: page.locator(".card").nth(idx).locator("button").first.click(timeout=5000),
            lambda: page.locator(f"h5:has-text('{title}')").locator("..").locator("button").first.click(timeout=5000),
            lambda: page.locator(f"h4:has-text('{title}')").locator("..").locator("button").first.click(timeout=5000),
            lambda: page.locator("li.ng-star-inserted").nth(idx).get_by_role("button").click(timeout=5000),
        ]:
            try:
                fn(); page.wait_for_timeout(1500)
                print(f'     ✅  Added to cart: "{title}"'); return True
            except Exception: pass

    # Fallback: first visible Add to Cart button
    for fn in [
        lambda: page.get_by_role("button",name="Add To Cart",exact=False).first.click(timeout=5000),
        lambda: page.locator("button[class*='cart']").first.click(timeout=5000),
    ]:
        try: fn(); page.wait_for_timeout(1500); print("     ✅  Added (fallback)"); return True
        except Exception: pass

    return False


def _smart_fill(page: Page, hint: str, val: str) -> bool:
    for fn in [
        lambda: page.get_by_label(hint,exact=False).first.fill(val),
        lambda: page.get_by_placeholder(hint,exact=False).first.fill(val),
        lambda: page.get_by_role("textbox",name=hint).first.fill(val),
        lambda: page.locator(f"[id*='{hint.lower()}']").first.fill(val),
        lambda: page.locator(f"[name*='{hint.lower()}']").first.fill(val),
    ]:
        try: fn(); return True
        except Exception: pass
    return False


# ═════════════════════════════════════════════════════════════════════════════
#  SECTION 5 — HEALER BRIDGE
# ═════════════════════════════════════════════════════════════════════════════

def _attempt_heal(page: Page, failed_action: str, error_msg: str, attempt: int) -> bool:
    if attempt > MAX_HEAL_ATTEMPTS:
        print(f"     ❌  Max heal attempts reached — skipping")
        return False
    print(f"\n  🔧  HEALER attempt {attempt}/{MAX_HEAL_ATTEMPTS}...")
    try:
        from healer_agent import diagnose_and_heal
        diagnosis = _vision_diagnose_failure(page, failed_action, error_msg)
        result    = diagnose_and_heal(page, failed_action, error_msg, diagnosis)
        if result["success"]:
            print(f'     ✅  Healer fixed it: {result.get("strategy_used","")}')
        else:
            print(f'     ⚠️  Healer failed: {result.get("reason","")}')
        return result["success"]
    except ImportError:
        print("     ⚠️  healer_agent.py not found — skipping")
        return False
    except Exception as e:
        print(f"     ⚠️  Healer error: {e}")
        return False


# ═════════════════════════════════════════════════════════════════════════════
#  SECTION 6 — LLM ACTION PLANNER
# ═════════════════════════════════════════════════════════════════════════════

def _plan_actions(query: str) -> list[str]:
    prompt = f"""You are a Browser Action Planner for a QA automation tool.

App URL : {BASE_URL}
Login   : email={USERNAME}  password={PASSWORD}

SUPPORTED ACTION TOKENS (use EXACTLY these keywords):
  GOTO_URL       <full_url>
  SNAPSHOT       <descriptive_page_name>
  LOGIN
  CLICK          <plain-english UI element description>
  CLICK_PRODUCT  <product title — navigates to product detail page>
  ADD_TO_CART    <product title — clicks that card's specific Add to Cart>
  FILL           <field_name> | <value>
  GO_BACK
  WAIT           <milliseconds>

RULES:
  1. Always start: GOTO_URL {BASE_URL}
  2. SNAPSHOT immediately after every page arrival
  3. LOGIN is one token — never use FILL+CLICK for login
  4. After LOGIN → SNAPSHOT Dashboard Page
  5. After any navigating action → SNAPSHOT <destination>
  6. For products: use CLICK_PRODUCT (not CLICK) to navigate into a product
  7. For adding to cart: use ADD_TO_CART (not CLICK) — it scopes to the right card
  8. CLICK is only for nav elements like tabs, menus, breadcrumbs

User Query: "{query}"

Output ONLY the action list, one per line. No numbers. No explanations:"""

    text  = invoke_llm_with_retry(_llm, prompt)
    lines = [l.strip() for l in text.splitlines()
             if l.strip() and not l.strip().startswith("```")]
    print(f"\n📋  Action Plan ({len(lines)} steps):")
    for l in lines: print(f"   {l}")
    return lines


# ═════════════════════════════════════════════════════════════════════════════
#  SECTION 7 — BROWSER EXECUTOR
# ═════════════════════════════════════════════════════════════════════════════

def _execute_actions(actions: list[str]) -> list[dict]:
    page_registry: dict[str,dict] = {}
    visit_order:   list[str]      = []

    def _register(snap: dict):
        name = snap["page"]
        if name not in page_registry:
            page_registry[name]=snap; visit_order.append(name)
            print(f"     📸  NEW  '{name}' — {snap['count']} elements @ {snap['url']}")
        else:
            page_registry[name]=snap
            print(f"     🔄  UPD  '{name}' — {snap['count']} elements @ {snap['url']}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        page    = browser.new_context().new_page()
        print("\n🌐  Executing browser actions...")

        for raw in actions:
            parts=raw.split(None,1); action=parts[0].upper() if parts else ""; arg=parts[1].strip() if len(parts)>1 else ""
            print(f"\n  ▶  {raw}")

            try:
                if action == "GOTO_URL":
                    page.goto(arg,timeout=15000)
                    page.wait_for_load_state("domcontentloaded",timeout=10000)

                elif action == "LOGIN":
                    if "#/auth/login" not in page.url:
                        page.goto(BASE_URL,timeout=15000)
                        page.wait_for_selector("#userEmail",timeout=10000)
                    page.fill("#userEmail",USERNAME); page.fill("#userPassword",PASSWORD); page.click("#login")
                    page.wait_for_url("**/dashboard/**",timeout=15000)
                    page.wait_for_load_state("networkidle",timeout=10000)
                    print("     ✅  Logged in")

                elif action == "CLICK":
                    ok = _smart_click(page, arg)
                    if not ok:
                        print(f'     ❌  CLICK failed: "{arg}"')
                        for att in range(1, MAX_HEAL_ATTEMPTS+1):
                            if _attempt_heal(page, raw, f"click failed: {arg}", att): break
                    else:
                        page.wait_for_load_state("domcontentloaded",timeout=8000)

                elif action == "CLICK_PRODUCT":
                    ok = _click_product_by_title(page, arg)
                    if not ok:
                        print(f'     ❌  CLICK_PRODUCT failed: "{arg}"')
                        for att in range(1, MAX_HEAL_ATTEMPTS+1):
                            if _attempt_heal(page, raw, f"product not found: {arg}", att): break
                    else:
                        page.wait_for_load_state("domcontentloaded",timeout=8000)

                elif action == "ADD_TO_CART":
                    ok = _add_product_to_cart(page, arg)
                    if not ok:
                        print(f'     ❌  ADD_TO_CART failed: "{arg}"')
                        for att in range(1, MAX_HEAL_ATTEMPTS+1):
                            if _attempt_heal(page, raw, f"add to cart failed: {arg}", att): break

                elif action == "FILL":
                    hint,val=(arg.split("|",1)+[""])[:2]
                    if not _smart_fill(page,hint.strip(),val.strip()):
                        print(f'     ❌  FILL failed: "{hint.strip()}"')

                elif action == "GO_BACK":
                    page.go_back(timeout=10000)
                    page.wait_for_load_state("domcontentloaded",timeout=8000)

                elif action == "WAIT":
                    page.wait_for_timeout(int(arg) if arg.isdigit() else 1000)

                elif action == "SNAPSHOT":
                    snap=_snapshot(page, arg or f"Page_{len(page_registry)+1}")
                    _register(snap)

                else:
                    print(f"     ⚠️  Unknown token: {action!r}")

            except Exception as exc:
                print(f"     ❌  Exception on [{raw}]: {exc}")

        browser.close()

    return [page_registry[n] for n in visit_order]


# ═════════════════════════════════════════════════════════════════════════════
#  SECTION 8 — FILTER + SAVE + PUBLIC API
# ═════════════════════════════════════════════════════════════════════════════

def _filter_relevant_locators(query: str, pages_data: list[dict]) -> dict:
    lines = []
    for pg in pages_data:
        lines.append(f"\n=== {pg['page']} ({pg['url']}) ===")
        for el in pg["elements"]:
            loc=el["locator"]; label=el.get("label") or "(no label)"
            lines.append(f'  [{el["tag"]}]  page.{loc["code"]}  q={loc["quality"]}  "{label}"')
        for card in pg.get("product_cards",[]):
            lines.append(f'  [CARD]  "{card["title"]}"  price={card["price"]}  btn={card["btn_locator"]}')

    prompt = f"""QA Locator Analyst.
Query: "{query}"
Elements:
{"".join(lines)}

Return ONLY valid JSON (no markdown):
{{"query":"{query}","relevant_locators":[{{"page":"","element_label":"","playwright_locator":"","quality":"","reason":""}}],"summary":""}}"""

    text=invoke_llm_with_retry(_llm, prompt)
    text=text.replace("```json","").replace("```","").strip()
    try: return json.loads(text)
    except Exception: return {"query":query,"relevant_locators":[],"raw":text}


def _save_outputs(query, actions, pages_data, filtered):
    json.dump(pages_data, open(f"{OUTPUT_DIR}/raw_locators.json","w",encoding="utf-8"),indent=2)
    json.dump(filtered,   open(f"{OUTPUT_DIR}/relevant_locators.json","w",encoding="utf-8"),indent=2)
    with open(f"{OUTPUT_DIR}/locator_map.md","w",encoding="utf-8") as f:
        f.write(f"# Locator Map\n\n## Query\n> {query}\n\n## Actions\n")
        for a in actions: f.write(f"- `{a}`\n")
        f.write("\n---\n\n")
        for pg in pages_data:
            f.write(f"### {pg['page']}\n**URL:** `{pg['url']}`\n\n")
            if pg.get("product_cards"):
                f.write("#### 🃏 Product Cards\n| # | Title | Price | Button |\n|---|-------|-------|--------|\n")
                for c in pg["product_cards"]:
                    f.write(f"| {c['index']} | {c['title']} | {c['price']} | {c['button_text']} |\n")
                f.write("\n")
            if pg["elements"]:
                f.write("#### Interactive Elements\n| Label | Tag | Locator | Quality |\n|-------|-----|---------|--------|\n")
                for el in pg["elements"]:
                    loc=el["locator"]
                    f.write(f'| {(el.get("label") or "—")[:40]} | `{el["tag"]}` | `page.{loc["code"]}` | {loc["quality"]} |\n')
                f.write("\n")
    print(f"\n📁  Saved → locator_map.md | raw_locators.json | relevant_locators.json")


def run_query_agent(state: "PipelineState") -> "PipelineState":
    """LangGraph node. Runs browser + captures real locators + product cards."""
    print(f'\n{"="*60}\n  NODE 1 / QUERY AGENT  (vision-enabled + product-aware)\n{"="*60}')
    query=state["query"]; errors=list(state.get("errors",[]))
    actions=_plan_actions(query)
    pages_data=_execute_actions(actions)
    if not pages_data:
        errors.append("QueryAgent: no pages captured")
        return {**state,"actions":actions,"pages_data":[],"filtered_locators":{},"errors":errors}
    print(f"\n📸  {sum(p['count'] for p in pages_data)} elements across {len(pages_data)} page(s)")
    filtered=_filter_relevant_locators(query,pages_data)
    _save_outputs(query,actions,pages_data,filtered)
    return {**state,"actions":actions,"pages_data":pages_data,"filtered_locators":filtered,"errors":errors}