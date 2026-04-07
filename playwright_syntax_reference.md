# Playwright Page Object Model — Syntax Reference
# Version: Playwright 1.40+  |  Language: JavaScript (ES Modules)
# Purpose: GeneratorAgent reads this file to produce syntactically correct Page Objects.
# ─────────────────────────────────────────────────────────────────────────────
# CRITICAL RULES (violating any of these causes runtime errors):
#   1. `.first` is a PROPERTY, not a function.  NEVER write `.first()`.
#   2. Every Playwright call must be preceded by `await`.
#   3. `fill()` and `click()` are FUNCTIONS — they take parentheses.
#   4. Page Objects import `expect` only — NEVER import `test`.
#   5. Always use `this.page` — never accept `page` as a method parameter.
# ─────────────────────────────────────────────────────────────────────────────

---

## FILE STRUCTURE

Every Page Object file must follow this exact structure:

```javascript
import { expect } from '@playwright/test';

export default class PageName {
  constructor(page) {
    this.page = page;
  }

  // methods go here
}
```

---

## LOCATOR METHODS
These are called on `this.page` and return a Locator object.
They do NOT need `await` — they are synchronous builders.

| Method | Syntax | Example |
|--------|--------|---------|
| getByRole | `this.page.getByRole("role", { name: "text" })` | `this.page.getByRole("button", { name: "Login" })` |
| getByLabel | `this.page.getByLabel("label text")` | `this.page.getByLabel("Email")` |
| getByPlaceholder | `this.page.getByPlaceholder("placeholder text")` | `this.page.getByPlaceholder("email@example.com")` |
| getByText | `this.page.getByText("visible text")` | `this.page.getByText("Sign In")` |
| getByTestId | `this.page.getByTestId("data-testid value")` | `this.page.getByTestId("submit-btn")` |
| locator (CSS) | `this.page.locator("css selector")` | `this.page.locator("#login")` |
| locator (nth) | `this.page.locator("css").nth(0)` | `this.page.locator(".card").nth(2)` |

---

## LOCATOR FILTERS — when multiple elements match

**Use `.nth()` exclusively. Never use `.first` or `.last`.**

`.nth()` is a METHOD with parentheses. It works on every Playwright version.
`.first` and `.last` are properties that exist only in certain versions and
cause `TypeError: .nth(0).fill is not a function` on many installations.

| Filter | Type | Syntax | Notes |
|--------|------|--------|-------|
| `.nth(0)` | **METHOD** | `locator.nth(0)` | First element. Always use this instead of .first |
| `.nth(-1)` | **METHOD** | `locator.nth(-1)` | Last element. Always use this instead of .last |
| `.nth(n)` | **METHOD** | `locator.nth(2)` | Any specific position (0-indexed) |

### CORRECT usage:
```javascript
// Always .nth(0) — never .first
await this.page.getByPlaceholder("search").nth(0).fill("test");
await this.page.getByRole("button", { name: "Add" }).nth(0).click();
await this.page.locator(".card").nth(0).click();
await this.page.locator(".card").nth(0).getByRole("button").click();
await this.page.getByRole("listitem").nth(2).click();
```

### WRONG usage (will throw TypeError at runtime):
```javascript
// ❌ NEVER .first — causes TypeError on many Playwright versions
await this.page.getByPlaceholder("search").nth(0).fill("test");
await this.page.getByPlaceholder("search").first().fill("test");
await this.page.locator(".card").nth(0).click();

// ❌ NEVER .last
await this.page.locator(".card").last.click();

// ❌ missing await
this.page.getByRole("button", { name: "Login" }).click();

// ❌ page as method argument
async clickLogin(page) { await page.locator("#login").click(); }
```

---

## ACTION METHODS
These are called ON a Locator and MUST be preceded by `await`.

| Method | Syntax | Use for |
|--------|--------|---------|
| `.click()` | `await locator.click()` | buttons, links, checkboxes |
| `.fill(value)` | `await locator.fill(value)` | text inputs, textareas |
| `.type(value)` | `await locator.type(value)` | character-by-character typing |
| `.check()` | `await locator.check()` | checkboxes |
| `.uncheck()` | `await locator.uncheck()` | checkboxes |
| `.selectOption(value)` | `await locator.selectOption("option")` | `<select>` dropdowns |
| `.hover()` | `await locator.hover()` | mouse hover |
| `.clear()` | `await locator.clear()` | clear an input |
| `.press(key)` | `await locator.press("Enter")` | keyboard keys |

---

## ASSERTION METHODS
These use `expect()` and MUST be preceded by `await`.
The locator goes INSIDE `expect()` — there is NO standalone `await` before the locator.

| Assertion | Syntax | Use for |
|-----------|--------|---------|
| toBeVisible | `await expect(this.page.locator).toBeVisible()` | element is on screen |
| toHaveText | `await expect(this.page.locator).toHaveText("text")` | element has exact text |
| toContainText | `await expect(this.page.locator).toContainText("text")` | element contains text |
| toHaveValue | `await expect(this.page.locator).toHaveValue("value")` | input has value |
| toBeEnabled | `await expect(this.page.locator).toBeEnabled()` | element is not disabled |
| toBeChecked | `await expect(this.page.locator).toBeChecked()` | checkbox is checked |
| toHaveCount | `await expect(this.page.locator).toHaveCount(n)` | locator matches n elements |
| toHaveURL | `await expect(this.page).toHaveURL("url")` | page URL matches |
| toHaveTitle | `await expect(this.page).toHaveTitle("title")` | page title matches |

### CORRECT assertion examples:
```javascript
// Heading visible on page
await expect(this.page.getByRole("heading", { name: "Login" })).toBeVisible();

// Specific text present
await expect(this.page.getByText("Welcome back")).toBeVisible();

// Input has correct value
await expect(this.page.getByLabel("Email")).toHaveValue("user@example.com");

// URL check
await expect(this.page).toHaveURL(/.*dashboard/);
```

### WRONG assertion examples:
```javascript
// ❌ await on the locator itself, not on expect
await this.page.getByRole("heading").toBeVisible();

// ❌ missing await on expect
expect(this.page.getByText("Welcome")).toBeVisible();
```

---

## NAVIGATION METHODS
Called directly on `this.page`, always `await`.

```javascript
await this.page.goto("https://example.com");
await this.page.waitForURL("**/dashboard/**");
await this.page.waitForLoadState("domcontentloaded");
await this.page.waitForLoadState("networkidle");
await this.page.waitForTimeout(1000);   // milliseconds
await this.page.waitForSelector("css selector");
await this.page.reload();
await this.page.goBack();
```

---

## METHOD PATTERNS BY TYPE

### TYPE: ACTION — user interacts with an element
```javascript
// Click a button
async clickLogin() {
  await this.page.getByRole("button", { name: "Login" }).click();
}

// Fill an input (accepts a value parameter)
async enterEmail(value) {
  await this.page.getByLabel("Email").fill(value);
}

// Fill with .first when locator may match multiple elements
async enterSearchTerm(value) {
  await this.page.getByPlaceholder("search").nth(0).fill(value);
}

// Click a scoped element inside a card
async clickAddToCart() {
  await this.page.locator(".card").nth(0).getByRole("button", { name: "Add To Cart" }).click();
}

// Click a link
async clickForgotPassword() {
  await this.page.getByRole("link", { name: "Forgot password?" }).click();
}
```

### TYPE: VALIDATION — assert page state
```javascript
// Assert a heading is visible (confirm page loaded)
async verifyLoginPage() {
  await expect(this.page.getByRole("heading", { name: "Login" })).toBeVisible();
}

// Assert an element with specific text is visible
async verifyProductName() {
  await expect(this.page.getByText("ADIDAS ORIGINAL")).toBeVisible();
}

// Assert page URL
async verifyDashboardUrl() {
  await expect(this.page).toHaveURL(/.*dashboard/);
}
```

---

## COMPLETE PAGE OBJECT EXAMPLE

```javascript
import { expect } from '@playwright/test';

export default class LoginPage {
  constructor(page) {
    this.page = page;
  }

  async enterEmail(value) {
    await this.page.getByPlaceholder("email@example.com").fill(value);
  }

  async enterPassword(value) {
    await this.page.getByPlaceholder("enter your passsword").fill(value);
  }

  async clickLogin() {
    await this.page.locator("#login").click();
  }

  async verifyLoginPage() {
    await expect(
      this.page.getByRole("link", { name: "dummywebsite@rahulshettyacademy.com" })
    ).toBeVisible();
  }
}
```

```javascript
import { expect } from '@playwright/test';

export default class DashboardPage {
  constructor(page) {
    this.page = page;
  }

  async verifyDashboardPage() {
    await expect(this.page.getByRole("heading", { name: "Practise" })).toBeVisible();
  }

  async enterSearchTerm(value) {
    // .nth(0) prevents strict mode violation — NEVER use .first
    await this.page.getByPlaceholder("search").nth(0).fill(value);
  }

  async clickAddToCartForProduct(productTitle) {
    // Scope button click to the specific card to avoid wrong-product clicks
    await this.page
      .locator(".card", { hasText: productTitle })
      .getByRole("button", { name: "Add To Cart" })
      .click();
  }
}
```

---

## STRICT MODE VIOLATION — PREVENTION RULES

Playwright runs in strict mode by default: if a locator matches MORE than one
element, calling `.click()` or `.fill()` throws an error.

Rules to prevent this:

1. **Always use the most specific locator available.**
   Prefer `getByRole("button", { name: "Login" })` over `getByRole("button")`.

2. **Always use `.nth(0)` (method, with parentheses) when a locator may
   match multiple elements and you want the first one.**
   ```javascript
   await this.page.getByPlaceholder("search").nth(0).fill(value);
   ```
   NEVER use `.first` — it causes TypeError on many Playwright versions.

3. **Use `.nth(index)` for any specific position.**
   ```javascript
   await this.page.locator(".card").nth(2).click();
   ```

4. **Scope nested elements using chaining** when a generic locator is inside
   a known container.**
   ```javascript
   await this.page.locator(".card", { hasText: "ADIDAS ORIGINAL" })
     .getByRole("button", { name: "Add To Cart" })
     .click();
   ```

---

## QUICK CHEATSHEET

```
USE THIS:     locator.nth(0)      ← first element  (method, with parens)
              locator.nth(-1)     ← last element   (method, with parens)
              locator.nth(2)      ← third element  (method, with parens)

NEVER USE:    locator.first       ← TypeError on many Playwright versions
              locator.first()     ← TypeError: first is not a function
              locator.last        ← same problem
              locator.last()      ← same problem

ALWAYS await:   .click()  .fill()  .type()  .hover()  .press()
                expect(...).toBeVisible()  etc.

NEVER await:    this.page.getByRole(...)   (returns Locator synchronously)
                this.page.locator(...)     (returns Locator synchronously)
                locator.nth(0)             (returns Locator synchronously)
```