// Wait for a ucu app window to change (and settle) instead of sleeping a fixed time.
// Import it in the js tool straight from this repo:
//
//   const { waitForChange } = await import("<this repo>/js/wait.mjs");
//   const r = await waitForChange(app, () => app.click(8), { timeoutMs: 20000 });
//   // r = { changed, settled, ms, changedBlocks }
//
// Screenshots are compared on a grid of 16-px blocks by mean brightness, so JPEG noise,
// a blinking caret or a clock tick don't count as a change; a new dialog, page or output line does.
import jpeg from "jpeg-js";

const BLOCK = 16;           // block size in screenshot pixels
const STRIDE = 2;           // sample every 2nd pixel in each direction inside a block
const BLOCK_TOLERANCE = 10; // mean-brightness difference (0-255) for a block to count as changed

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function frame(app, region) {
  const bytes = await app.getScreenshot({ emit: false });
  return { bytes, region, blocks: null };
}

function blocksOf(f) {
  if (f.blocks) return f.blocks;
  const { width, height, data } = jpeg.decode(f.bytes, { useTArray: true, formatAsRGBA: false });
  const [x0, y0, w, h] = f.region
    ? [Math.max(0, f.region[0]), Math.max(0, f.region[1]),
       Math.min(f.region[2], width - Math.max(0, f.region[0])), Math.min(f.region[3], height - Math.max(0, f.region[1]))]
    : [0, 0, width, height];
  const cols = Math.max(1, Math.floor(w / BLOCK)), rows = Math.max(1, Math.floor(h / BLOCK));
  const sums = new Float64Array(cols * rows), counts = new Uint32Array(cols * rows);
  for (let y = 0; y < rows * BLOCK; y += STRIDE) {
    const row = Math.floor(y / BLOCK) * cols;
    let i = ((y0 + y) * width + x0) * 3;
    for (let x = 0; x < cols * BLOCK; x += STRIDE, i += 3 * STRIDE) {
      const b = row + Math.floor(x / BLOCK);
      sums[b] += 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
      counts[b]++;
    }
  }
  for (let b = 0; b < sums.length; b++) sums[b] /= counts[b];
  f.blocks = { width, height, means: sums };
  return f.blocks;
}

// Number of blocks whose mean brightness moved by more than BLOCK_TOLERANCE (Infinity if the size changed).
function difference(a, b) {
  if (a.bytes.length === b.bytes.length && a.bytes.every((v, i) => v === b.bytes[i])) return 0;
  const A = blocksOf(a), B = blocksOf(b);
  if (A.width !== B.width || A.height !== B.height) return Infinity;
  let changed = 0;
  for (let i = 0; i < A.means.length; i++) if (Math.abs(A.means[i] - B.means[i]) > BLOCK_TOLERANCE) changed++;
  return changed;
}

/**
 * Take a baseline screenshot, run `action` (optional), then poll until the window differs from
 * the baseline and, unless settleMs is 0, stays unchanged for settleMs. Never throws on timeout.
 *
 * opts.timeoutMs  give up after this long (default 30000)
 * opts.intervalMs pause between polls (default 250)
 * opts.settleMs   how long the screen must stay still after the change (default 800; 0 = return at first change)
 * opts.minBlocks  16-px blocks that must change to count (default 8, about a short line of text;
 *                 a caret blink or clock tick is 1-4, so raise it or use region if noise triggers it)
 * opts.region     [x, y, w, h] in screenshot pixels to watch only part of the window
 */
export async function waitForChange(app, action, opts = {}) {
  const { timeoutMs = 30000, intervalMs = 250, settleMs = 800, minBlocks = 8, region } = opts;
  const start = Date.now();
  const baseline = await frame(app, region);
  if (action) await action();
  let changedBlocks = 0, last = null, stillSince = 0;
  while (Date.now() - start < timeoutMs) {
    const current = await frame(app, region);
    if (!last) {
      changedBlocks = difference(baseline, current);
      if (changedBlocks >= minBlocks) {
        if (settleMs === 0) return { changed: true, settled: false, ms: Date.now() - start, changedBlocks };
        last = current;
        stillSince = Date.now();
      }
    } else if (difference(last, current) >= minBlocks) {
      last = current;
      stillSince = Date.now();
    } else if (Date.now() - stillSince >= settleMs) {
      return { changed: true, settled: true, ms: Date.now() - start, changedBlocks };
    }
    await sleep(intervalMs);
  }
  return { changed: last !== null, settled: false, ms: Date.now() - start, changedBlocks };
}
