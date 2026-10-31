# Pre-LLM decision model

## Purpose

The NSE research pipeline uses a transparent, hand-authored decision tree after quantitative scoring and portfolio context, but before the LLM review. The gate checks data suitability and consistency. It is not a trained machine-learning model and does not predict returns.

## Decisions

- **PASS:** eligible for LLM review and suggested position sizing.
- **HOLD:** retained for monitoring, but not sent to the LLM and receives no suggested allocation.
- **REJECT:** excluded from LLM review and position sizing.

## Initial rules

1. Reject AVOID signals and records with a market-data error or no usable score.
2. Hold records with missing/invalid dates or market data older than 7 calendar days.
3. Require at least 50 observations for short-term, 100 for mid-term, and 200 for long-term research.
4. Require data confidence of at least 70%.
5. For short-term candidates, hold RSI above 85 or price extension above 20% over SMA20.
6. Only PASS candidates with SETUP, ACCUMULATE or QUALITY_CANDIDATE signals receive suggested allocation.
7. Only PASS candidates are sent to the LLM.

Each decision is persisted with the model version, input features and human-readable reasons.

## Why this is rule-based first

A trained decision tree needs labelled examples with forward outcomes and an explicit target, such as risk-adjusted return over a fixed holding period or whether a setup hit its stop before its target. The current analysis JSON contains features and generated signals, but it does not establish those outcome labels. Training a classifier on generated signals would mostly teach it to reproduce the existing scoring rules.

The rule-based gate is therefore the safer first verification layer. Once historical outcomes are captured without look-ahead bias, a trained model can be evaluated using walk-forward validation and compared with this baseline.

## Calibration

The initial thresholds are guardrails, not proven trading parameters. Evaluate them with walk-forward backtests, transaction costs, slippage, survivorship-bias controls and separate stock/ETF cohorts before changing them or allowing the model to influence live orders.
