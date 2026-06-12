import { test, expect, type Frame, type Page } from "@playwright/test";

const host = process.env.E2E_HOST ?? "http://localhost:3000";
const path = process.env.E2E_COMPOSER_PATH ?? "/";
const iframeName = process.env.E2E_IFRAME_NAME;
const menuSelector = process.env.E2E_COMPOSER_TRIGGER;

const resolveScope = async (page: Page): Promise<Frame | Page> => {
  if (!iframeName) return page;
  const frame = page.frame({ name: iframeName });
  if (!frame) throw new Error(`iframe '${iframeName}' not found`);
  await frame.waitForLoadState("domcontentloaded");
  return frame;
};

const getHeight = (scope: Frame | Page, selector: string) =>
  scope.locator(selector).evaluate((el) => (el as HTMLElement).getBoundingClientRect().height);

test("composer textarea grows with input", async ({ page }) => {
  await page.goto(`${host}${path}`);
  if (menuSelector) {
    await page.click(menuSelector);
  }

  const scope = await resolveScope(page);
  const textarea = scope.getByRole("textbox").first();

  await textarea.waitFor({ state: "visible" });
  const initialHeight = await textarea.evaluate(
    (el) => (el as HTMLTextAreaElement).getBoundingClientRect().height,
  );
  expect(initialHeight).toBeGreaterThan(40);
  expect(initialHeight).toBeLessThan(80);

  await textarea.fill("第一行\n第二行\n第三行");
  const grownHeight = await textarea.evaluate(
    (el) => (el as HTMLTextAreaElement).getBoundingClientRect().height,
  );
  expect(grownHeight).toBeGreaterThan(initialHeight);
  expect(grownHeight).toBeLessThan(260);

  const formHeight = await getHeight(scope, "form");
  expect(formHeight).toBeGreaterThan(grownHeight);
});
