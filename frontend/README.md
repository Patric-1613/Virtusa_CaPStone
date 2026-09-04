# AI Daily Digest frontend

React, TypeScript and Vite frontend foundation for AI Daily Digest. It currently renders an illustrative, data-free single-page shell and does not call the backend yet.

## Requirements

- Node.js 22.13 or newer
- npm 11 or newer

## Local setup

From the repository root:

```bash
cd frontend
cp .env.example .env.local
npm ci
npm run dev
```

Open <http://localhost:3000>. The committed `package-lock.json` provides reproducible installs.

## Public configuration

`VITE_API_BASE_URL` is the public base URL reserved for the FastAPI service. Local development defaults to `http://localhost:8000`; a deployed frontend should receive the deployed API origin instead. The current shell deliberately does not make network requests.

Only variables prefixed with `VITE_` may be exposed to frontend code. Do not place API keys, Render credentials, Resend keys or any other secrets in a `VITE_` variable or commit them to the repository.

## Checks

```bash
npm run lint
npm run typecheck
npm test
npm run build
```

`npm test` runs the component tests. `npm run check` runs lint, type checking, tests and a production build together.

Frontend CI is defined separately in `.github/workflows/frontend-ci.yml` and only runs when frontend files or that workflow change.
