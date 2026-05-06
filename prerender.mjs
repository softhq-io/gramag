import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';

const base = 'https://mingify-self.vercel.app';
const outDir = '/Users/piotrzwolinski/projects/gramag/mingify-static';

const routes = [
  '/',
  '/agent-experience',
  '/hybrid-organisation',
  '/work',
  '/insights',
  '/about',
  '/score',
  '/contact',
  '/careers',
  '/impressum',
  '/datenschutz',
  '/privacy',
  '/intent-graph',
  '/insights/we-fired-an-ai-agent',
  '/insights/agents-are-the-new-colleagues',
];

const browser = await chromium.launch();
const ctx = await browser.newContext();
const page = await ctx.newPage();

for (const r of routes) {
  try {
    await page.goto(base + r, { waitUntil: 'networkidle', timeout: 30000 });
    await page.waitForTimeout(1500);
    const html = await page.content();
    const rel = r === '/' ? 'index.html' : r.replace(/^\//, '') + '.html';
    const full = path.join(outDir, rel);
    fs.mkdirSync(path.dirname(full), { recursive: true });
    fs.writeFileSync(full, html);
    console.log('OK', rel, html.length);
  } catch (e) {
    console.log('FAIL', r, e.message);
  }
}

await browser.close();
