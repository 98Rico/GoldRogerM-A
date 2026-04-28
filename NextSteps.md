# NEXT STEPS — GOLD ROGER

---

## ✅ PHASES COMPLÉTÉES (1–8)

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
| Companies House 🇬🇧 | UK | Comptes annuels, SIC | ✅ intégré |
| Infogreffe 🇫🇷 | FR | CA déclaré, bilans | ✅ intégré |
| Handelsregister 🇩🇪 | DE | Comptes GmbH/AG | ✅ intégré |
| SEC EDGAR 🇺🇸 | US | 10-K revenues | ✅ intégré |
| Crunchbase | Global | Funding, revenus estimés | ✅ intégré |
| KVK 🇳🇱 | NL | Comptes, directeurs | ⬜ à connecter |
| Registro Mercantil 🇪🇸 | ES | Comptes annuels | ⬜ à connecter |
| Dealroom | EU | Startups, funding | ⬜ freemium |
| SimilarWeb | Global | Trafic web | ⬜ freemium |
| OpenCorporates | 140+ pays | Données légales | ⬜ freemium |

**Sources premium (stubs à compléter)** : PitchBook, Mergermarket, Dealogic, Preqin.

---

## 🔴 PRIORITÉ 3 — Triangulation privée systématique

`data/private_triangulation.py` est **construit** (Phase 7) mais **pas encore câblé** dans le flow orchestrateur.

Signaux implémentés : EU registry, Crunchbase range, headcount × benchmark, funding ARR proxy, press NLP.
Signaux manquants : SimilarWeb traffic, LinkedIn headcount live, transaction comps scraping.

**À faire** : appeler `triangulate_revenue()` depuis `orchestrator.py` pour les sociétés privées, en complément du revenue fallback actuel. La triangulation donne une estimation plus robuste (médiane pondérée multi-signaux) vs un seul appel LLM.

**Fichiers** : `data/private_triangulation.py` (existant), `orchestrator.py` (câblage)

---

## 🔴 PRIORITÉ 4 — Name Resolution : améliorer la précision

Le resolver actuel (Phase 8) normalise bien le nom commercial → identifiant par source. Améliorations :
- Fuzzy matching sur les résultats retournés par le registry (score similarité > 0.8 au lieu de `LIKE "%name%"`)
- Enrichir le prompt LLM pour demander la **raison sociale exacte** (ex : "SEZANE SAS" pas juste "SEZANE")
- Ajouter SIRET/SIREN lookup pour Infogreffe (recherche plus précise que dénomination)

---

## 🟡 PRIORITÉ 5 — Qualité Engine

### 5.1 Taux de change live ⚠️ BUG CONNU
Les taux EUR/GBP/CHF/CAD sont **hardcodés** dans `finance/core/valuation_service.py` (`_FX` dict).
Fix : `yf.Ticker("EURUSD=X").fast_info["last_price"]` avec fallback sur hardcoded. Tagguer `fx_source`.

### 5.2 Transaction comps sans CapIQ
Actuellement : multiple sectoriel par défaut — non ancré sur de vraies transactions.
Fix : `TransactionCompsAgent` — scrape press releases M&A → NLP → base JSON locale.

### 5.3 SOTP pour conglomérats
Implémenté (`finance/valuation/sotp.py`) mais pas câblé dans `run_analysis`.
Fix : détecter multi-segment (LVMH, Berkshire, Alphabet) → proposer SOTP automatiquement.

### 5.4 Retry JSON pour tous les agents
`_parse_with_retry()` existe mais câblé uniquement pour Financials et Assumptions.
Fix : étendre à Market, Fundamentals, Thesis, M&A agents dans `orchestrator.py`.

### 5.5 Scenario narratives
Les scénarios Bear/Base/Bull sont numériques. Ajouter 1–2 phrases narratives par scénario depuis le thesis agent.

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
