import { expect, test } from "@playwright/test";

test("public reliability explainer needs no pilot session or workflow writes", async ({ page }) => {
  const protectedWrites: string[] = [];
  page.on("request", (request) => {
    if (request.method() !== "GET" && /\/(?:pilot\/sessions|workflows|operator\/)/.test(request.url())) {
      protectedWrites.push(request.url());
    }
  });

  await page.goto("/");
  await page.getByRole("button", { name: /Explore the public reliability walkthrough/i }).click();

  await expect(page.getByRole("heading", { name: /What happens when a lender/i })).toBeVisible();
  await expect(page.getByText(/No account, database write, live lender, or AI service/i)).toBeVisible();
  await expect(page.getByText(/one existing effect can be confirmed/i)).toBeVisible();

  await page.getByRole("button", { name: "Duplicate confirmation" }).click();
  await expect(page.getByRole("button", { name: "Duplicate confirmation" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText(/one effect remains one effect/i)).toBeVisible();

  await page.getByRole("button", { name: /Return to sign in/i }).click();
  await expect(page.getByRole("heading", { name: "Welcome to Verity" })).toBeVisible();
  expect(protectedWrites).toEqual([]);
});
