# CodeWise V2

CodeWise V2 is a React and Django application for workspace-scoped repository analysis with eight agents, reviewable patch proposals, and explicit human approval. The backend owns workflow execution, authorization, and approval decisions; frontend readiness does not grant permission to apply or publish changes.

See [backend setup](backend/README.md), [frontend setup](frontend/README.md), and the [production readiness and deployment boundary](docs/production-readiness.md). The application is not production ready: deployment infrastructure, a production workflow runtime, durable background execution/checkpoint recovery, and operational monitoring remain outstanding.
