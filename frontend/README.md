# CodeWise V2 frontend

React and Vite interface for the authenticated Django API. It includes session login/logout, workspace discovery, repository submission, synchronous execution status, agent findings, and approval decision flows. The backend remains authoritative for permissions and approval eligibility.

## Local development

Use Node.js 20.19+ or 22.12+ and npm. From this directory:

```powershell
npm install
npm run dev
```

Start Django from `backend/` separately. Vite proxies `/api/` to `http://127.0.0.1:8000` for development. Production is intended to serve the frontend and `/api/` on the same HTTPS origin; this repository does not configure CORS, production static hosting, or an ingress. Leave `VITE_API_BASE_URL` unset for same-origin use. Never put secrets in `VITE_*` variables.

Login uses accounts provisioned through the backend/admin. Account creation, password reset, and workspace creation are not provided by this UI. Workspace selection is based on the authenticated user's memberships.

## Scripts

```powershell
npm test
npm run lint
npm run build
```

The test suite mocks the backend and is not browser E2E. Production readiness boundaries and the browser validation plan are documented in [the deployment guide](../docs/production-readiness.md).
