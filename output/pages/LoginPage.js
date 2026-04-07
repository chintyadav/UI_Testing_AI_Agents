import { expect } from '@playwright/test';

export default class LoginPage {
  constructor(page) {
    this.page = page;
  }

  async enterEmail(value) {
    await this.page.getByPlaceholder("email@example.com").nth(0).fill(value);
  }

  async enterPassword(value) {
    await this.page.getByPlaceholder("enter your passsword").nth(0).fill(value);
  }

  async clickLogin() {
    await this.page.locator("#login").nth(0).click();
  }

  async enterEmail_2(value) {
    await this.page.getByPlaceholder("email@example.com").nth(0).fill(value);
  }

  async enterPassword_2(value) {
    await this.page.getByPlaceholder("enter your passsword").nth(0).fill(value);
  }

  async clickLogin_2() {
    await this.page.locator("#login").nth(0).click();
  }

  async enterEmail_3(value) {
    await this.page.getByPlaceholder("email@example.com").nth(0).fill(value);
  }

  async enterPassword_3(value) {
    await this.page.getByPlaceholder("enter your passsword").nth(0).fill(value);
  }

  async clickLogin_3() {
    await this.page.locator("#login").nth(0).click();
  }

  async enterEmail_4(value) {
    await this.page.getByPlaceholder("email@example.com").nth(0).fill(value);
  }

  async enterPassword_4(value) {
    await this.page.getByPlaceholder("enter your passsword").nth(0).fill(value);
  }

  async clickLogin_4() {
    await this.page.locator("#login").nth(0).click();
  }

  async enterEmail_5(value) {
    await this.page.getByPlaceholder("email@example.com").nth(0).fill(value);
  }

  async enterPassword_5(value) {
    await this.page.getByPlaceholder("enter your passsword").nth(0).fill(value);
  }

  async clickLogin_5() {
    await this.page.locator("#login").nth(0).click();
  }

  async enterEmail_6(value) {
    await this.page.getByPlaceholder("email@example.com").nth(0).fill(value);
  }

  async enterPassword_6(value) {
    await this.page.getByPlaceholder("enter your passsword").nth(0).fill(value);
  }

  async clickLogin_6() {
    await this.page.locator("#login").nth(0).click();
  }

  async enterEmail_7(value) {
    await this.page.getByPlaceholder("email@example.com").nth(0).fill(value);
  }

  async enterPassword_7(value) {
    await this.page.getByPlaceholder("enter your passsword").nth(0).fill(value);
  }

  async clickLogin_7() {
    await this.page.locator("#login").nth(0).click();
  }

  async enterEmail_8(value) {
    await this.page.getByPlaceholder("email@example.com").nth(0).fill(value);
  }

  async enterPassword_8(value) {
    await this.page.getByPlaceholder("enter your passsword").nth(0).fill(value);
  }

  async clickLogin_8() {
    await this.page.locator("#login").nth(0).click();
  }

  async enterEmail_9(value) {
    await this.page.getByPlaceholder("email@example.com").nth(0).fill(value);
  }

  async enterPassword_9(value) {
    await this.page.getByPlaceholder("enter your passsword").nth(0).fill(value);
  }

  async clickLogin_9() {
    await this.page.locator("#login").nth(0).click();
  }

  async enterEmail_10(value) {
    await this.page.getByPlaceholder("email@example.com").nth(0).fill(value);
  }

  async enterPassword_10(value) {
    await this.page.getByPlaceholder("enter your passsword").nth(0).fill(value);
  }

  async clickLogin_10() {
    await this.page.locator("#login").nth(0).click();
  }
}