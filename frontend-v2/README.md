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

The new Solid frontend is mounted behind nginx at `/v2/`.

## Local Development

```bash
pnpm install
pnpm dev
```

By default the app serves from `http://localhost:5173/v2/`.

When DeerFlow is running through the existing nginx reverse proxy, use
`http://localhost:2026/v2/` so the Solid app can share the same backend routes
as the legacy frontend.

## Scripts

```bash
pnpm dev
pnpm build
pnpm preview
```

## Environment

Optional variables:

```bash
VITE_BACKEND_BASE_URL=
VITE_LANGGRAPH_BASE_URL=
```

Leave them empty to use the nginx proxy paths.

## Current Tradeoffs

- The first milestone intentionally keeps the feature surface small.
- The UI is new, but the backend contracts are still the same DeerFlow APIs.
- Message rendering is intentionally simple right now; richer rendering can come
  back once the core flow is stable.
