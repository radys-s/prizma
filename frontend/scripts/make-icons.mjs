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
 */
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import sharp from "sharp";

const here = dirname(fileURLToPath(import.meta.url));
const outDir = join(here, "..", "public");

const BG = "#1f1e1d";
const BEAM = "#f5f4ef";
const GLASS = "#8a867c";
const FAN = ["#f2c879", "#e59356", "#d9635a", "#a86fc4", "#5d9fd6"];

/**
 * The mark on a 40x40 grid, scaled and centred into a `size` square.
 *
 * `inset` is the fraction of the square left empty on each side. Android masks
 * a maskable icon down to a circle inscribed in the middle 80%, so anything
 * outside that can be cropped — hence a much larger inset for that one.
 */
function markSvg(size, inset) {
  const box = size * (1 - 2 * inset);
  const scale = box / 40;
  const offset = size * inset;
  // The fan spreads to x=40 and the beam starts at x=1, so the mark fills the
  // grid horizontally; vertically it sits between y=6.5 and y=31.5 and needs
  // nudging down to look centred.
  const dy = (40 - (6.5 + 31.5)) / 2;
  const stroke = 2.4;

  const fan = FAN.map((color, i) => {
    const ends = [
      [40, 10.5],
      [40, 15.5],
      [40, 20.5],
      [40, 25.5],
      [40, 30.5],
    ];
    const [x, y] = ends[i];
    return `<path d="M26.6 20.5 ${x} ${y}" stroke="${color}" stroke-width="${stroke}"/>`;
  }).join("");

  return Buffer.from(
    `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">` +
      `<rect width="${size}" height="${size}" fill="${BG}"/>` +
      `<g transform="translate(${offset} ${offset + dy * scale}) scale(${scale})" fill="none" stroke-linecap="round">` +
      `<path d="M1 19.5H13" stroke="${BEAM}" stroke-width="${stroke}"/>` +
      `<path d="M20 6.5 33.5 31.5H6.5Z" stroke="${GLASS}" stroke-width="${stroke}" stroke-linejoin="round"/>` +
      fan +
      `</g></svg>`
  );
}

const targets = [
  { file: "icon-192.png", size: 192, inset: 0.14 },
  { file: "icon-512.png", size: 512, inset: 0.14 },
  // 26% inset keeps every stroke inside Android's circular safe zone.
  { file: "icon-maskable-512.png", size: 512, inset: 0.26 },
  // iOS does not mask beyond rounding the corners, so it can run tighter.
  { file: "apple-touch-icon.png", size: 180, inset: 0.12 },
];

await mkdir(outDir, { recursive: true });

for (const { file, size, inset } of targets) {
  const png = await sharp(markSvg(size, inset)).png({ compressionLevel: 9 }).toBuffer();
  await writeFile(join(outDir, file), png);
  console.log(`${file}  ${size}x${size}  ${(png.length / 1024).toFixed(1)} kB`);
}
