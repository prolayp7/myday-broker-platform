/**
 * Renders the app icons a home-screen install needs, from the same favicon.svg
 * the browser tab uses, so there is one source of truth for the mark.
 *
 * Run with `npm run icons` after changing favicon.svg. The PNGs are committed
 * rather than built on deploy: they change about never, and a missing icon is
 * invisible until someone installs the app and finds a grey square.
 *
 * WHY PNG AT ALL. iOS ignores an SVG in apple-touch-icon and falls back to a
 * screenshot of the page. PNG is the only format it will use.
 *
 * WHY OPAQUE. iOS composites the icon onto white and applies its own rounded
 * mask. A transparent source therefore shows as a white square with the mark
 * floating in it, and any corner rounding of our own gets clipped twice. Flat
 * background, no rounding, let the OS do the masking.
 *
 * The 20% padding is Apple's own guidance for how much room a mark needs
 * inside the icon grid — the bolt drawn edge to edge reads as cramped next to
 * native icons.
 */
import { readFileSync, mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import sharp from 'sharp'

const here = dirname(fileURLToPath(import.meta.url))
const publicDir = resolve(here, '..', 'public')
const source = readFileSync(resolve(publicDir, 'favicon.svg'))

// Matches --background in light mode (oklch(0.973 0.002 247)). The icon sits
// on a home screen, not on our page, so it cannot inherit the app's theme —
// and a dark-mode-tinted icon would look wrong on a light wallpaper anyway.
const BACKGROUND = '#f7f8fa'

// 192/512 are the manifest's required pair (small in-app surfaces, and the
// splash/store-quality source). 180 is what iOS asks for by name.
const SIZES = [192, 512, 180]

mkdirSync(publicDir, { recursive: true })

for (const size of SIZES) {
  const inner = Math.round(size * 0.6) // 20% padding on each side
  const bolt = await sharp(source, { density: 384 })
    .resize(inner, inner, { fit: 'contain', background: { r: 0, g: 0, b: 0, alpha: 0 } })
    .png()
    .toBuffer()

  await sharp({
    create: {
      width: size,
      height: size,
      channels: 4,
      background: BACKGROUND,
    },
  })
    .composite([{ input: bolt, gravity: 'centre' }])
    .png()
    .toFile(resolve(publicDir, `icon-${size}.png`))

  console.log(`icon-${size}.png`)
}
