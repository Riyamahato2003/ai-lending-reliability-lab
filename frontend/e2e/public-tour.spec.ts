import { expect, test } from "@playwright/test";

test("public reliability explainer opens the complete no-login guest workspace", async ({ page }) => {
  const protectedWrites: string[] = [];
  page.on("request", (request) => {
    if (request.method() !== "GET" && /\/(?:pilot\/sessions|workflows|operator\/)/.test(request.url())) {
      protectedWrites.push(request.url());
    }
  });

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Reliability, made visible." })).toBeVisible();
  await expect(page.getByText("Guest explorer")).toBeVisible();
  await expect(page.getByText(/No real lender, credit check/i)).toBeVisible();
  await expect(page.getByText(/Sign in|Invalid credentials/i)).toHaveCount(0);

  await page.getByRole("button", { name: /Create application/i }).click();
  await expect(page.getByRole("heading", { name: "Synthetic applications" })).toBeVisible();
  await page.getByRole("button", { name: /Create synthetic application/i }).click();
  await expect(page.getByText("Application created. Compare the fictional lender offers below.")).toBeVisible();
  await page.getByRole("button", { name: /Compare three offers/i }).click();
  await expect(page.getByRole("button", { name: /Approve selected fictional offer/i })).toBeVisible();
  await page.getByRole("button", { name: /Approve selected fictional offer/i }).click();
  await page.getByLabel("Failure scenario").selectOption("lost");
  await page.getByRole("button", { name: /Submit to simulated lender/i }).click();
  await expect(page.getByRole("heading", { name: "Unknown is not failure" })).toBeVisible();
  await page.getByRole("button", { name: /Reconcile original effect/i }).click();
  await expect(page.getByText("One effect verified.")).toBeVisible();

  await page.getByRole("button", { name: /AI Intelligence/i }).click();
  await page.getByRole("button", { name: /Explain with synthetic evidence/i }).click();
  await expect(page.getByText("Verity explanation")).toBeVisible();

  await page.getByRole("button", { name: /Reliability Lab/i }).click();
  await page.getByRole("button", { name: /Run guarded strategy/i }).click();
  await page.getByRole("button", { name: /Run unsafe baseline/i }).click();
  await expect(page.getByText(/2 effects · 1 duplicate from blind retry/i)).toBeVisible();

  expect(protectedWrites).toEqual([]);
});

test("pilot sign-in stays isolated at its explicit private entry path", async ({ page }) => {
  await page.goto("/?pilot=1");
  await expect(page.getByRole("heading", { name: "Welcome to Verity" })).toBeVisible();
  await expect(page.getByLabel("Password")).toBeVisible();
});
