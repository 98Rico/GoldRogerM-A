# NEXT STEPS — GOLD ROGER

---

## ✅ PHASES 1–4 COMPLÉTÉES (Avril 2025)

### Récap de ce qui a été livré

| Phase | Item | Fichier(s) | Statut |
|-------|------|-----------|--------|
| 1 | Fetcher yfinance (données vérifiées) | `data/fetcher.py` | ✅ |
| 1 | Tables multiples sectorielles (20+ secteurs) | `data/sector_multiples.py` | ✅ |
| 1 | WACC CAPM (β réel, Rf=4.5%, ERP=5.5%) | `finance/core/wacc.py` | ✅ |
| 1 | DCF corrigé (NWC incrémental + D&A tax shield) | `finance/valuation/dcf.py` | ✅ |
| 1 | ValuationService orchestrateur (BUY/HOLD/SELL, sensibilité) | `finance/core/valuation_service.py` | ✅ |
| 1 | Orchestrateur equity avec fetch real data step 0 | `orchestrator.py` | ✅ |
| 2 | Estimations forward analystes (consensus 1 an) | `data/fetcher.py` | ✅ |
| 2 | Path P/E + P/B pour secteur financier (détection auto) | `sector_multiples.py` + `valuation_service.py` | ✅ |
| 2 | LBO engine déterministe (leverage, FCF sweep, IRR, MOIC) | `finance/valuation/lbo.py` | ✅ |
| 2 | SOTP framework (valorisation multi-segments) | `finance/valuation/sotp.py` | ✅ |
| 3 | `run_ma_analysis()` — pipeline M&A complet | `orchestrator.py` | ✅ |
| 3 | `run_pipeline()` — pipeline acquisitions automatique | `orchestrator.py` | ✅ |
| 3 | IC Scoring institutionnel (6 dimensions, gates durs) | `ma/scoring.py` | ✅ |
| 4 | Cache TTL (1h yfinance, 24h ticker) | `utils/cache.py` | ✅ |
| 4 | Logging structuré JSON-lines | `utils/logger.py` | ✅ |

### Smoke test résultats (Avril 2025)

| Test | Résultat | Attendu |
|------|----------|---------|
| AAPL CAPM WACC | 10.6% (β=1.11) | ✓ |
| AAPL LBO | IRR 10.4% → INFEASIBLE | ✓ (trop cher pour LBO standard) |
| AAPL SOTP (3 segments) | Equity $3.27T vs MCap $3.9T | ✓ (−15% holdco discount) |
| JPM path | pe_pb (détection auto) | ✓ |
| JPM EV blended | $318B vs $66B avant | ✓ (amélioration majeure) |
| JPM recommandation | HOLD +4.6% | ✓ (raisonnable) |
| Cache | hit au 2ème appel | ✓ |
| Logger | JSON-lines flushé | ✓ |

---

## 🔴 PRIORITÉ IMMÉDIATE — Fix banques/financières

**Problème** : Pour les banques (JPM, GS, BNP...), le DCF calcule une EV très basse car l'EBITDA margin yfinance renvoie ~0% (les banques n'ont pas d'EBITDA au sens traditionnel). Avec la pondération actuelle 50% DCF, ce zéro tire fortement le blended vers le bas.

**Fix à faire** :
```python
# Dans valuation_service.py, _run_full_valuation()
if use_financial_path:
    weights = {"dcf": 0.0, "comps": 0.60, "transactions": 0.40}
else:
    weights = assumptions.get("weights") or {"dcf": 0.5, "comps": 0.3, "transactions": 0.2}
```

**Fichier** : [finance/core/valuation_service.py](goldroger/finance/core/valuation_service.py) — ligne ~148

---

## 🟡 PRIORITÉ 2 — Robustesse engine

### 2.1 Tests unitaires valuation engine

**Aucun test actuellement.** Un changement de formule peut casser silencieusement.

À créer : `tests/test_dcf.py`, `tests/test_lbo.py`, `tests/test_wacc.py`

Tests minimum :
- DCF : Apple-like input → EV dans range attendu
- WACC : beta 1.0 → 10% WACC (Rf=4.5%, ERP=5.5%, 50/50 equity/debt)
- LBO : 4.5x leverage, 15% rev growth → IRR > 20%
- SOTP : 2 segments → gross_ev = sum des EVs individuels
- Financials path : JPM-like → path = "pe_pb"

### 2.2 Timeout agents LLM

**Problème** : Si Mistral timeout ou est lent, le CLI bloque sans feedback.

**Fix** :
```python
# Dans base.py
response = self.client.chat.complete(
    model=self.model,
    max_tokens=self.max_tokens,
    messages=messages,
    tools=[WEB_SEARCH_TOOL],
    tool_choice="auto",
    timeout=30,  # 30s max
)
```

### 2.3 Retry JSON invalide

**Problème** : Si l'agent retourne un JSON malformé, le parse_model fallback déclenche silencieusement.

**Fix** : Ajouter un retry avec prompt "STRICT JSON ONLY" si parse_model retourne le fallback.

---

## 🟡 PRIORITÉ 3 — Précision DCF (scénarios)

### 3.1 Bear/Base/Bull cases

**Actuellement** : un seul run DCF (base case).

**À faire** :
- Bear : revenue_growth − 3%, ebitda_margin − 2%, WACC + 1%
- Base : inputs as-is
- Bull : revenue_growth + 3%, ebitda_margin + 1%, WACC − 0.5%

Résultat : football field visuel (Low EV / Base EV / High EV par méthode).

**Fichier** : `finance/core/scenarios.py` — stub existant

### 3.2 IC Scoring — enrichissement scores LLM

**Actuellement** : auto_score_from_valuation() initialise strategy/synergies/integration/risk à 5.0/10 (neutre).

**À faire** : lire les outputs des agents (StrategicFit, DueDiligence) pour dériver des scores plus précis :
- `fit_score = "High"` → strategy = 8-9
- `red_flags: [{"severity": "high"}]` → risk = 2-3
- `integration_complexity = "High"` → integration = 3-4

**Fichier** : `ma/scoring.py` + `orchestrator.py` dans `run_ma_analysis()`

---

## 🟢 PRIORITÉ 4 — Productisation SaaS

### 4.1 Frontend Next.js

Périmètre minimal :
- Search bar → `run_analysis()` → affichage memo + football field
- Export PPT / Excel one-click
- Pipeline M&A view

### 4.2 Multi-user / API keys

- FastAPI avec auth Bearer
- Rate limiting par user

### 4.3 Caching persistent (Redis ou fichier)

Actuellement le cache est in-process (reset à chaque restart).
- Passer à Redis ou fichier JSON avec TTL pour persistance entre runs.

---

## 📋 BACKLOG COMPLET

| Item | Priorité | Notes |
|------|----------|-------|
| Fix DCF weight=0 pour financières | CRITICAL | Quick fix 1 ligne |
| Tests unitaires engine | HIGH | Fiabilité production |
| Timeout agents LLM (30s) | HIGH | Éviter CLI freeze |
| Retry JSON invalide | HIGH | Stabilité |
| Bear/Base/Bull scenarios | MED | `scenarios.py` stub existant |
| IC scores LLM-enrichis | MED | Lire fit_score, red_flags |
| Cache persistent (Redis/fichier) | MED | Inter-session |
| Graphique football field | MED | Pour export Excel |
| Bloomberg / Capital IQ | FUTURE | Tier 1 si credentials |
| SEC / EDGAR filings | FUTURE | 10-K/10-Q parsing |
| Frontend Next.js | FUTURE | SaaS UI |

---

## ⚠️ RÈGLE NON NÉGOCIABLE

**LLM = analyse qualitative uniquement.**

Le LLM ne produit jamais :
- chiffres de revenus, marges, WACC, EV

Ces chiffres viennent exclusivement de :
1. `yfinance` (verified) — toujours prioritaire
2. Assumptions LLM (estimated) — fallback sociétés privées
3. Defaults sectoriels (inferred) — dernier recours
