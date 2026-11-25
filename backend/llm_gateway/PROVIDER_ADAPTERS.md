# Groq and Gemini gateway adapters

The backend gateway provides synchronous Groq Chat Completions and Google Gemini Generate Content adapters. They are registered through `ProviderAdapterRegistry.groq_and_gemini(...)` and continue to run behind `LLMGateway`; callers do not access provider SDKs directly. SDK retries are disabled so retry and fallback decisions stay in the gateway and in the configured pool.

Google Gen AI SDK 3.x defaults Gemini Developer API calls to `v1`. Set `CODEWISE_GEMINI_API_VERSION` to `v1beta` or `v1alpha` only when the reviewed model requires that endpoint; the runtime also accepts explicit `v1`. The runtime does not choose model aliases based on API version.

## Model capability manifest

No provider model IDs or capabilities are bundled in the adapters. Before enabling a route, deployment configuration must supply an operator-verified manifest mapping each exact model ID to the features validated for that model. Example shape (illustrative values only; verify against provider documentation and the selected model):

```python
model_capabilities = {
    "groq": {
        "<verified-groq-model-id>": {
            "structured_output", "system_messages", "max_output_tokens", "temperature", "top_p",
            "seed", "stop_sequences",
        },
    },
    "gemini": {
        "<verified-gemini-model-id>": {
            "structured_output", "system_messages", "max_output_tokens", "temperature", "top_p",
            "seed", "stop_sequences",
        },
    },
}
providers = ProviderAdapterRegistry.groq_and_gemini(
    model_capabilities=model_capabilities,
)
```

Do not copy the illustrative feature set without verifying it for the chosen model and provider API. The manifest is trusted deployment metadata, not runtime discovery. An absent model, missing feature, provider mismatch, or invalid structured-output declaration fails closed before a provider request. Structured responses are parsed and then validated against the gateway request schema. Groq is configured for non-strict JSON Schema mode because its strict mode is limited to selected models and schema forms.

## Credentials and pool boundaries

The pool router resolves credential references on the backend and passes a `ResolvedCredential` only for the selected route. Default SDK clients receive the secret in memory for that single request and SDK retries are set to zero/one attempt. Never put provider keys in frontend configuration or log them. Environment-backed secret references use the existing `CODEWISE_LLM_CREDENTIAL_<NAME>` convention. Keep pool credentials, account identities, quota buckets, and fallback routes separate; the adapters do not transfer credentials or quota between pools.

Pool A remains Agents 2–3, Pool B Agents 4–6, and Pool C Agents 7–8. Agent 1 stays outside gateway access. The registry does not enable providers or paid calls: `GatewayPolicy` and enabled pool configurations remain authoritative and default disabled. Do not enable paid tiers or pools until runtime wiring, model capability verification, quotas, and operational controls are reviewed.

## Runtime wiring still required

The server-side composition root is `config.workflow_runtime.build_api_workflow_runtime`, the default `CODEWISE_API_WORKFLOW_FACTORY`. It loads strict global policy, capability-manifest, and three independent pool configuration environment values, then wires all eight existing agents through the gateway-backed LangChain model. The explicit agent-to-pool mapping binds model requests to their assigned pool, and Agent 1 cannot receive a model. API execution remains synchronous. Durable approval resume uses the opt-in Postgres checkpointer described in [the API runtime guide](../api/README.md); without it, approval-required work fails closed. These adapters do not implement asynchronous jobs, model discovery, or provider health checks.

## SDK pins

`backend/requirements.txt` pins `groq==1.7.0` and `google-genai==3.6.1`. Imports are lazy so offline tests can use fake clients without importing either provider SDK. No live requests are required by the test suite.
