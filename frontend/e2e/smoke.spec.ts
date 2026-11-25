import { test, expect } from "@playwright/test";

test("borrower can create and evaluate a synthetic workflow, then ask grounded AI", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Welcome to Verity" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Borrower demo" }).click();
  await page.getByLabel("Password").fill("borrower-demo");
  await page.getByRole("button", { name: /Enter workspace/ }).click();
  await expect(page.getByText("Good afternoon")).toBeVisible();
  await page.getByRole("button", { name: /Manage applications/ }).click();
  await page.getByRole("button", { name: "New application" }).click();
  await expect(page.getByText("Explicit lending consent")).toBeVisible();
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: /Create synthetic workflow/ }).click();
  await expect(
    page.getByRole("heading", { name: "Workflow details" }),
  ).toBeVisible();
  await page.getByRole("button", { name: /Run evaluation/ }).click();
  await expect(
    page.getByText("Simulated offers", { exact: true }),
  ).toBeVisible();
  await expect(
    page.locator(".offer-card").getByText("Cedar", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: /AI Intelligence/ }).click();
  await expect(
    page.getByRole("heading", { name: /Ask what the evidence says/ }),
  ).toBeVisible();
  await page.getByRole("button", { name: /Why am I not eligible/ }).click();
  await expect(
    page.getByText(/Under policy|recorded rules pass/i).first(),
  ).toBeVisible();
  await expect(page.getByText(/No AI writes/i)).toBeVisible();
});

test("operator runs isolated safe and unsafe comparison", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Operator lab" }).click();
  await page.getByLabel("Password").fill("operator-demo");
  await page.getByRole("button", { name: /Enter workspace/ }).click();
  await expect(
    page.getByRole("heading", { name: /Observe the failure/ }),
  ).toBeVisible();
  await page.getByRole("button", { name: /Open Reliability Lab/ }).click();
  await expect(
    page.getByRole("heading", { name: /Reliability Lab/ }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: /Run safe \/ unsafe comparison/ })
    .click();
  await expect(page.getByRole("heading", { name: /Same fault/ })).toBeVisible();
  await expect(page.getByText("Invariant accounting")).toBeVisible();
  await expect(page.getByText("Trial-level evidence")).toBeVisible();
});

test("explicit approval is consumed and operator worker confirms only a synthetic effect", async ({
  browser,
}) => {
  const borrower = await browser.newPage();
  await borrower.goto("/");
  await borrower.getByRole("button", { name: "Borrower demo" }).click();
  await borrower.getByLabel("Password").fill("borrower-demo");
  await borrower.getByRole("button", { name: /Enter workspace/ }).click();
  await borrower.getByRole("button", { name: /Manage applications/ }).click();
  await borrower.getByRole("button", { name: "New application" }).click();
  await borrower.getByRole("checkbox").check();
  await borrower
    .getByRole("button", { name: /Create synthetic workflow/ })
    .click();
  await borrower.getByRole("button", { name: /Run evaluation/ }).click();
  await expect(borrower.locator(".offer-card").first()).toBeVisible();
  await borrower.getByRole("button", { name: "Request approval" }).click();
  await expect(
    borrower.getByRole("button", { name: /Confirm single-use approval/ }),
  ).toBeVisible();
  await borrower
    .getByRole("button", { name: /Confirm single-use approval/ })
    .click();
  await borrower.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(
    borrower.getByText("Submitting", { exact: true }).first(),
  ).toBeVisible();
  const operator = await browser.newPage();
  await operator.goto("/");
  await operator.getByRole("button", { name: "Operator lab" }).click();
  await operator.getByLabel("Password").fill("operator-demo");
  await operator.getByRole("button", { name: /Enter workspace/ }).click();
  await operator.getByRole("button", { name: /Open Reliability Lab/ }).click();
  await operator
    .getByRole("button", { name: /Process next approved dispatch/ })
    .click();
  await expect(operator.getByText(/Dispatch worker result/)).toBeVisible();
  await borrower.getByRole("button", { name: "Refresh workspace" }).click();
  await expect(borrower.locator(".detail-heading .state-tag")).toContainText(
    "Submitted",
  );
  await borrower.close();
  await operator.close();
});

test("borrower workspace remains navigable on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByRole("button", { name: "Borrower demo" }).click();
  await page.getByLabel("Password").fill("borrower-demo");
  await page.getByRole("button", { name: /Enter workspace/ }).click();
  await page.getByRole("button", { name: "Open menu" }).click();
  await page
    .getByRole("button", { name: /Applications Borrower lifecycle/ })
    .click();
  await expect(
    page.getByRole("heading", { name: "Applications" }),
  ).toBeVisible();
});
