// Rendert die Motion Graphic Frame für Frame zu MP4.
//   node render.mjs                     -> alle Formate
//   node render.mjs --format=feed       -> nur 4:5
//   node render.mjs --stills=1,4.5,10   -> nur Standbilder (Sekunden) als PNG
//   node render.mjs --preview           -> Vorschau-Server im Browser
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import ffmpegPath from 'ffmpeg-static';

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const FPS = 30;
const FORMATS = {
  feed: { width: 1080, height: 1350 },  // 4:5  LinkedIn / Instagram Feed
  story: { width: 1080, height: 1920 }, // 9:16 Reels / Stories / Shorts
};

const args = Object.fromEntries(
  process.argv.slice(2).map((a) => {
    const [k, v] = a.replace(/^--/, '').split('=');
    return [k, v ?? true];
  })
);

const MIME = {
  '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.svg': 'image/svg+xml', '.ttf': 'font/ttf',
};

function serve(port = 0) {
  const server = http.createServer((req, res) => {
    const url = decodeURIComponent(new URL(req.url, 'http://x').pathname);
    const file = path.join(ROOT, url === '/' ? 'index.html' : url);
    if (!file.startsWith(ROOT) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
      res.writeHead(404).end();
      return;
    }
    res.writeHead(200, { 'Content-Type': MIME[path.extname(file)] ?? 'application/octet-stream' });
    fs.createReadStream(file).pipe(res);
  });
  return new Promise((resolve) => server.listen(port, '127.0.0.1', () => resolve(server)));
}

async function openPage(browser, base, name) {
  const { width, height } = FORMATS[name];
  const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });
  page.on('pageerror', (e) => console.error('[page]', e.message));
  await page.goto(`${base}/index.html?render=1&format=${name}`);
  await page.waitForFunction(() => window.__ready === true, null, { timeout: 30000 });
  return page;
}

async function renderVideo(browser, base, name) {
  const page = await openPage(browser, base, name);
  const duration = await page.evaluate(() => window.__duration);
  const frames = Math.round(duration * FPS);
  const out = path.join(ROOT, 'out', `they-consulting-${name}-${FORMATS[name].width}x${FORMATS[name].height}.mp4`);

  const music = path.join(ROOT, 'assets', 'audio', 'music.wav');
  if (!fs.existsSync(music)) throw new Error('assets/audio/music.wav fehlt – zuerst `node music.mjs` ausführen');

  const ff = spawn(ffmpegPath, [
    '-y', '-loglevel', 'error',
    '-f', 'image2pipe', '-framerate', String(FPS), '-c:v', 'png', '-i', '-',
    '-i', music,
    '-map', '0:v', '-map', '1:a', '-t', String(duration),
    // Explizit nach BT.709 wandeln und taggen (Transferkurve sRGB, wie die Quelle). Ohne Tags interpretieren QuickTime/Safari/Social-Apps
    // die Farbmatrix unterschiedlich, und Orange/Mint kippen sichtbar.
    '-vf', 'scale=out_color_matrix=bt709:out_range=tv,format=yuv420p',
    '-color_primaries', 'bt709', '-color_trc', 'iec61966-2-1', '-colorspace', 'bt709', '-color_range', 'tv',
    '-c:v', 'libx264', '-preset', 'slow', '-crf', '16',
    '-profile:v', 'high', '-r', String(FPS),
    '-c:a', 'aac', '-b:a', '192k',
    '-movflags', '+faststart', out,
  ], { stdio: ['pipe', 'inherit', 'inherit'] });
  const done = new Promise((res, rej) => ff.on('close', (c) => (c === 0 ? res() : rej(new Error(`ffmpeg ${c}`)))));

  const t0 = Date.now();
  for (let i = 0; i < frames; i++) {
    await page.evaluate((t) => window.__seek(t), i / FPS);
    const buf = await page.screenshot({ type: 'png' });
    if (!ff.stdin.write(buf)) await new Promise((r) => ff.stdin.once('drain', r));
    if (i % 30 === 0) process.stdout.write(`\r${name}: ${i}/${frames} Frames`);
  }
  ff.stdin.end();
  await done;
  await page.close();
  console.log(`\r${name}: ${frames}/${frames} Frames in ${((Date.now() - t0) / 1000).toFixed(0)}s -> ${path.relative(ROOT, out)}`);
}

async function renderStills(browser, base, name, times) {
  const page = await openPage(browser, base, name);
  fs.mkdirSync(path.join(ROOT, 'out', 'stills'), { recursive: true });
  for (const t of times) {
    await page.evaluate((s) => window.__seek(s), t);
    const file = path.join(ROOT, 'out', 'stills', `${name}-${t.toFixed(2).padStart(5, '0')}s.png`);
    await page.screenshot({ path: file });
    console.log(path.relative(ROOT, file));
  }
  await page.close();
}

if (args.preview) {
  const server = await serve(Number(args.port ?? 5173));
  console.log(`Vorschau: http://127.0.0.1:${server.address().port}/  (?format=story für 9:16)`);
} else {
  fs.mkdirSync(path.join(ROOT, 'out'), { recursive: true });
  const server = await serve();
  const base = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch();
  const names = args.format ? String(args.format).split(',') : Object.keys(FORMATS);
  try {
    for (const name of names) {
      if (!FORMATS[name]) throw new Error(`Unbekanntes Format: ${name}`);
      if (args.stills) await renderStills(browser, base, name, String(args.stills).split(',').map(Number));
      else await renderVideo(browser, base, name);
    }
  } finally {
    await browser.close();
    server.close();
  }
}
