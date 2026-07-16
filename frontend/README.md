# VERIDICAL frontend

Vite + React + TypeScript (strict) + Tailwind CSS v4.

## Commands

```
npm install
npm run dev        # dev server (Vite default port)
npm run lint       # oxlint
npm test           # vitest (jsdom + testing-library)
npm run build      # tsc -b + vite build
npm run check:css  # post-build guard: token classes survived Tailwind's scan
```

## Design tokens

Every color, font, radius and shadow lives once in `src/tokens.css`
(Tailwind v4 `@theme` — emits both the CSS custom properties and the
utility classes). Tailwind's default color palette is disabled there, so
only token color utilities exist; components must not carry raw values.

Shared primitives (Button, Pill, Tag, Panel, KpiCard, Stepper, Modal) live
in `src/components/` and are the only place tokens turn into markup —
feature code composes primitives. `/gallery` renders every primitive with
all variants for visual review.

Variant classes are written as full literal strings: Tailwind only emits
classes its scanner finds written out whole, so dynamically composed class
names would silently disappear from the build (`npm run check:css` catches
this).
