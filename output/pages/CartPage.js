import { expect } from '@playwright/test';

export default class CartPage {
  constructor(page) {
    this.page = page;
  }

  async verifyCartPage() {
    await expect(this.page.getByRole("link", { name: "Continue Shopping" })).toBeVisible();
  }
}