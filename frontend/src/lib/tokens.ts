// Bridge from the CSS design tokens to JS, for libraries that need a color
// STRING rather than a class (e.g. Recharts `fill`). This is the ONLY place
// the app reads token values in JS — components import from here, never write
// a `var(--…)` string or a hex inline.

// Reference a token by its CSS custom-property name. Returns the `var()`
// expression, which is valid anywhere a CSS color string is accepted
// (Recharts fills, SVG attributes) and stays live under theme changes.
export function token(name: string): string {
  return `var(--${name})`
}

// Categorical chart palette, named by product line. Add to this list rather
// than hardcoding a color at a chart call site.
export const chartColors = {
  auto: token('chart-auto'),
  home: token('chart-home'),
  travel: token('chart-travel'),
  series: [
    token('chart-1'),
    token('chart-2'),
    token('chart-3'),
    token('chart-4'),
    token('chart-5'),
  ],
} as const
