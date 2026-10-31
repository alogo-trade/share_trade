# Configurable pre-LLM decision model

## Purpose and safety boundary

The NSE research pipeline applies a configurable deterministic rules gate after quantitative scoring and portfolio context, before any generative LLM review. It checks data suitability and signal consistency. It does not predict returns or recommend trades.

The rules gate is always active and is authoritative for basic data safety. Jev is only called for candidates that pass the deterministic gate. Jev cannot promote a rules-based HOLD or REJECT candidate.

## Provider modes

Configure `DECISION_PROVIDER` as one of:

- `RULES` (default): deterministic rules only. No Jev network call.
- `JEV`: deterministic hard gate followed by Jev classification. Jev PASS is required for LLM escalation; REVIEW or an uncertain answer becomes HOLD; a sufficiently confident REJECT becomes REJECT.
- `HYBRID`: the same hard gate and Jev classification, but with stricter default PASS probability and confidence thresholds (0.90 rather than 0.80). This is a higher-consensus mode, not a second independent predictive model.

Set `DECISION_SHADOW_MODE=true` with JEV or HYBRID to call Jev and record its decisions without changing the actual deterministic verdicts or LLM eligibility. This is the recommended initial evaluation mode.

## OpenRouter Jev

Jev uses OpenRouter's Decisions API, not the chat-completions endpoint:

- Endpoint: `POST https://openrouter.ai/api/alpha/decisions`
- Default model: `typesafe/jev-1.14`
- Credential: `OPENROUTER_API_KEY`

The request sends a bounded set of quantitative candidate fields in `state` and a typed `choice` question. The choice criteria are an object mapping PASS, REVIEW and REJECT to descriptions. Requests are batched at no more than 20 candidates each. The response includes a selected choice, per-choice probabilities and a confidence value. Jev does not return a natural-language rationale. The application records a policy description, not a model-generated explanation.

If the API key is missing, the request fails, or the answer is malformed, active Jev mode fails closed to HOLD. In shadow mode the error is recorded but does not affect rule-based decisions.

Jev confidence is not investment accuracy. Calibrate thresholds on labelled historical outcomes and replay them with walk-forward validation before enabling active mode. This model is a research-escalation verifier, not a return predictor or trade signal.

## Environment configuration

| Variable | Default | Meaning |
|---|---:|---|
| `DECISION_PROVIDER` | `RULES` | RULES, JEV or HYBRID |
| `DECISION_SHADOW_MODE` | `true` | Observe Jev without changing decisions |
| `OPENROUTER_API_KEY` | unset | OpenRouter credential; never store in dashboard JSON |
| `JEV_MODEL` | `typesafe/jev-1.14` | Pinned Jev model |
| `JEV_TIMEOUT_SECONDS` | `25` | HTTP timeout |
| `JEV_PASS_PROBABILITY_THRESHOLD` | `0.80` | Minimum Jev PASS probability |
| `JEV_REJECT_PROBABILITY_THRESHOLD` | `0.80` | Minimum Jev REJECT probability |
| `JEV_CONFIDENCE_THRESHOLD` | `0.80` | Minimum Jev confidence |
| `HYBRID_PASS_PROBABILITY_THRESHOLD` | `0.90` | Stricter HYBRID PASS probability |
| `HYBRID_CONFIDENCE_THRESHOLD` | `0.90` | Stricter HYBRID confidence |
| `DECISION_MAX_DATA_AGE_DAYS` | `7` | Maximum market-data age |
| `DECISION_MIN_DATA_CONFIDENCE` | `70` | Minimum data confidence, percent |
| `DECISION_MIN_OBSERVATIONS_SHORT` | `50` | Short-term history minimum |
| `DECISION_MIN_OBSERVATIONS_MID` | `100` | Mid-term history minimum |
| `DECISION_MIN_OBSERVATIONS_LONG` | `200` | Long-term history minimum |
| `DECISION_MAX_RSI_SHORT` | `85` | Short-term RSI upper guardrail |
| `DECISION_MAX_DISTANCE_SMA20_SHORT_PCT` | `20` | Short-term extension guardrail, percent |

The Streamlit sidebar exposes these settings and persists non-secret configuration to `backend_state/decision_config.json`. Environment variables provide defaults. The API key remains in the process environment or deployment secret store.

## Auditability

Each research run stores:
- selected provider, active/shadow mode and a configuration snapshot;
- deterministic rule thresholds and per-candidate rule decisions;
- Jev model identifier, request ID, usage, selected choice, probabilities and confidence when available;
- final verdict and LLM eligibility.

The US research pipeline is not wired to Jev because its feature schema is separate and has not yet been normalized against the NSE candidate schema.

## Calibration process

1. Run RULES as the production baseline.
2. Enable JEV in shadow mode and collect decisions without affecting eligibility.
3. Label historical candidates using a fixed outcome definition and horizon. Avoid labels derived from the existing signal itself.
4. Compare Jev against rules using precision, recall, false-reject rate, calibration and incremental LLM-review volume.
5. Use time-ordered walk-forward evaluation, transaction costs and separate stock/ETF cohorts.
6. Only then consider active JEV or HYBRID mode. Do not use Jev output to place orders.
