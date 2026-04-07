import { test, expect } from '@playwright/test';
import LoginPage from '../pages/LoginPage.js';
import DashboardPage from '../pages/DashboardPage.js';
import AdidasOriginal from '../pages/AdidasOriginal.js';


test('end-to-end test', async ({ page }) => {
  const loginPage = new LoginPage(page);
  const dashboardPage = new DashboardPage(page);
  const adidasOriginal = new AdidasOriginal(page);
  await page.goto('https://rahulshettyacademy.com/client/#/auth/login');

  // ── Login Page ──
  await loginPage.enterEmail('harsh.yadav262002@gmail.com');
  await loginPage.enterPassword('Harsh@123');
  await loginPage.clickLogin();
  // → navigated to: Dashboard Page
  await loginPage.enterEmail_2('harsh.yadav262002@gmail.com');
  await loginPage.enterPassword_2('Harsh@123');
  await loginPage.clickLogin_2();
  // → navigated to: Dashboard Page
  await loginPage.enterEmail_3('harsh.yadav262002@gmail.com');
  await loginPage.enterPassword_3('Harsh@123');
  await loginPage.clickLogin_3();
  // → navigated to: Dashboard Page
  await loginPage.enterEmail_4('harsh.yadav262002@gmail.com');
  await loginPage.enterPassword_4('Harsh@123');
  await loginPage.clickLogin_4();
  // → navigated to: Dashboard Page
  await loginPage.enterEmail_5('harsh.yadav262002@gmail.com');
  await loginPage.enterPassword_5('Harsh@123');
  await loginPage.clickLogin_5();
  // → navigated to: Dashboard Page
  await loginPage.enterEmail_6('harsh.yadav262002@gmail.com');
  await loginPage.enterPassword_6('Harsh@123');
  await loginPage.clickLogin_6();
  // → navigated to: Dashboard Page
  await loginPage.enterEmail_7('harsh.yadav262002@gmail.com');
  await loginPage.enterPassword_7('Harsh@123');
  await loginPage.clickLogin_7();
  // → navigated to: Dashboard Page
  await loginPage.enterEmail_8('harsh.yadav262002@gmail.com');
  await loginPage.enterPassword_8('Harsh@123');
  await loginPage.clickLogin_8();
  // → navigated to: Dashboard Page
  await loginPage.enterEmail_9('harsh.yadav262002@gmail.com');
  await loginPage.enterPassword_9('Harsh@123');
  await loginPage.clickLogin_9();
  // → navigated to: Dashboard Page
  await loginPage.enterEmail_10('harsh.yadav262002@gmail.com');
  await loginPage.enterPassword_10('Harsh@123');
  await loginPage.clickLogin_10();
  // → navigated to: Dashboard Page

  // ── Dashboard Page ──
  await dashboardPage.verifyDashboard();
  await dashboardPage.clickADIDASORIGINAL();
  await dashboardPage.enterSearchTerm('test_value');
  await dashboardPage.clickViewADIDASORIGINAL();
  await dashboardPage.enterSearchTerm_2('test_value');
  await dashboardPage.clickSearchResult();
  // → navigated to: ADIDAS ORIGINAL
  await dashboardPage.verifyDashboard_2();
  await dashboardPage.enterSearchTerm_3('test_value');
  await dashboardPage.verifyDashboard_3();
  await dashboardPage.enterSearchTerm_4('test_value');
  await dashboardPage.clickADIDASORIGINAL_2();
  // → navigated to: ADIDAS ORIGINAL
  await dashboardPage.verifyDashboard_4();
  await dashboardPage.enterSearchTerm_5('test_value');
  await dashboardPage.verifyDashboard_5();
  await dashboardPage.enterSearchTerm_6('test_value');
  await dashboardPage.verifyDashboard_6();
  await dashboardPage.navigateToProductPage();
  // → navigated to: ADIDAS ORIGINAL
  await dashboardPage.verifyDashboard_7();
  await dashboardPage.enterSearchTerm_7('test_value');
  await dashboardPage.verifyDashboard_8();
  await dashboardPage.enterSearchTerm_8('test_value');
  await dashboardPage.verifyDashboard_9();
  await dashboardPage.navigateToProductPage_2();
  // → navigated to: ADIDAS ORIGINAL
  await dashboardPage.verifyDashboard_10();

  // ── ADIDAS ORIGINAL ──
  await adidasOriginal.verifyProductPage();
  await adidasOriginal.clickAddToCart();
  await adidasOriginal.verifyProductPage_2();
  await adidasOriginal.clickAddToCart_2();
  await adidasOriginal.verifyProductPage_3();
  await adidasOriginal.clickAddToCart_3();
  await adidasOriginal.verifyProductPage_4();
  await adidasOriginal.clickAddToCart_4();
  await adidasOriginal.verifyProductPage_5();
  await adidasOriginal.clickAddToCart_5();
  await adidasOriginal.verifyProductPage_6();
  await adidasOriginal.clickAddToCart_6();
  await adidasOriginal.verifyProductPage_7();
  await adidasOriginal.clickAddToCart_7();
});
