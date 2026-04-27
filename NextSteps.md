# NEXT STEPS — GOLD ROGER

---

## ✅ PHASES 1–5 COMPLÉTÉES

| Phase | Item | Fichier(s) | Statut |
|-------|------|-----------|--------|
| 1 | Fetcher yfinance (données vérifiées) | `data/fetcher.py` | ✅ |
| 1 | Tables multiples sectorielles (20+ secteurs) | `data/sector_multiples.py` | ✅ |
| 1 | WACC CAPM (β réel, Rf=4.5%, ERP=5.5%) | `finance/core/wacc.py` | ✅ |
| 1 | DCF corrigé (NWC incrémental + D&A tax shield) | `finance/valuation/dcf.py` | ✅ |
| 1 | ValuationService orchestrateur | `finance/core/valuation_service.py` | ✅ |
| 2 | Estimations forward analystes | `data/fetcher.py` | ✅ |
| 2 | Path P/E + P/B secteur financier | `valuation_service.py` | ✅ |
| 2 | LBO engine déterministe (IRR, MOIC) | `finance/valuation/lbo.py` | ✅ |
| 2 | SOTP framework | `finance/valuation/sotp.py` | ✅ |
| 3 | `run_ma_analysis()` — pipeline M&A complet | `orchestrator.py` | ✅ |
| 3 | `run_pipeline()` — pipeline acquisitions | `orchestrator.py` | ✅ |
| 3 | IC Scoring institutionnel (6 dimensions) | `ma/scoring.py` | ✅ |
| 4 | Cache TTL (1h yfinance, 24h ticker) | `utils/cache.py` | ✅ |
| 4 | Logging structuré JSON-lines | `utils/logger.py` | ✅ |
| 5 | **Data provider layer pluggable** | `data/providers/` + `data/registry.py` | ✅ |
| 5 | **Peer comparables réels** (PeerFinderAgent + yfinance) | `data/comparables.py` | ✅ |
| 5 | **Bear/Base/Bull scenarios** (football field) | `finance/core/scenarios.py` | ✅ |
| 5 | **IC scoring enrichi** depuis outputs agents | `ma/scoring.py` | ✅ |
| 5 | **Revenue series: projections forward** (bug fix) | `valuation_service.py` | ✅ |
| 5 | **PPT 10 slides** (football field + peer comps + IC) | `exporters/pptx.py` | ✅ |
| 5 | **DCF poids 0% banques** (path pe_pb) | `valuation_service.py` | ✅ |
| 5 | **LBO skippé mega-caps** (MCap > $500B) | `valuation_service.py` | ✅ |
| 5 | **Rate limit Mistral** (backoff 60s + global 3s) | `agents/base.py` | ✅ |

---

## 🔴 PRIORITÉ 1 — Sources de données premium

### 1.1 Bloomberg BLP Integration

**Fichier** : `data/providers/bloomberg.py` — stub prêt, `is_available()` gate sur `BLOOMBERG_API_KEY`

Pour activer :
1. Installer `blpapi` Python SDK (fourni avec licence Bloomberg Terminal)
2. Implémenter `fetch()` avec BDP/BDH calls
3. Set `BLOOMBERG_API_KEY=any_value` dans `.env`

Bloomberg apporte : données intraday, private company estimates, M&A transaction comps, credit ratings, consensus estimates complets.

### 1.2 Capital IQ Integration

**Fichier** : `data/providers/capitaliq.py` — stub prêt

Pour activer : `CAPITALIQ_USERNAME` + `CAPITALIQ_PASSWORD` dans `.env`.

Valeur ajoutée M&A : précédent transactions database, private company financials, covenants, credit.

### 1.3 Crunchbase API (privées, freemium)

**À créer** : `data/providers/crunchbase.py`

Crunchbase a des données de revenus estimés pour les startups/scale-ups privées. API gratuite jusqu'à 200 req/jour.

---

## 🟡 PRIORITÉ 2 — Qualité Engine

### 2.1 Tests unitaires valuation engine

**Aucun test.** Un changement de formule peut casser silencieusement.

À créer : `tests/test_dcf.py`, `tests/test_lbo.py`, `tests/test_wacc.py`, `tests/test_scenarios.py`

Tests minimum :
- DCF : revenue serie forward → EV dans range attendu
- WACC : β=1.0 → ~10% WACC (Rf=4.5%, ERP=5.5%)
- Scenarios : bull.blended_ev > base.blended_ev > bear.blended_ev
- Peers : 3 tickers → PeerMultiples.n_peers == 3, médiane calculée

### 2.2 SEC EDGAR — revenus historiques

**Fichier** : `data/providers/sec_edgar.py` — fetch revenue implémenté

À améliorer : ajouter EBITDA, net income, capex depuis les filings 10-K.
Permet de croiser les données yfinance avec les chiffres SEC officiels.

### 2.3 Retry JSON invalide LLM

Si l'agent retourne un JSON malformé, `parse_model` déclenche silencieusement le fallback.

**Fix** : ajouter retry avec prompt "STRICT JSON ONLY, no markdown" si fallback déclenché.

---

## 🟡 PRIORITÉ 3 — Précision & Robustesse

### 3.1 Normalisation devises privées

Pour Longchamp (€), le LLM peut retourner des montants en EUR.
Le `_f()` helper parse le nombre mais ne convertit pas.

**Fix** : détecter la devise dans la réponse LLM, appliquer FX rate EUR→USD via yfinance (`EURUSD=X`).

### 3.2 Scenarios — narrative enrichie

Aujourd'hui les scénarios sont purement numériques.
À ajouter : 1–2 phrases narratives par scénario dérivées du thesis agent.
Ex : "Bear : ralentissement IA en 2026, compression des multiples" pour NVIDIA.

### 3.3 SOTP pour conglomérats

SOTP implémenté mais pas câblé dans `run_analysis`.
Pour LVMH, Berkshire, Alphabet — détecter multi-segment et proposer SOTP automatiquement.

---

## 🟢 PRIORITÉ 4 — Productisation SaaS

### 4.1 Frontend Next.js

Périmètre minimal :
- Search bar → `run_analysis()` → affichage memo + football field
- Export PPT / Excel one-click
- Pipeline M&A view

### 4.2 Cache persistent (Redis ou fichier)

Actuellement le cache est in-process (reset à chaque restart).
Passer à fichier JSON avec TTL pour persistance entre runs.

### 4.3 Multi-user / API keys

- FastAPI avec auth Bearer (déjà en place, à sécuriser)
- Rate limiting par user

---

## ⚠️ RÈGLE NON NÉGOCIABLE

**LLM = analyse qualitative uniquement.**

Le LLM ne produit jamais :
- chiffres de revenus, marges, WACC, EV dans les calculs finaux

Ces chiffres viennent exclusivement de :
1. **Bloomberg / CapIQ** (premium, si credentials) — priorité maximale
2. **yfinance / SEC EDGAR** (free, verified) — défaut publiques
3. **Estimations LLM** (estimated) — fallback privées uniquement
4. **Defaults sectoriels** (inferred) — dernier recours

Dans cet ordre. Toujours.
