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
| 6 | **Football field unit bug** (EV passed as multiple → absurd values) | `orchestrator.py` | ✅ |
| 6 | **Agent speed** — rate gap 3s→1s, tool rounds 6→3, synthesis agents no web search | `agents/base.py`, `specialists.py` | ✅ |
| 7 | **LLM-agnostic architecture** — Mistral/Anthropic/OpenAI via `--llm` flag | `agents/llm_client.py`, `agents/providers/` | ✅ |
| 7 | **EU registries** — Companies House 🇬🇧, Infogreffe 🇫🇷, Handelsregister 🇩🇪 | `data/providers/` | ✅ |
| 7 | **Private triangulation engine** — 5-signal weighted median estimate | `data/private_triangulation.py` | ✅ |
| 8 | **DataCollectorAgent fix** — inherits BaseAgent, uses LLMProvider (était `self.client.chat.complete`) | `agents/specialists.py` | ✅ |
| 8 | **No placeholder values** — DCF/comps/football field skipped when revenue unavailable; `N/A` displayed honestly | `finance/core/valuation_service.py`, `orchestrator.py` | ✅ |
| 8 | **Optional LLM deps** — `anthropic`/`openai` sont des dependency groups optionnels; install en une commande, erreur claire si manquant | `pyproject.toml`, `agents/llm_client.py` | ✅ |
| 8 | **`.env.example`** — documentes toutes les variables avec instructions d'activation | `.env.example` | ✅ |
| 8 | **EU registries câblés pour privées** — `fetch_by_name()` dans DataRegistry, appelé avant LLM agents | `data/registry.py`, `orchestrator.py` | ✅ |
| 8 | **Name Resolver** — LLM one-shot → identifiants légaux par source + normalisation accents/suffixes | `data/name_resolver.py` | ✅ |
| 8 | **Revenue fallback** — si `revenue_current` null après 2 LLM attempts → appel ciblé → unlocks football field | `orchestrator.py` | ✅ |
| 8 | **FinancialModelerAgent prompt** — CRITICAL: revenue_current NEVER null si données trouvées, exemples EUR→USD | `agents/specialists.py` | ✅ |

---

## 🎯 VISION — REMPLACER L'ANALYSTE M&A

**Objectif** : produire une analyse meilleure qu'un analyste M&A humain sur toutes les tâches qu'on lui confie.

**Scope** : M&A européen en priorité (Companies House, Infogreffe, Handelsregister = sources gratuites critiques), architecture globale dès le départ. Toutes tailles de deal.

**LLM** : Mistral actuellement (gratuit). Architecture LLM-agnostique à implémenter — même pattern que `DataRegistry` — pour switcher entre Mistral, Claude, GPT-4o via env var ou bouton UI sans toucher au code agent.

Ce que l'outil bat déjà un analyste sur :
- Vitesse (minutes vs jours)
- Cohérence (même structure, zéro biais d'ancrage)
- Couverture (10 dimensions systématiquement)
- Disponibilité (pas de fatigue, pas de vacances)

Les trois vrais gaps à combler :

### GAP 1 — Private company data depth (le plus critique)

Un analyste triangule depuis 6–8 signaux indépendants. L'outil n'en utilise qu'un (web search LLM).
Signaux à intégrer :
- **LinkedIn employee count × revenue/employee benchmark** par secteur → revenue estimate
- **Job postings** (volume, séniorité, fonctions) → stage de croissance, burn rate, orientation produit
- **Crunchbase funding history** → stage, total raised, lead investors, implied valuation
- **SimilarWeb / web traffic** → pour DTC/consumer, trafic = proxy revenue
- **Press/media NLP** → extraire mentions de revenus, parts de marché, clients clés depuis articles
- **Office footprint** → taille des locaux × coût marché → proxy base de coûts
- **Social media signals** → Meta Ad Library (spend estimé), followers → notoriété marque
- **Comparable recent M&A transactions** → closing prices from press releases (sans CapIQ)

### GAP 2 — Transaction comps (sans CapIQ)

Un analyste ancre les valorisations sur des *transactions fermées* similaires, pas juste des peers cotés.
"3 acquisitions comparables en B2B SaaS européen à 5–7x ARR en 2023–2024" = anchor solide.
Sans Mergermarket/CapIQ, partiellement reconstituable via :
- Scraping de press releases de M&A (PR Newswire, BusinessWire, Cision)
- NLP sur news financières (Reuters, Bloomberg articles publics)
- Base de données open source (OpenSanctions, Companies House filings)
- Dealroom API (freemium, spécialisé Europe)

### GAP 3 — Output polish

Le PPT est fonctionnel mais pas Goldman-quality. Combler le gap :
- Vrais graphiques (bar chart football field, waterfall synergies, courbe DCF)
- Template institutionnel avec charte graphique configurable
- Excel 3-statement complet (P&L, BS, CF liés) vs. DCF standalone actuel
- Executive summary page (1-pager pour IC)

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

## 🔴 PRIORITÉ 1b — Connectivité data dynamique

**Principe** : n'importe quelle source de données doit être connectable en 30 minutes, sans toucher au moteur de valorisation.

L'architecture `DataProvider` / `DataRegistry` est en place. Ce qui manque :

### Sources gratuites / freemium intégrées ✅ ou à connecter
| Source | Pays | Données | API |
|--------|------|---------|-----|
| **Companies House** | 🇬🇧 UK | Comptes annuels, SIC, statut | Gratuit REST | ✅ intégré |
| **Infogreffe / INSEE** | 🇫🇷 FR | CA déclaré, effectifs, bilans | Gratuit | ✅ intégré |
| **Handelsregister** | 🇩🇪 DE | Comptes annuels GmbH/AG | Gratuit | ✅ intégré |
| **KVK (Kamer van Koophandel)** | 🇳🇱 NL | Comptes, directeurs | Freemium |
| **Registro Mercantil** | 🇪🇸 ES | Comptes annuels | Gratuit |
| **Dealroom** | 🌍 EU | Startups, funding, revenus estimés | Freemium |
| **SimilarWeb** | 🌍 Global | Trafic web, canaux, géos | Freemium |
| **LinkedIn (via proxy)** | 🌍 Global | Headcount, croissance, offres d'emploi | Scraping indirect |
| **OpenCorporates** | 🌍 140+ pays | Données légales, filings | Freemium |
| **SEC EDGAR** | 🇺🇸 US | 10-K, 10-Q, revenus officiels | Gratuit (déjà intégré) |

### Sources premium (stubs à implémenter)
| Source | Valeur M&A |
|--------|-----------|
| **PitchBook** | Deals, valorisations, fonds |
| **Mergermarket** | Transaction comps, deal flow |
| **Dealogic** | Bookrunner, fees, process |
| **Preqin** | PE/VC fund data |

Chaque source = un fichier dans `data/providers/`, `is_available()` gate sur env var, zéro modification au reste du code.

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

## ✅ PRIORITÉ 1c — EU registries câblés + Name Resolver + Revenue Fallback

- `DataRegistry.fetch_by_name()` appelle Infogreffe → Companies House → Handelsregister en ordre
- `data/name_resolver.py` : résout le nom commercial → identifiant légal par source (LLM one-shot + fallback normalisation : accents supprimés, suffixes légaux strippés, variantes générées)
- Chaque provider essaie toutes les variantes du nom pour maximiser le taux de match
- Revenue fallback : si `revenue_current` toujours null après 2 tentatives LLM, appel ciblé "what is the revenue of X?" → unlocks football field même sans filings officiels
- **Résultat Sézane** : football field Bear $1.0B / Base $1.7B / Bull $2.6B (vs N/A avant)

---

## ✅ PRIORITÉ 1d — Name Resolution + Revenue Fallback (TERMINÉ)

- `data/name_resolver.py` : LLM one-shot → identifiants par source (infogreffe_query, companies_house_query, crunchbase_slug, etc.) avec fallback normalisation (accents, suffixes légaux, variantes)
- Chaque provider EU reçoit toutes les variantes et itère jusqu'au match
- Revenue fallback dans orchestrateur : si `revenue_current` null après 2 tentatives → appel LLM ciblé "what is the annual revenue of X?" → unlocks football field
- **Sézane résultat** : football field fonctionnel Bear $1.0B / Base $1.7B / Bull $2.6B

**Améliorations futures** : fuzzy matching sur résultats registry (score > 0.8), enrichir le resolver avec la raison sociale exacte vs nom commercial.

---

## ✅ PRIORITÉ 1e — FinancialModelerAgent : extraction structurée (TERMINÉ)

- Prompt renforcé : `revenue_current` MUST be a plain number, NEVER null si des données ont été trouvées, avec exemples de conversion EUR→USD
- Revenue fallback dans orchestrateur comme filet de sécurité

**Fix** :
1. Renforcer le prompt de `FinancialModelerAgent` pour exiger `revenue_current` en USD millions avec source et confidence
2. Ajouter un step de post-processing : si `revenue_current` est vide, extraire via regex depuis le texte free-form retourné
3. Marquer comme `estimated` avec source tag (ex: `"source": "web_search"`)
4. Intégrer le moteur `private_triangulation.py` (déjà construit) dans le flow orchestrateur pour les sociétés privées

**Impact** : une fois ce fix appliqué, Sézane et toute société privée avec des données web disponibles produira un football field complet plutôt qu'un `N/A`.

**Fichiers** : `agents/specialists.py` (FinancialModelerAgent prompt), `orchestrator.py` (appel triangulation), `data/private_triangulation.py` (déjà existant, pas encore câblé)

**Note** : l'EU registry fix (1c) couvre les sociétés avec filings officiels. Ce fix couvre le reste (sociétés sans filings publics, ou hors Europe). Le Name Resolver (1d) est un prérequis pour que 1c fonctionne de manière fiable.

---

## 🔴 PRIORITÉ 2b — Triangulation privée systématique

Pour que l'outil batte un analyste sur les sociétés privées, le `FinancialModelerAgent` doit systématiquement croiser plusieurs signaux indépendants — pas juste une recherche web générique.

### Moteur de triangulation à construire (`data/private_triangulation.py`)

```
1. Headcount signal    → LinkedIn scrape ou Crunchbase → × revenue/employee benchmark sectoriel
2. Funding signal      → Crunchbase total_raised → implied ARR (SaaS: ~3–5x ARR/capital raised)
3. Web traffic signal  → SimilarWeb → pour DTC/consumer: traffic × conversion × AOV
4. Press signal        → NLP sur articles → extraire chiffres revenus mentionnés explicitement
5. Comparable M&A      → scrape press releases → trouver 3 transactions similaires → appliquer multiples
6. Regulatory filings  → Companies House / Infogreffe → CA et résultat déclaré si disponible
```

Chaque signal produit un `(estimate, confidence, source)`. L'agrégateur prend la médiane pondérée par confidence.
Si ≥3 signaux concordent à ±30% → `confidence: "estimated"`. Sinon → `confidence: "inferred"`.

**Fichiers** : `data/private_triangulation.py` (nouveau), intégration dans `agents/specialists.py` (FinancialModelerAgent)

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

## 🟡 PRIORITÉ 3b — Output quality (PPT + Excel)

### PPT Goldman-quality
- Remplacer les tables ASCII par de vrais graphiques python-pptx : bar chart football field, courbe DCF, waterfall synergies
- Template configurable (couleurs, logo) par client
- Executive summary 1-pager (slide 0) : société, recommandation, EV implied, upside, IC score, 3 bullet points

### Excel 3-statement
- Lier P&L → Bilan → Cash Flow (modèle intégré)
- Onglets séparés : Assumptions / P&L / BS / CF / DCF / LBO / Scenarios / Sensitivities
- Actuellement : DCF standalone uniquement

### Transaction comps sans CapIQ
- Agent `TransactionCompsAgent` : scrape PR Newswire / BusinessWire pour transactions M&A annoncées
- NLP pour extraire : acquéreur, cible, secteur, EV, multiple (EV/EBITDA ou EV/Revenue), date
- Base locale JSON mise à jour à chaque run
- Alimente directement la méthode "Transactions" du DCF avec de vraies données récentes

## ✅ PRIORITÉ 3c — Architecture LLM-agnostique (TERMINÉE)

`agents/llm_client.py` + `agents/providers/` (Mistral, Anthropic, OpenAI). `BaseAgent` provider-agnostique. Mistral reste le défaut gratuit.

```bash
LLM_PROVIDER=anthropic    # .env — persistant
uv run python -m goldroger.cli --company "NVIDIA" --llm claude   # CLI — one-shot
```

Switcher de provider = une ligne. Package installé à la demande (`uv add --group anthropic anthropic`).

À ajouter si besoin : `OllamaProvider` (local/offline), per-agent provider override.

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
