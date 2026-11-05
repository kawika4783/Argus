# Argus zero-additional-cost AI

Argus defaults to **Zero Additional Cost Mode**. It does not require an OpenAI API key, a GPU, another subscription, or a cloud AI service.

## Provider order

1. Deterministic analysis: case statistics, trends, degree analysis, anomaly checks, Monte Carlo simulation, and regression.
2. Ollama, when an administrator configures a reachable local endpoint and selects an installed model.
3. Official ChatGPT plan usage only if OpenAI approves and enables the integration for this deployment type.
4. Other explicitly configured providers.
5. OpenAI API only when the administrator disables Zero Additional Cost Mode, enables it in the server environment, supplies a key, and configures a positive spending limit.

The router never silently changes from a free provider to a paid provider.

## ChatGPT subscription limitation

OpenAI documents Sign in with ChatGPT and ChatGPT plan usage as separate, consent-based capabilities. The official open-source flow issues OAuth credentials for eligible Responses API requests; it does not expose ChatGPT conversations or API keys. The current documentation describes plan usage for open-source/local clients and selected private apps. A remotely hosted Argus VPS deployment must not claim eligibility without approval.

References:

- [Sign in with ChatGPT quickstart](https://developers.openai.com/siwc/quickstart)
- [Open-source ChatGPT plan usage guide](https://developers.openai.com/cookbook/articles/sign-in-with-chatgpt)
- [ChatGPT plan usage error handling](https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery)

Argus therefore reports `unavailable_remote_hosted_approval_required` and continues with deterministic analysis and Ollama. It does not scrape ChatGPT, reuse browser cookies, or call unofficial endpoints.

## Ollama

The base Compose stack does not start Ollama. To opt in, use `compose.ai.yaml` with the existing stack. The file adds only the Ollama service and does not pull a model. An administrator must explicitly install a model and select it in Argus.

```bash
docker compose -f compose.yaml -f compose.ai.yaml up -d --build
docker compose -f compose.yaml -f compose.ai.yaml exec ollama ollama pull <approved-model>
```

The API exposes model discovery and health through the admin AI status endpoints. The endpoint and selected model are never returned with credentials.

For a hosted Ollama endpoint, store its key only as `NEXUSINTEL_OLLAMA_API_KEY` in the VPS environment or an external secret manager. Argus sends it server-side as an Authorization header and never returns or commits it.

## Verification endpoints

- `GET /ai/status` — provider availability, active provider, usage, and cost mode.
- `POST /ai/question` — authorized question/analysis request with provider and evidence labels.
- `POST /ai/question/stream` — server-sent event response stream.
- `POST /analysis/cases/{case_id}` — deterministic analysis without an LLM.
- `GET /admin/ai/usage` — non-secret usage events and estimated costs.
