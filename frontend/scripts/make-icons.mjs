/**
 * Rasterises the prism mark into the PNGs a home-screen install needs.
 *
 * Run with `npm run icons`. The output is committed: the build must not depend
 * on `sharp` being installable, and these files change roughly never. The
 * script exists so the icons can be regenerated from the mark rather than
 * redrawn by hand.
 *
 * Why PNG at all, when the tab favicon is an inline SVG: iOS ignores SVG for
 * `apple-touch-icon`, and Android's install prompt wants a raster at 192 and
 * 512. A home screen is exactly the surface that will not take the SVG.
 *
 * **The geometry below is a hand-kept copy** of `src/components/Logo.tsx`,
 * which is the source of truth. The third copy lives in `index.html` as the
 * inline favicon. Sharing one source would mean either a build step or a
 * headless browser in the pipeline; three short paths are the cheaper trade,
 * as long as a change to the mark touches all three.
 */
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import sharp from "sharp";

const here = dirname(fileURLToPath(import.meta.url));
const outDir = join(here, "..", "public");

// Dark-theme tokens from `src/styles/tokens.css`. An icon on a home screen has
// no way to react to light mode, so it decides once.
const TILE = "#1f1e1d"; // --bg-sidebar, same as theme_color in the manifest
const INK = "#f0eee6"; // --text: the beam and the glass
const SPECTRUM = ["#5d9fd6", "#a86fc4", "#d9635a", "#e59356", "#f2c879"];
const STOPS = [0, 0.27, 0.52, 0.76, 1];

const PRISM = "M20 6.6 33.4 30.4H6.6Z";
const BEAM = "M2.4 24H18L37 11";

/**
 * One icon as SVG source.
 *
 * `markRatio` is the share of the edge the mark itself takes. `cornerRatio` 0
 * means a full square — that is what iOS wants (it rounds the corner itself
 * and fills transparency with black) and what a maskable icon wants, since
 * there the system decides the shape.
 */
function iconSvg(size, markRatio, cornerRatio) {
  const mark = size * markRatio;
  const scale = mark / 40;
  const origin = (size - mark) / 2;
  const stops = SPECTRUM.map(
    (color, i) => `<stop offset="${STOPS[i]}" stop-color="${color}"/>`,
  ).join("");

  return Buffer.from(
    `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" ` +
      `viewBox="0 0 ${size} ${size}" fill="none">` +
      `<defs><linearGradient id="s" x1="4" y1="34" x2="36" y2="6" ` +
      `gradientUnits="userSpaceOnUse">${stops}</linearGradient></defs>` +
      `<rect width="${size}" height="${size}" rx="${size * cornerRatio}" fill="${TILE}"/>` +
      `<g transform="translate(${origin} ${origin}) scale(${scale})">` +
      // The glass sits at the bottom; the beam goes under the frame on purpose,
      // so the coloured outline — the thing the mark is recognised by — stays
      // unbroken, and the light reads as passing through rather than stuck on.
      `<path d="${PRISM}" fill="${INK}" fill-opacity="0.12"/>` +
      `<path d="${BEAM}" stroke="${INK}" stroke-width="3.3" ` +
      `stroke-linecap="round" stroke-linejoin="round"/>` +
      `<path d="${PRISM}" stroke="url(#s)" stroke-width="4" stroke-linejoin="round"/>` +
      `</g></svg>`,
  );
}

const ICONS = [
  // [file, size, mark share, corner rounding]
  ["icon-192.png", 192, 0.62, 0.22],
  ["icon-512.png", 512, 0.62, 0.22],
  // iOS rounds the corner itself and backs transparent pixels with black.
  ["apple-touch-icon.png", 180, 0.62, 0],
  // Maskable: the system may crop up to 20% of the edge, so the mark has to fit
  // the safe zone — hence noticeably smaller than the rest.
  ["icon-maskable-512.png", 512, 0.46, 0],
];

await mkdir(outDir, { recursive: true });

for (const [name, size, markRatio, cornerRatio] of ICONS) {
  const png = await sharp(iconSvg(size, markRatio, cornerRatio))
    .png({ compressionLevel: 9 })
    .toBuffer();
  await writeFile(join(outDir, name), png);
  console.log(`${name} (${size}x${size}, ${png.length} B)`);
}
