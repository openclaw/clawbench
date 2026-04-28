const { chromium } = require("playwright");

async function main() {
  const url = process.argv[2];
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.goto(url, { waitUntil: "networkidle" });
  await page.fill("#email", "reader@example.com");
  await page.click("#submit-button");
  await page.waitForFunction(() => document.querySelector("#status").textContent.includes("Saved"), null, {
    timeout: 3000,
  });
  const status = await page.textContent("#status");
  await browser.close();
  if (status.trim() !== "Saved reader@example.com") {
    throw new Error(`Unexpected status: ${status}`);
  }
}

main().catch((error) => {
  console.error(error.message || String(error));
  process.exit(1);
});
