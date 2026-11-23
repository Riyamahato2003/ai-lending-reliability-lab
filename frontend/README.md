# Verity — UI for the AI Lending Reliability Lab

This React 19 + TypeScript application makes the existing synthetic FastAPI workflows and operator-only Reliability Lab explorable in a browser. It does not implement financial decision logic or make external lender submissions.

## Launch

From the repository root, with PostgreSQL and the three demo database roles configured:

```sh
./scripts/start_local_demo.sh
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). FastAPI serves the bundled production frontend. In development, run `npm ci && npm run dev` from this directory; Vite proxies API paths to FastAPI port 8000.

## UI features

- Borrower login, synthetic application creation, explicit versioned lending consent, deterministic evaluation, illustrative INR offer comparison, rule trace and audit timeline.
- Single-use direct approval consumption and direct revocation. Both remain exclusively server-authorized actions.
- Evidence-grounded explanations using the fixed read-only tool registry. Separate optional AI-processing consent; Gemini may select a topic but cannot write financial facts or take action.
- Operator-only safe/unsafe Reliability Lab with matching fault schedules, invariant counts, authoritative confirmations, run history, trial evidence, and bounded local-demo dispatch worker action.
- Responsive mobile navigation and accessible native form controls.

## Verification

```sh
npm ci
npm run build
npm run test:e2e
```

The Playwright suite expects the **separately running local FastAPI server** at http://127.0.0.1:8000 and a dedicated synthetic database. Do not run Playwright against production or customer data.

The operator unsafe baseline is an intentionally deficient simulation. Its measured duplicates do not describe real lender effects. The app is a local engineering portfolio demo, not a production credit service.

For exact acceptance evidence, see [Checkpoint 5 verification](../docs/checkpoint-5-verification.md). For security and deployment limitations, see [release readiness](../docs/release-readiness.md).
