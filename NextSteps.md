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
| 5 | **Crunchbase API** (freemium, privées) | `data/providers/crunchbase.py` | ✅ |
| 5 | **Peer comparables réels** (PeerFinderAgent + yfinance) | `data/comparables.py` | ✅ |
| 5 | **Bear/Base/Bull scenarios** (football field) | `finance/core/scenarios.py` | ✅ |
| 5 | **IC scoring enrichi** depuis outputs agents | `ma/scoring.py` | ✅ |
| 5 | **Revenue series: projections forward** (bug fix) | `valuation_service.py` | ✅ |
| 5 | **PPT 10 slides** (football field + peer comps + IC) | `exporters/pptx.py` | ✅ |
| 5 | **DCF poids 0% banques** (path pe_pb) | `valuation_service.py` | ✅ |
| 5 | **LBO skippé mega-caps** (MCap > $500B) | `valuation_service.py` | ✅ |
| 5 | **Rate limit Mistral** (backoff 60s + global 3s) | `agents/base.py` | ✅ |
| 5 | **20 tests unitaires** (WACC, DCF, LBO, scenarios) | `tests/` | ✅ |
| 6 | **Auto output subfolder** `outputs/<name>_<ts>/` | `cli.py` | ✅ |
| 6 | **Pipeline: 3 targets, mistral-small, `--quick` flag** | `specialists.py`, `cli.py` | ✅ |
| 6 | **Pipeline retry on 0 targets** + Optional fields on PipelineTarget | `orchestrator.py`, `models/__init__.py` | ✅ |
| 6 | **run_pipeline / run_ma_analysis imported in CLI** (NameError fix) | `cli.py` | ✅ |

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

---

## 🔴 PRIORITÉ 2 — Opportunity Screening (sourcing actif)

### 2.1 Enrichissement sourcing par secteur / société de référence

**Contexte** : quand un client dit "je cherche des opportunités dans le SaaS B2B européen" ou "trouve-moi des cibles similaires à Figma", le `SourcingAgent` actuel retourne une liste de noms mais l'analyse se limite à ce qui est demandé explicitement.

**À améliorer** :

**2.1a — Scoring de pertinence des cibles**
Pour chaque cible identifiée par le `SourcingAgent`, calculer un score de pertinence vis-à-vis du brief client (taille, géographie, secteur, stade de maturité). Aujourd'hui toutes les cibles ont le même poids.

**2.1b — Enrichissement due diligence automatique**
Pour un secteur donné, le système devrait automatiquement :
- Identifier les 10–15 acteurs clés (cotés + privés) via web search
- Récupérer les multiples sectoriels live pour calibrer les attentes de valorisation
- Trier par fit stratégique avant de soumettre au pipeline complet

**2.1c — Comparaison à une société de référence**
Si le client donne une société de référence ("trouve-moi des cibles comme Veeva"), extraire son profil (secteur, taille, marges, multiple) et utiliser ces paramètres comme filtre de screening. Aujourd'hui le pipeline ignore la société de référence pour calibrer les cibles.

**2.1d — Output : shortlist scorecard**
Ajouter un slide de synthèse au PPT pipeline : tableau des cibles avec score de pertinence, EV estimée, IC score, statut (publique/privée), next step recommandé.

**Fichiers concernés** : `agents/specialists.py` (SourcingAgent), `orchestrator.py` (run_pipeline), `exporters/pptx.py` (nouveau slide pipeline)

---

## 🟡 PRIORITÉ 3 — Qualité Engine

### 3.1 Taux de change live (BUG CONNU ⚠️)

**Problème** : les taux EUR/GBP/CHF/CAD sont **hardcodés** dans `valuation_service.py` (`_FX` dict) :
```python
_FX = {"€": 1.08, "eur": 1.08, "gbp": 1.26, "£": 1.26, "chf": 1.11, "cad": 0.74}
```

Ces taux changent et peuvent être significativement faux (ex : CHF/USD a bougé de 10%+ en 2024). Pour une analyse institutionnelle, utiliser des taux hardcodés est inacceptable.

**Fix** : fetcher les taux live via yfinance au moment de l'analyse :
```python
# yfinance tickers: "EURUSD=X", "GBPUSD=X", "CHFUSD=X", "CADUSD=X"
import yfinance as yf
rate = yf.Ticker("EURUSD=X").fast_info["last_price"]
```
Avec fallback sur les taux hardcodés si yfinance échoue. Tagguer `fx_source: "live"` vs `"fallback_hardcoded"` dans le MarketData.

**Fichier** : `finance/core/valuation_service.py` → `_FX` dict → fonction `_get_fx_rates()`

### 3.2 SEC EDGAR — données enrichies

**Fichier** : `data/providers/sec_edgar.py` — fetch revenue implémenté

À améliorer : ajouter EBITDA, net income, capex depuis les filings 10-K.
Permet de croiser les données yfinance avec les chiffres SEC officiels.

### 3.3 Retry JSON invalide LLM

Si l'agent retourne un JSON malformé, `parse_model` déclenche silencieusement le fallback. Un helper `_parse_with_retry()` existe dans l'orchestrateur pour les agents financiers/assumptions, mais pas encore câblé pour tous les agents (market, fundamentals, thesis, M&A agents).

**Fix** : étendre `_parse_with_retry()` à tous les agents dans `orchestrator.py`.

### 3.4 Scenarios — narrative enrichie

Aujourd'hui les scénarios sont purement numériques.
À ajouter : 1–2 phrases narratives par scénario dérivées du thesis agent.
Ex : "Bear : ralentissement IA en 2026, compression des multiples" pour NVIDIA.

### 3.5 SOTP pour conglomérats

SOTP implémenté mais pas câblé dans `run_analysis`.
Pour LVMH, Berkshire, Alphabet — détecter multi-segment et proposer SOTP automatiquement.

---

## 🟢 PRIORITÉ 4 — Productisation SaaS

### 4.1 Frontend Next.js

Périmètre minimal :
- Search bar → `run_analysis()` → affichage memo + football field
- Export PPT / Excel one-click
- Pipeline M&A view avec shortlist scorecard

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
3. **Crunchbase** (freemium) — revenus estimés startups/privées
4. **Estimations LLM** (estimated) — fallback privées uniquement
5. **Defaults sectoriels** (inferred) — dernier recours

Dans cet ordre. Toujours.
