import { expect } from '@playwright/test';

export default class AdidasOriginal {
  constructor(page) {
    this.page = page;
  }

  async verifyProductPage() {
    await expect(this.page.getByRole("button", { name: "Add To Cart" })).toBeVisible();
  }

  async clickAddToCart() {
    await this.page.getByRole("button", { name: "Add To Cart" }).nth(0).click();
  }

  async verifyProductPage_2() {
    await expect(this.page.getByRole("button", { name: "Add To Cart" })).toBeVisible();
  }

  async clickAddToCart_2() {
    await this.page.getByRole("button", { name: "Add To Cart" }).nth(0).click();
  }

  async verifyProductPage_3() {
    await expect(this.page.getByRole("button", { name: "Add To Cart" })).toBeVisible();
  }

  async clickAddToCart_3() {
    await this.page.getByRole("button", { name: "Add To Cart" }).nth(0).click();
  }

  async verifyProductPage_4() {
    await expect(this.page.getByRole("button", { name: "Add To Cart" })).toBeVisible();
  }

  async clickAddToCart_4() {
    await this.page.getByRole("button", { name: "Add To Cart" }).nth(0).click();
  }

  async verifyProductPage_5() {
    await expect(this.page.getByRole("button", { name: "Add To Cart" })).toBeVisible();
  }

  async clickAddToCart_5() {
    await this.page.getByRole("button", { name: "Add To Cart" }).nth(0).click();
  }

  async verifyProductPage_6() {
    await expect(this.page.getByRole("button", { name: "Add To Cart" })).toBeVisible();
  }

  async clickAddToCart_6() {
    await this.page.getByRole("button", { name: "Add To Cart" }).nth(0).click();
  }

  async verifyProductPage_7() {
    await expect(this.page.getByRole("button", { name: "Add To Cart" })).toBeVisible();
  }

  async clickAddToCart_7() {
    await this.page.getByRole("button", { name: "Add To Cart" }).nth(0).click();
  }
}