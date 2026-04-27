# NEXT STEPS — GOLD ROGER (PRODUCTION ROADMAP)

---

## ✅ PHASE 1 COMPLÉTÉE (Avril 2025)

### Ce qui a été livré :

| Problème (avant) | Solution (livrée) | Fichier |
|---|---|---|
| WACC hardcodé à 10% | CAPM : Re = Rf + β × ERP (β réel yfinance) | `finance/core/wacc.py` |
| LLM extrayait les chiffres financiers | yfinance fetch → MarketData vérifié avant tout LLM | `data/fetcher.py` |
| NWC sur niveau de revenu (faux) | NWC incrémental : Δrevenu × nwc_pct | `finance/valuation/dcf.py` |
| Pas de D&A tax shield | FCFF = EBITDA(1-T) + D&A×T - CapEx - ΔNWC | `finance/valuation/dcf.py` |
| Multiples hardcodés (8-12x) | 20+ tables sectorielles calibrées marché | `data/sector_multiples.py` |
| Comps non ancrées au marché | EV/EBITDA live de yfinance ancre la plage | `finance/core/valuation_service.py` |
| Pas de BUY/HOLD/SELL | EV − Net Debt / Shares vs prix actuel → ±15% | `finance/core/valuation_service.py` |
| Pas de traçabilité | Tags `verified` / `estimated` / `inferred` | `finance/core/valuation_service.py` |
| Pas de sensibilité | Matrice 5×5 WACC × terminal growth | `finance/core/valuation_service.py` |
| Orchestrateur dépendait du LLM pour les chiffres | Step 0 : fetch réel avant agents LLM | `orchestrator.py` |

### Résultats du smoke test (live, Avril 2025) :

| Société | WACC CAPM | EV Blended | Reco | Confiance |
|---------|-----------|-----------|------|-----------|
| AAPL | 10.6% (β=1.11) | $2.4T | SELL −39% | verified |
| MSFT | 10.5% (β=1.11) | $1.9T | SELL −40% | verified |

> Note : SELL sur les mega-caps est défendable — le DCF (CAGR historique) capte la prime de franchise. Un analyste PE discuterait les hypothèses de croissance forward pour ajuster.

---

## 🔴 PHASE 2 — PRÉCISION (PRIORITÉ IMMÉDIATE)

### 2.1 Estimations forward analystes

**Problème** : Le DCF utilise le CAGR historique. Pour les sociétés premium, le marché price in des projections forward bien supérieures.

**À faire** :
- Intégrer `yfinance` → `earningsEstimate` / `revenueEstimate` (consensus analystes)
- Si disponible, remplacer CAGR historique par croissance forward sur 2–3 ans, puis normaliser vers terminal growth
- Tagger source : `consensus_estimate`

**Fichier** : `data/fetcher.py` + `finance/core/valuation_service.py`

---

### 2.2 Framework P/E / P/B pour financières

**Problème** : EV/EBITDA ne fonctionne pas pour les banques, assureurs, asset managers. JPM donne des résultats aberrants avec le framework actuel.

**À faire** :
- Détecter secteur `Financials` / `Banking` / `Insurance`
- Switcher vers multiples P/E (forward) et P/B (book value)
- Equity Value = Price × Shares (pas EV − Net Debt pour les banques)

**Fichier** : `finance/core/valuation_service.py`, nouveau fichier `data/sector_multiples.py` (ajouter cas spéciaux)

---

### 2.3 LBO model déterministe

**Problème** : `finance/valuation/lbo.py` est un stub vide. L'agent LBO existe mais produit du texte, pas de calcul.

**À faire** :
- Entry EV = blended EV from valuation engine
- Leverage schedule (debt/EBITDA, standard tranches)
- Debt paydown annuel (FCF sweep)
- Exit EV = EBITDA exit year × exit multiple
- IRR computation sur equity investment
- Filtrer : LBO feasible si IRR > 15% et leverage < 6x EBITDA

**Fichier** : `finance/valuation/lbo.py`

---

### 2.4 SOTP pour conglomérats

**Problème** : `finance/valuation/sotp.py` est vide. Les holdings et conglomérats nécessitent une valorisation par segment.

**À faire** : Implémenter Sum-of-the-Parts avec poids par segment

---

## 🟡 PHASE 3 — DEAL SOURCING INSTITUTIONNEL

> Ne pas commencer avant que la Phase 2 valuation soit stable.

### 3.1 Orchestration M&A manquante

**Problème** : `run_ma_analysis()` et `run_pipeline()` ne sont PAS implémentés dans `orchestrator.py`. Les agents existent (`DealSourcingAgent`, `StrategicFitAgent`, etc.) mais ne sont pas orchestrés.

**À faire** :
- Implémenter `run_ma_analysis(target, acquirer)` dans `orchestrator.py`
- Implémenter `run_pipeline(buyer, thesis)` dans `orchestrator.py`
- Brancher sur les endpoints API existants (`/ma`, `/pipeline`)

---

### 3.2 IC Scoring upgrade

**Problème** : `ma/scoring.py` est un scoring simple pondéré. Pas de logique IC réelle.

**À faire** :
- Scoring stratégique avec sous-critères (fit, synergies, integration complexity)
- LBO feasibility score automatique (branché sur `lbo.py`)
- Risk-adjusted scoring (red flags impact score)
- Output : scorecard IC prête pour investment committee

---

### 3.3 Screening automatique de cibles

**À faire** :
- Critères acquéreur → filtres sectoriels, géographiques, size
- Ranking par score IC
- Pipeline auto-généré type PitchBook

---

## 🟢 PHASE 4 — PRODUCTISATION

### 4.1 Caching

- Cache yfinance (TTL 1h par ticker)
- Cache Mistral responses (hash prompt → response)
- Éviter double-fetch sur runs successifs

### 4.2 Logging structuré

Ajouter pour chaque run :
- `request_id`
- `ticker` + `data_confidence`
- `wacc_method` (capm / estimated / sector_default)
- `valuation_notes` (audit trail complet)
- Temps d'exécution par step

### 4.3 SaaS UI

- Next.js dashboard : recherche société → memo instantané
- Graphique football field (DCF vs Comps vs TX)
- Export PPT / Excel one-click
- API multi-user avec clés

---

## 📋 BACKLOG (à ne pas oublier)

| Item | Priorité | Notes |
|------|----------|-------|
| Retry LLM si JSON invalide | Medium | Max 2-3 tentatives avec prompt corrigé |
| Timeout par agent (20-40s) | Medium | Éviter blocage CLI |
| Parallélisation Market + Financials | Low | async agents |
| Scénarios DCF (bear/base/bull) | Medium | `finance/core/scenarios.py` — stub existant |
| Tests unitaires valuation engine | High | Aucun test actuellement |
| Bloomberg / Capital IQ (si credentials) | Future | Tier 1 data source |
| SEC / EDGAR integration | Future | Pour les filings 10-K/10-Q |

---

## ⚠️ RÈGLE NON NÉGOCIABLE

**LLM = analyse qualitative uniquement.**

Le LLM ne produit jamais :
- de chiffres de revenus
- de marges EBITDA
- de WACC
- d'enterprise value

Ces chiffres viennent exclusivement de : `yfinance (verified)` → `assumptions LLM (estimated)` → `defaults sectoriels (inferred)`.

Dans cet ordre. Toujours.
