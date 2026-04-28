# NEXT STEPS — GOLD ROGER

---

## ✅ PHASES COMPLÉTÉES (1–12)

| Phase | Item | Fichier(s) | Statut |
|-------|------|-----------|--------|
| 1 | Fetcher yfinance (données vérifiées) | `data/fetcher.py` | ✅ |
| 1 | Tables multiples sectorielles (20+ secteurs) | `data/sector_multiples.py` | ✅ |
| 1 | WACC CAPM (β réel, Rf=4.5%, ERP=5.5%) | `finance/core/wacc.py` | ✅ |
| 1 | DCF corrigé (NWC incrémental + D&A tax shield) | `finance/valuation/dcf.py` | ✅ |
| 1 | ValuationService orchestrateur | `finance/core/valuation_service.py` | ✅ |
| 2 | Estimations forward analystes | `data/fetcher.py` | ✅ |
| 2 | Path P/E + P/B secteur financier | `finance/core/valuation_service.py` | ✅ |
| 2 | LBO engine déterministe (IRR, MOIC) | `finance/valuation/lbo.py` | ✅ |
| 2 | SOTP framework (implémenté, pas encore câblé auto) | `finance/valuation/sotp.py` | ✅ |
| 3 | `run_ma_analysis()` — pipeline M&A complet | `orchestrator.py` | ✅ |
| 3 | `run_pipeline()` — pipeline acquisitions | `orchestrator.py` | ✅ |
| 3 | IC Scoring institutionnel (6 dimensions) | `ma/scoring.py` | ✅ |
| 4 | Cache TTL (1h yfinance, 24h ticker) | `utils/cache.py` | ✅ |
| 4 | Logging structuré JSON-lines | `utils/logger.py` | ✅ |
| 5 | Data provider layer pluggable (`DataRegistry`) | `data/providers/` + `data/registry.py` | ✅ |
| 5 | Crunchbase API (freemium, privées) | `data/providers/crunchbase.py` | ✅ |
| 5 | Peer comparables réels (PeerFinderAgent + yfinance) | `data/comparables.py` | ✅ |
| 5 | Bear/Base/Bull scenarios (football field) | `finance/core/scenarios.py` | ✅ |
| 5 | IC scoring enrichi depuis outputs agents | `ma/scoring.py` | ✅ |
| 5 | PPT 10 slides (football field + peer comps + IC) | `exporters/pptx.py` | ✅ |
| 5 | DCF poids 0% banques (path pe_pb) | `finance/core/valuation_service.py` | ✅ |
| 5 | LBO skippé mega-caps (MCap > $500B) | `finance/core/valuation_service.py` | ✅ |
| 5 | Rate limit Mistral (backoff + global gap) | `agents/base.py` | ✅ |
| 5 | 20 tests unitaires (WACC, DCF, LBO, scenarios) | `tests/` | ✅ |
| 6 | Auto output subfolder `outputs/<name>_<ts>/` | `cli.py` | ✅ |
| 6 | Pipeline: 3 targets, mistral-small, `--quick` flag | `agents/specialists.py`, `cli.py` | ✅ |
| 6 | Pipeline retry on 0 targets + Optional fields | `orchestrator.py`, `models/__init__.py` | ✅ |
| 6 | Football field unit bug (EV passé comme multiple) | `orchestrator.py` | ✅ |
| 6 | Agent speed: rate gap 1s, tool rounds 3, synthesis agents no web search | `agents/base.py`, `agents/specialists.py` | ✅ |
| 7 | LLM-agnostic: Mistral/Anthropic/OpenAI via `--llm` + `LLM_PROVIDER` | `agents/llm_client.py`, `agents/providers/` | ✅ |
| 7 | EU registries: Companies House 🇬🇧, Infogreffe 🇫🇷, Handelsregister 🇩🇪 | `data/providers/` | ✅ |
| 7 | Private triangulation engine (5-signal weighted median) | `data/private_triangulation.py` | ✅ |
| 8 | DataCollectorAgent hérite BaseAgent (fix LLMProvider) | `agents/specialists.py` | ✅ |
| 8 | No placeholder values — DCF/comps omis si revenue manquant, `N/A` honnête | `finance/core/valuation_service.py`, `orchestrator.py` | ✅ |
| 8 | Optional LLM deps (anthropic/openai groupes optionnels, erreur claire) | `pyproject.toml`, `agents/llm_client.py` | ✅ |
| 8 | `.env.example` documenté (toutes variables, instructions) | `.env.example` | ✅ |
| 8 | `fetch_by_name()` dans DataRegistry — EU registries appelés pour privées | `data/registry.py`, `orchestrator.py` | ✅ |
| 8 | Name Resolver — LLM one-shot → identifiants légaux par source + normalisation | `data/name_resolver.py` | ✅ |
| 8 | Revenue fallback — si null après 2 LLM attempts → appel ciblé → football field débloqué | `orchestrator.py` | ✅ |
| 8 | FinancialModelerAgent prompt renforcé — revenue_current NEVER null si données trouvées | `agents/specialists.py` | ✅ |
| 10 | Target price bug — `implied_value` now human-readable EV ("$4.97T"); `target_price` = per-share intrinsic; unambiguous labels in CLI + PPT | `models/__init__.py`, `orchestrator.py`, `cli.py`, `exporters/pptx.py` | ✅ |
| 10 | Mega-cap tx comps exclusion — weight=0 for MCap >$500B; reweight DCF 60% / Comps 40% | `finance/core/valuation_service.py` | ✅ |
| 10 | Private company recommendation — "HOLD" translated to ATTRACTIVE / NEUTRAL / EXPENSIVE | `orchestrator.py` | ✅ |
| 10 | Revenue reconciliation — thesis agent receives `verified_revenue` lock; cannot contradict data layer | `agents/specialists.py`, `orchestrator.py` | ✅ |
| 10 | Thesis agent retry — now uses `_parse_with_retry` like other agents | `orchestrator.py` | ✅ |
| 9 | KVK 🇳🇱 provider (free, `api.kvk.nl`) — sector from SBI code | `data/providers/kvk.py` | ✅ |
| 9 | Registro Mercantil 🇪🇸 provider (BORME + cif.es fallback) | `data/providers/registro_mercantil.py` | ✅ |
| 9 | Fuzzy name matching (difflib) — `fuzzy_best_match()` in name_resolver, used by Infogreffe + Companies House | `data/name_resolver.py`, providers | ✅ |
| 9 | SOTP auto-detect — keyword detection → LLM segment split → `compute_sotp()` → ValuationMethod | `orchestrator.py`, `finance/valuation/sotp.py` | ✅ |
| 9 | Scenario narratives wired — bear/base/bull.narrative from thesis agent | `orchestrator.py`, `models/__init__.py` | ✅ |
| 9 | JSON retry for Fundamentals + Market agents (5.4 done) | `orchestrator.py` | ✅ |
| 9 | Private triangulation wired (5.3 done) | `orchestrator.py`, `data/private_triangulation.py` | ✅ |
| 9 | Live FX rates via yfinance with hardcoded fallback | `finance/core/valuation_service.py` | ✅ |
| 11 | DCF NWC year-1 fix — `DCFInput.base_revenue` anchors NWC delta to actual year-0 revenue | `finance/valuation/dcf.py`, `finance/core/valuation_service.py`, `finance/core/scenarios.py` | ✅ |
| 11 | LBO revenue fix — `entry_ebitda / ebitda_margin` (was `(entry_ev / exit_multiple) / ebitda_margin`) | `finance/valuation/lbo.py` | ✅ |
| 11 | Scenarios year-0 anchor — all 3 scenarios share same y0 actual revenue; delta applies to growth rate not level | `finance/core/scenarios.py` | ✅ |
| 11 | Aggregator normalization — weights auto-normalize to 1.0; `blended = mid` (was biased average) | `finance/valuation/aggregator.py` | ✅ |
| 11 | Sector multiples word-boundary fix — `_word_in()` regex prevents "fintech" → "financials" misfires | `data/sector_multiples.py` | ✅ |
| 11 | WACC net-cash note — logs "unlevered WACC (D=0)" when net_debt < 0 | `finance/core/valuation_service.py` | ✅ |
| 11 | LBO test updated — `_standard_lbo()` inputs internally consistent with corrected formula (entry 5x, no expansion, 5% growth) | `tests/test_lbo.py` | ✅ |
| 12 | EU registry audit — dead APIs removed, `is_available()` corrected, silent 401s eliminated across all 5 providers | see below | ✅ |
| 12 | 🇫🇷 Infogreffe rewritten — `opendata.infogreffe.fr` dead; replaced with `recherche-entreprises.api.gouv.fr` (official FR govt, no auth). Sector only — no revenue. | `data/providers/infogreffe.py` | ✅ |
| 12 | 🇬🇧 Companies House — anonymous access removed (401). `is_available()` now gates on `COMPANIES_HOUSE_API_KEY`. Free key at developer.company-information.service.gov.uk | `data/providers/companies_house.py` | ✅ |
| 12 | 🇩🇪 Handelsregister — `api.offeneregister.de` DNS dead; removed. Provider now uses Bundesanzeiger directly as primary (best-effort revenue via HTML regex). | `data/providers/handelsregister.py` | ✅ |
| 12 | 🇪🇸 Registro Mercantil — `api.cif.es` DNS dead; removed. BORME full-text search (`boe.es`) used as sole source (company existence only, no revenue). | `data/providers/registro_mercantil.py` | ✅ |
| 12 | 🇳🇱 KVK — API requires key despite "no key required" docstring (401). `is_available()` now gates on `KVK_API_KEY`. Free key at developers.kvk.nl | `data/providers/kvk.py` | ✅ |

---

## 🎯 VISION — REMPLACER L'ANALYSTE M&A

**Objectif** : produire une analyse meilleure qu'un analyste M&A humain sur toutes les tâches courantes.

**Scope** : M&A européen en priorité (EU registries gratuits = avantage compétitif clé), architecture globale. Toutes tailles de deal.

**LLM** : Mistral (gratuit, défaut). Architecture LLM-agnostique en place — switch via `.env` ou `--llm` sans toucher au code. Anthropic/OpenAI optionnels (install en une commande).

Ce que l'outil bat déjà un analyste sur :
- Vitesse (minutes vs jours)
- Cohérence (même structure, zéro biais d'ancrage)
- Couverture systématique (10 dimensions)
- Disponibilité 24/7, zéro fatigue

Les trois vrais gaps restants :

### GAP 1 — Private company data depth
L'outil triangule maintenant depuis 3 sources (EU registry + web search + LLM fallback). Un analyste senior utilise 6–8 signaux. À ajouter :
- LinkedIn headcount × revenue/employee benchmark par secteur
- Crunchbase funding history → implied valuation
- SimilarWeb / web traffic → proxy revenue DTC/consumer
- Press NLP → extraction chiffres depuis articles
- Transaction comps scraping (press releases M&A)

### GAP 2 — Transaction comps sans CapIQ
Les transaction comps actuelles utilisent un multiple sectoriel par défaut — pas de vraies transactions. Ancrer sur des transactions récentes fermées = différenciation majeure.
- Scraping PR Newswire / BusinessWire / Cision
- NLP : acquéreur, cible, secteur, EV, multiple, date
- Base JSON locale mise à jour à chaque run

### GAP 3 — Output polish
- PPT : vrais graphiques python-pptx (bar chart football field, courbe DCF, waterfall synergies) vs tables ASCII actuelles
- Excel : modèle 3-statement lié (P&L → BS → CF) vs DCF standalone
- Executive summary 1-pager

---

## 🔴 PRIORITÉ 1 — Sources de données premium

### 1.1 Bloomberg BLP
**Fichier** : `data/providers/bloomberg.py` — stub prêt, gate sur `BLOOMBERG_API_KEY`
Activer : installer `blpapi` SDK + set `BLOOMBERG_API_KEY` dans `.env`.
Apporte : données intraday, private company estimates, transaction comps, consensus complets.

### 1.2 Capital IQ
**Fichier** : `data/providers/capitaliq.py` — stub prêt, gate sur `CAPITALIQ_USERNAME` + `CAPITALIQ_PASSWORD`
Valeur M&A : transactions database, private company financials, covenants, credit.

---

## 🔴 PRIORITÉ 2 — Connectivité data (sources gratuites manquantes)

Architecture `DataProvider` / `DataRegistry` en place — chaque source = 1 fichier, 0 modification au moteur.

| Source | Pays | Données | Statut |
|--------|------|---------|--------|
| Companies House 🇬🇧 | UK | SIC/secteur + revenue XBRL best-effort | ⚠️ `COMPANIES_HOUSE_API_KEY` requis (gratuit) |
| recherche-entreprises.api.gouv.fr 🇫🇷 | FR | SIREN, NAF/secteur — pas de revenue | ✅ intégré (gratuit, no auth) |
| Bundesanzeiger 🇩🇪 | DE | Revenue best-effort HTML | ✅ intégré (gratuit, no auth) |
| SEC EDGAR 🇺🇸 | US | 10-K revenues | ✅ intégré |
| Crunchbase | Global | Funding, revenus estimés | ✅ intégré |
| KVK 🇳🇱 | NL | SBI/secteur — pas de revenue | ⚠️ `KVK_API_KEY` requis (gratuit) |
| Registro Mercantil 🇪🇸 | ES | Existence société via BORME — pas de revenue | ✅ intégré (gratuit, no auth) |
| PAPPERS 🇫🇷 | FR | CA, résultat, bilans complets | ⬜ `PAPPERS_API_KEY` (100 calls/mois gratuit) |
| Dealroom | EU | Startups, funding | ⬜ freemium |
| SimilarWeb | Global | Trafic web | ⬜ freemium |
| OpenCorporates | 140+ pays | Données légales | ⬜ freemium |

**Sources premium (stubs à compléter)** : PitchBook, Mergermarket, Dealogic, Preqin.

---

## ⚠️ EU REGISTRY STATUS — Sociétés Privées

Audit complet (2026-04) — état réel de chaque provider :

| Pays | Provider | Statut | Revenue | Condition |
|------|----------|--------|---------|-----------|
| 🇫🇷 FR | recherche-entreprises.api.gouv.fr | ✅ Actif | ❌ Non dispo | Gratuit, aucune clé |
| 🇬🇧 UK | Companies House API | ⚠️ Clé requise | ⚠️ Best-effort (XBRL) | `COMPANIES_HOUSE_API_KEY` — gratuit (developer.company-information.service.gov.uk) |
| 🇩🇪 DE | Bundesanzeiger | ✅ Actif | ⚠️ Best-effort (HTML) | Gratuit, aucune clé |
| 🇪🇸 ES | BORME (boe.es) | ✅ Actif | ❌ Non dispo | Gratuit — confirmation existence seulement |
| 🇳🇱 NL | KVK | ⚠️ Clé requise | ❌ Non dispo | `KVK_API_KEY` — gratuit (developers.kvk.nl) |

**Conséquence** : pour toutes les sociétés privées européennes, le revenue passe par le fallback web search + LLM. Les registres confirment l'existence et donnent le secteur (FR/DE) mais pas les comptes.

**Pour débloquer UK + NL** : ajouter `COMPANIES_HOUSE_API_KEY` et `KVK_API_KEY` dans `.env` (inscription gratuite).

**Prochaine étape données privées recommandée** : intégrer PAPPERS (🇫🇷) avec `PAPPERS_API_KEY` — 100 calls/mois gratuits, retourne CA, résultat net, bilans. Remplace définitivement le gap revenue France.

---

## ✅ PRIORITÉ 3 — Triangulation privée systématique

`data/private_triangulation.py` est **câblé** dans l'orchestrateur (Phase 9). Après revenue fallback LLM → si toujours null → `triangulate_revenue()` (médiane pondérée multi-signaux).

Signaux implémentés : EU registry, Crunchbase range, headcount × benchmark, funding ARR proxy, press NLP.
Signaux manquants : SimilarWeb traffic, LinkedIn headcount live, transaction comps scraping.

---

## ✅ PRIORITÉ 4 — Name Resolution : précision améliorée

- ✅ Fuzzy matching `difflib.SequenceMatcher` (score ≥ 0.6) — `fuzzy_best_match()` utilisé par Infogreffe et Companies House pour sélectionner le meilleur résultat parmi les candidats du registry
- Prompt LLM demande déjà la raison sociale exacte par source (infogreffe_query, companies_house_query, etc.)
- ⬜ SIRET/SIREN lookup pour Infogreffe (recherche plus précise que dénomination seule)

---

## 🟡 PRIORITÉ 5 — Qualité Engine

### ✅ 5.1 Taux de change live
`_live_fx()` dans `ValuationService` — `yf.Ticker("EURUSD=X")` etc. avec cache en mémoire et fallback hardcoded.

### 5.2 Transaction comps sans CapIQ
Actuellement : multiple sectoriel par défaut — non ancré sur de vraies transactions.
Fix : `TransactionCompsAgent` — scrape press releases M&A → NLP → base JSON locale.

### ✅ 5.3 SOTP pour conglomérats
Câblé dans `run_analysis` : détection par mots-clés ("segment", "division", "business unit", etc.) → LLM extrait les segments → `compute_sotp()` → `ValuationMethod("SOTP")` ajouté.

### ✅ 5.4 Retry JSON pour tous les agents
`_parse_with_retry()` câblé pour Fundamentals, Market, Financials, Assumptions.

### ✅ 5.5 Scenario narratives
`ScenarioSummary.narrative` wired depuis `thesis.bear_case` / `base_case` / `bull_case`.

---

## 🟡 PRIORITÉ 6 — Output quality (PPT + Excel)

### PPT Goldman-quality
- Vrais graphiques python-pptx : bar chart football field horizontal, courbe DCF projetée, waterfall synergies
- Template configurable (couleurs, logo client)
- Slide 0 : executive summary 1-pager (société, recommandation, EV implied, IC score, 3 bullets)

### Excel 3-statement
- P&L + Bilan + Cash Flow liés (modèle intégré)
- Onglets : Assumptions / P&L / BS / CF / DCF / LBO / Scenarios / Sensitivities
- Actuellement : DCF standalone uniquement

---

## 🟡 PRIORITÉ 7 — Opportunity Screening (sourcing actif)

### 7.1 Scoring de pertinence des cibles
Aujourd'hui toutes les cibles du pipeline ont le même poids. Ajouter un score de pertinence vs brief client (taille, géo, secteur, stade).

### 7.2 Comparaison à une société de référence
Si le client donne "trouve-moi des cibles comme Veeva", extraire le profil de référence et l'utiliser comme filtre de screening.

### 7.3 Shortlist scorecard PPT
Nouveau slide pipeline : tableau des cibles avec score de pertinence, EV estimée, IC score, statut, next step.

**Fichiers** : `agents/specialists.py` (SourcingAgent), `orchestrator.py` (run_pipeline), `exporters/pptx.py`

---

## 🟢 PRIORITÉ 8 — Productisation SaaS

### 8.1 Frontend Next.js (périmètre minimal)
- Search bar → `run_analysis()` → affichage memo + football field
- Export PPT / Excel one-click
- Pipeline M&A view avec shortlist scorecard
- Bouton switch LLM provider

### 8.2 Cache persistent
Cache actuel : in-process (reset à chaque restart). Passer à fichier JSON avec TTL.

### 8.3 Multi-user / API keys
FastAPI avec auth Bearer (en place), rate limiting par user.

---

## ⚠️ RÈGLE NON NÉGOCIABLE

**LLM = analyse qualitative + estimation de dernier recours uniquement.**

Hiérarchie des sources pour les chiffres financiers (dans l'ordre, toujours) :
1. **Bloomberg / CapIQ** (premium, si credentials) — priorité maximale, données vérifiées
2. **yfinance / SEC EDGAR** (gratuit) — défaut sociétés publiques, données vérifiées
3. **EU Registries** (gratuit) — Infogreffe / Companies House / Handelsregister, données officielles
4. **Crunchbase** (freemium) — revenus estimés startups/privées, taggé `estimated`
5. **Private triangulation** (multi-signal) — médiane pondérée, taggé `estimated`
6. **LLM estimation** (fallback) — revenue fallback uniquement si aucune autre source, taggé `estimated`
7. **Defaults sectoriels** (dernier recours) — taggé `inferred`

Le LLM ne produit **jamais** les chiffres finaux de valorisation (EV, WACC, multiples de sortie). Il peut estimer le revenue d'une société privée comme fallback, explicitement taggué `estimated`.
