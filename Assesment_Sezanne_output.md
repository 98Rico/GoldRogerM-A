# Valuation Tool Accuracy Review — Sézane Case Study

## Executive Summary

The tool is directionally useful, but it is **not yet investment-grade accurate**.

For a private company like Sézane, the current system behaves more like a **smart investment memo generator** than a true valuation engine.

### Estimated Quality Score

- **Narrative quality:** 7.5/10
- **Comparable selection:** 6.5/10
- **Financial accuracy:** 3/10
- **Actual valuation reliability:** 2–4/10

The tool is strong for first-pass screening and IC memo drafting, but it should not be trusted for final investment decisions without significant analyst review.

---

# What Works Well

## 1. Peer Selection Is Sensible (But Imperfect)

Current peers:

- LVMH
- Kering
- Hermès
- Burberry
- Richemont

These make sense for “premium fashion,” but they are too large and too luxury-heavy compared to Sézane.

### Problem

This causes:

- inflated valuation multiples
- overly optimistic transaction comps
- distorted football field outputs

### Better Peer Set

A better peer group would include:

- Sandro / SMCP
- Maje
- Reformation
- Ganni
- APC
- Ba&sh
- Theory
- Vince

These are much closer to the DTC premium / accessible luxury segment.

### Impact

This is likely the single biggest issue affecting comps accuracy.

---

## 2. Good Transparency on Missing Data

Example:

> EBITDA margin not found — using sector default 20%

This is excellent.

Most tools hide this.

Explicitly surfacing fallback assumptions improves trust and usability.

---

## 3. LBO Infeasible Flag Is Strong

Output:

- IRR: 10.9%
- MOIC: 1.7x
- Entry leverage: 4.5x

This is realistic and useful.

It prevents “everything is buyable” bias and improves credibility.

This part feels strong.

---

# Where Accuracy Breaks

## 1. Massive Revenue Contradiction

### Table

Revenue = **250M**

### Investment Thesis

Revenue = **€350M in 2023**

This is a major red flag.

### Root Cause

The system is mixing:

- generated assumptions
- fallback estimates
- LLM narrative hallucinations

without reconciliation.

### Why This Matters

A valuation engine must have:

# ONE source of truth

—not three competing versions.

This alone makes the output unreliable.

---

## 2. DCF Is Likely Broken

### Reported Valuation

- DCF Mid: 444M
- Trading Comps Mid: 1.45B
- Transaction Comps Mid: 4.4B

This spread is far too large.

### Likely Causes

One or more of the following are wrong:

- WACC
- EBITDA assumptions
- terminal assumptions
- financial base inputs

Possibly all of them.

### Recommendation

For private consumer businesses:

**DCF should be a sanity check, not the anchor.**

Currently it looks structurally unreliable.

---

## 3. Transaction Comps Are Clearly Overstated

### Output

Transaction comps = 4.4B

This is very unlikely unless:

- revenue > €500M
- EBITDA margins > 25%
- strategic buyer premium exists

### Likely Problem

The tool is probably using:

- public luxury M&A comps
- giant luxury transactions

instead of relevant founder-led DTC brands.

This creates severe valuation inflation.

---

## 4. “HOLD” Is the Wrong Recommendation Framework

For private companies, output should be:

- Attractive / Neutral / Expensive
- Invest / Pass
- Sponsorable / Non-sponsorable

—not:

# HOLD

That is a public equity framework leaking into private markets.

It weakens credibility immediately.

---

## 5. Many Qualitative Metrics Look Hallucinated

Examples:

- repeat purchase rate = 40%
- AOV = €180
- 95% eco-friendly materials
- U.S. = 40% of sales

These are plausible, but likely LLM-generated unless sourced.

### Problem

Plausible ≠ true.

This is dangerous in investment work.

---

# Biggest Recommendation

# Separate Facts vs Assumptions vs Narrative

Right now they are blended together.

This is the core architectural issue.

---

## Recommended Structure

### 1. Hard Data Layer

Must be sourced and verified:

- Revenue
- EBITDA
- growth
- funding rounds
- employee count
- store count

↓

### 2. Assumption Layer

Can be modeled:

- EBITDA margin estimates
- WACC
- exit multiple
- growth assumptions

↓

### 3. Narrative Layer

Can be generated:

- investment thesis
- bull / base / bear
- IC memo
- recommendation framing

---

## Critical Rule

# Never let narrative invent facts

This is the single most important improvement.

---

# Immediate Improvements to Implement

## 1. Add Confidence Scores Per Metric

Example:

| Metric | Value | Confidence |
|---|---:|---|
| Revenue | €350M | High |
| EBITDA Margin | 18% | Medium |
| U.S. Sales Mix | 40% | Low |
| Repeat Purchase Rate | 40% | Low |

This would massively improve trust.

---

## 2. Force Reconciliation Checks

Reject output if:

- Revenue differs across sections
- EBITDA differs across sections
- valuation methods differ by >5x without explanation

This would eliminate many bad outputs.

---

## 3. Use Smaller-Company Private Comps

This will improve valuation accuracy more than almost anything else.

Especially for DTC consumer brands.

---

# Final Verdict

Today the tool is:

# Excellent for generating an IC memo draft

but

# Not reliable enough for investment decisions

---

# Safe Use Cases

Good for:

- first-pass screening
- analyst brainstorming
- preparing investment notes
- IC memo drafting
- identifying diligence questions

---

# Unsafe Use Cases

Do NOT trust it for:

- submitting final IC recommendations
- making offers
- setting valuation expectations
- investor negotiations
- final pricing decisions

without manual analyst correction.

---

# Realistic Value

A strong human analyst could move this from:

### 30% useful

to

### 80% useful

with:

- corrected peers
- real financials
- sourced assumptions
- rebuilt DCF

That means the tool is valuable.

It just should not be treated as the final answer.

---

# True Core Problem

The biggest issue is not valuation math.

It is:

# Bad source control of facts

That is the real problem to solve first.
