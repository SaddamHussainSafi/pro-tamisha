# Brand verification checklist — pending

Website: https://tamishatrading.com/

The official site was inspected for brand identity, company context, logo files and product taxonomy. Direct CSS extraction and authorized asset downloads were not possible in the current build environment, so colors in `tokens.json` are **provisional visual approximations of the official logo**.

## Before production
- [ ] Inspect global CSS/WordPress theme styles and record exact primary, secondary, accent and semantic colors.
- [ ] Confirm the two logo URLs in the design guide; download approved high-res variants and place them in `public/brand/`.
- [ ] Check SVG/vector originals if available, transparent vs dark-mode wordmark, clearspace, favicon and minimum display size.
- [ ] Verify image and font licensing; do not bundle third-party fonts/assets without permission.
- [ ] Capture actual website screenshots for style consistency and keep those in development references only.
- [ ] Verify updated colors with WCAG 2.2 AA contrast.
- [ ] Record any palette change and signoff in a design ADR.

**No fictional assets should be generated to represent the official business brand.**
