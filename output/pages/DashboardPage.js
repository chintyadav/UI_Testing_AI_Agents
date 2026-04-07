import { expect } from '@playwright/test';

export default class DashboardPage {
  constructor(page) {
    this.page = page;
  }

  async verifyDashboard() {
    await expect(this.page.getByRole("button", { name: "HOME" })).toBeVisible();
  }

  async clickADIDASORIGINAL() {
    // Since the exact locator for the product is not available, 
    // we will assume that after entering the search term, the product will be visible and we can click on it to navigate to the product page.
    await this.page.getByPlaceholder("search").nth(0).fill("ADIDAS ORIGINAL");
    await this.page.locator(".card", { hasText: "ADIDAS ORIGINAL" }).getByRole("button").nth(0).click();
  }

  async enterSearchTerm(value) {
    await this.page.getByPlaceholder("search").nth(0).fill(value);
  }

  async clickViewADIDASORIGINAL() {
    // This step is not possible as we don't have the exact locator for the view button of the specific product, 
    // instead we will navigate to the product page using a different approach.
    await this.page.locator(".card", { hasText: "ADIDAS ORIGINAL" }).getByRole("button").nth(0).click();
  }

  async enterSearchTerm_2(value) {
    await this.page.getByPlaceholder("search").nth(0).fill(value);
  }

  async clickSearchResult() {
    // Since the exact locator for the search result is not available, 
    // we will assume that after entering the search term, the product will be visible and we can click on it to navigate to the product page.
    await this.page.locator(".card", { hasText: "ADIDAS ORIGINAL" }).getByRole("button").nth(0).click();
  }

  async verifyDashboard_2() {
    await expect(this.page.getByRole("button", { name: "HOME" })).toBeVisible();
  }

  async enterSearchTerm_3(value) {
    await this.page.getByPlaceholder("search").nth(0).fill(value);
  }

  async verifyDashboard_3() {
    await expect(this.page.getByRole("button", { name: "HOME" })).toBeVisible();
  }

  async enterSearchTerm_4(value) {
    await this.page.getByPlaceholder("search").nth(0).fill(value);
  }

  async clickADIDASORIGINAL_2() {
    // Since the exact locator for the product is not available, 
    // we will assume that after entering the search term, the product will be visible and we can click on it to navigate to the product page.
    await this.page.locator(".card", { hasText: "ADIDAS ORIGINAL" }).getByRole("button").nth(0).click();
  }

  async verifyDashboard_4() {
    await expect(this.page.getByRole("button", { name: "HOME" })).toBeVisible();
  }

  async enterSearchTerm_5(value) {
    await this.page.getByPlaceholder("search").nth(0).fill(value);
  }

  async verifyDashboard_5() {
    await expect(this.page.getByRole("button", { name: "HOME" })).toBeVisible();
  }

  async enterSearchTerm_6(value) {
    await this.page.getByPlaceholder("search").nth(0).fill(value);
  }

  async verifyDashboard_6() {
    await expect(this.page.getByRole("button", { name: "HOME" })).toBeVisible();
  }

  async navigateToProductPage() {
    // Since the exact locator for the product is not available, 
    // we will assume that we can navigate to the product page directly.
    await this.page.locator(".card", { hasText: "ADIDAS ORIGINAL" }).getByRole("button").nth(0).click();
  }

  async verifyDashboard_7() {
    await expect(this.page.getByRole("button", { name: "HOME" })).toBeVisible();
  }

  async enterSearchTerm_7(value) {
    await this.page.getByPlaceholder("search").nth(0).fill(value);
  }

  async verifyDashboard_8() {
    await expect(this.page.getByRole("button", { name: "HOME" })).toBeVisible();
  }

  async enterSearchTerm_8(value) {
    await this.page.getByPlaceholder("search").nth(0).fill(value);
  }

  async verifyDashboard_9() {
    await expect(this.page.getByRole("button", { name: "HOME" })).toBeVisible();
  }

  async navigateToProductPage_2() {
    // Since the exact locator for the product is not available, 
    // we will assume that we can navigate to the product page directly.
    await this.page.locator(".card", { hasText: "ADIDAS ORIGINAL" }).getByRole("button").nth(0).click();
  }

  async verifyDashboard_10() {
    await expect(this.page.getByRole("button", { name: "HOME" })).toBeVisible();
  }
}