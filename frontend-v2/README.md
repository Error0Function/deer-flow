# DeerFlow Frontend v2

`frontend-v2` is the parallel Solid-based workspace rebuild.

It exists to let us replace the current chat workspace incrementally instead of
trying to rewrite the legacy Next.js frontend in place.

## Current Goal

Phase 1 focuses on the smallest useful surface:

- faster shell and route transitions
- recent thread list with progressive loading
- a basic chat timeline
- message submission against the existing DeerFlow backend
- explicit room for future background runs, branching, and multi-account work

The legacy frontend remains available at `/`.

The new Solid frontend now has two nginx-backed entrypoints:

- `/v2/` for the stable release-like build
- `/v2-dev/` for the hot-reloading development build

## Local Development

```bash
pnpm install
pnpm dev
```

By default the dev server serves from `http://localhost:5173/v2-dev/`.

When DeerFlow is running through the existing nginx reverse proxy, use
`http://localhost:2026/v2-dev/` for live edits or `http://localhost:2026/v2/`
for the stable build that mirrors production routing more closely.

## Scripts

```bash
pnpm dev
pnpm build
pnpm preview
```

## Environment

Optional variables:

```bash
VITE_APP_BASE_PATH=/v2-dev/
VITE_BACKEND_BASE_URL=
VITE_LANGGRAPH_BASE_URL=
```

Leave them empty to use the nginx proxy paths.

## Current Tradeoffs

- The first milestone intentionally keeps the feature surface small.
- The UI is new, but the backend contracts are still the same DeerFlow APIs.
- Message rendering is intentionally simple right now; richer rendering can come
  back once the core flow is stable.

## Deferred Design Notes

These discussion points are intentionally recorded before the visual polish
phase starts, so feature work can continue without forgetting the design
questions that still need a deliberate answer.

- Keep Phase 1 focused on core workspace behavior first:
  - thread loading
  - thread switching
  - message submission
  - run status recovery
  - long-conversation handling
  - tool-call visibility
- Do not treat the current v2 appearance as the intended final art direction.
- Before visual polish, align on the product shape of the workspace:
  - should it feel like a research workbench, a chat IDE, or a more productized messenger
  - should the interface favor dense information or a roomier layout
  - should tool activity be prominent by default or collapsed until needed
- Visual styling can follow after the core interaction model is stable, but the
  information architecture should be discussed before deeper component growth.
