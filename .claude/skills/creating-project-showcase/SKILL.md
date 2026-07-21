---
name: creating-project-showcase
description: Use when a mini-app or feature in this repo is finished, live-verified, and ready to be published as a promotional one-page HTML case study on the project's GitHub Pages wall (docs/).
---

# Creating a Project Showcase Page

## Overview

This repo hosts a growing collection of small apps (Olist Copilot, and future ones
like the IAM Chatbot and ML Pipelines app). Each finished one gets a self-contained
HTML case-study page under `docs/<project-slug>/index.html`, linked from the hub at
`docs/index.html`, served via GitHub Pages.

## Reference example

`docs/olist-copilot/index.html` is a complete, real page — copy its structure for
the next project rather than starting from a blank file. It has:
- A two-column hero: headline + pitch on one side, a real screenshot on the other
- A stat strip in monospace tabular numbers (real figures, not placeholders)
- A feature list in the app's actual nav order (not a generic icon-card grid)
- An architecture pipeline built from flex boxes + arrows (no Mermaid/CDN — see below)
- A filmstrip of real screenshots with captions
- A tech-stack tag list and footer linking back to `docs/index.html` and the GitHub repo

## Workflow

1. Pull real assets first: screenshots from `apex/screenshots/` (or wherever the
   feature's agent saved them), real stats from the app itself — never placeholder
   numbers or lorem text.
2. Copy `docs/olist-copilot/index.html`, adapt every section's content and the
   `assets/` images for the new project. Keep the token-based light/dark CSS
   pattern (`:root` custom properties, overridden under `prefers-color-scheme` and
   `data-theme`) — don't hardcode colors inline.
3. Everything stays self-contained: inline CSS, system font stacks (no external
   font/script CDN). GitHub Pages has no CSP that would block a CDN, but keeping it
   dependency-free means the exact same file also previews cleanly as a Claude
   Artifact before it's committed.
4. Add a card for the new project to `docs/index.html`'s grid, replacing one of the
   `.card.placeholder` entries.
5. Preview via the Artifact tool before committing (point it at the real file path
   in `docs/`, not a copy) — catch layout/content issues while it's still cheap to fix.
6. GitHub Pages itself only needs enabling once per repo (Settings → Pages → source
   = `main` branch, `/docs` folder) — a repo-settings change, so confirm with the
   user before doing it rather than assuming it's already on.