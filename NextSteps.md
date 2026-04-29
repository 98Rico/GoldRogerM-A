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
| 14 | `--siren` CLI flag — bypasses fuzzy name resolution, calls Pappers then Infogreffe by SIREN ID directly | `cli.py`, `orchestrator.py`, `data/providers/pappers.py`, `data/providers/infogreffe.py` | ✅ |
| 14 | `SourcesLog` utility — tracks every data point (metric, value, source, confidence, URL) across the pipeline | `utils/sources_log.py` | ✅ |
| 14 | `sources.md` data room — written to output folder alongside Excel/PPT when `--excel` or `--pptx` used | `cli.py`, `orchestrator.py` | ✅ |
| 14 | `AnalysisResult.sources_md` field — carries markdown sources table through full pipeline | `models/__init__.py` | ✅ |
| 15 | Parallel agents — Market + Peers + Financials run concurrently via `ThreadPoolExecutor(max_workers=3)` after Fundamentals | `orchestrator.py` | ✅ |
| 15 | Revenue fallback + private triangulation moved inside `_do_financials()` — correct thread-local scope | `orchestrator.py` | ✅ |
| 15 | Timing output — wall-clock elapsed for parallel block displayed in CLI | `orchestrator.py` | ✅ |

---

## 🎯 VISION — REMPLACER L'ANALYSTE M&A

**Objectif** : produire une analyse meilleure qu'un analyste M&A humain sur toutes les tâches courantes.
**Scope** : M&A européen en priorité (EU registries gratuits = avantage compétitif clé). Toutes tailles de deal.
**LLM** : Mistral (gratuit, défaut). Switch via `--llm` ou `LLM_PROVIDER` dans `.env` — aucun changement de code.

Ce que l'outil bat déjà un analyste sur :
- Vitesse (minutes vs jours), cohérence (zéro biais), couverture systématique, 24/7

Gaps réels qui restent :
1. **Private data depth** — revenue vérifié pour sociétés privées hors France encore insuffisant
2. **Transaction comps** — multiples sectoriels par défaut, pas de vraies transactions récentes
3. **Output polish** — PPT tables text, pas de vrais graphiques; Excel DCF seul, pas 3-statement

---

## 🔴 PRIORITÉ IMMÉDIATE — Refactoring (avant toute feature)

**Voir [RefactoringSteps.md](RefactoringSteps.md) pour le plan complet et la méthode worktree.**

| Phase | Objectif | Impact | Effort |
|-------|---------|--------|--------|
| R1 | Supprimer ~600 lignes de code mort (`_backup_specialists.py`, `engine.py`, stubs vides) | Clarté immédiate | 1–2h |
| R2 | Découper `orchestrator.py` (976 lignes) en 4 modules `pipelines/` | Testabilité | 4–6h |
| R3 | Centraliser la config (WACC, LBO seuils, IC scoring) dans `config.py` | Maintainabilité | 2–3h |
| R4 | Ajouter `fetch_by_name()` à SEC EDGAR (nom→CIK via EDGAR search); activer clé Crunchbase | Qualité data | 2–3h |
| R5 | Tests agents + providers + exporters (17 tests → 40+) | Fiabilité | 3–4h |

---

## ⚠️ DATA PROVIDERS — État réel (2026-04)

| Source | Pays | Revenue | Statut |
|--------|------|---------|--------|
| **yfinance** | Global | ✅ Vérifié (public seulement) | ✅ Actif |
| **Pappers** | 🇫🇷 | ✅ Vérifié RNCS/INPI | ⚠️ `PAPPERS_API_KEY` ~€30/mois |
| **recherche-entreprises.api.gouv.fr** | 🇫🇷 | ❌ Secteur seulement | ✅ Gratuit, no auth |
| **Companies House** | 🇬🇧 | ⚠️ Best-effort XBRL | ⚠️ `COMPANIES_HOUSE_API_KEY` (gratuit) |
| **Bundesanzeiger** | 🇩🇪 | ⚠️ Best-effort HTML | ✅ Gratuit, no auth |
| **BORME** | 🇪🇸 | ❌ Existence seulement | ✅ Gratuit, no auth |
| **KVK** | 🇳🇱 | ❌ Secteur seulement | ⚠️ `KVK_API_KEY` (gratuit) |
| **SEC EDGAR** | 🇺🇸 | ✅ Revenue 10-K via XBRL (ticker) | ✅ Actif — pas de `fetch_by_name`, US public seulement |
| **Crunchbase** | Global | ⚠️ Revenue range estimé | ⚠️ `CRUNCHBASE_API_KEY` (gratuit 200 req/j, data.crunchbase.com) |
| **Bloomberg** | Global | ✅ Tout | ⬜ Stub, `BLOOMBERG_API_KEY` |
| **Capital IQ** | Global | ✅ Tout | ⬜ Stub, credentials requis |

**Conséquence** : pour les sociétés US privées, SEC EDGAR ne couvre que les publiques (ticker requis). Pour UK+NL, ajouter les clés gratuites. Crunchbase couvre les startups si clé activée.

---

## 🟡 PRIORITÉ 1 — Output polish

### PPT Goldman-quality
- Vrais graphiques python-pptx : bar chart football field horizontal, courbe DCF projetée
- Slide executive summary 1-pager (recommandation, EV, IC score, 3 bullets)

### Excel 3-statement
- P&L + Bilan + Cash Flow liés (modèle intégré)
- Onglets : Assumptions / P&L / BS / CF / DCF / LBO / Scenarios / Sensitivities

---

## 🟡 PRIORITÉ 2 — Transaction comps réels

Actuellement : multiple sectoriel moyen par défaut.
Cible : `TransactionCompsAgent` scrape press releases M&A (PR Newswire, BusinessWire) → NLP → base JSON locale mise à jour à chaque run. Acquéreur, cible, secteur, EV, multiple, date.

---

## 🟡 PRIORITÉ 3 — Opportunity Screening amélioré

- Score de pertinence des cibles vs brief client (taille, géo, secteur, stade)
- Comparaison à une société de référence ("trouve-moi des cibles comme Veeva")
- Shortlist scorecard PPT : tableau cibles avec score, EV, IC score, statut, next step

---

## 🟢 PRIORITÉ 4 — Productisation SaaS

- Frontend Next.js minimal : search bar → memo + football field + export PPT/Excel one-click
- Cache persistent (fichier JSON, TTL configurable)
- Multi-user / API keys via FastAPI auth Bearer (déjà en place)

---

## ⚠️ RÈGLE NON NÉGOCIABLE

**Le LLM ne produit jamais les chiffres de valorisation (EV, WACC, multiples).**

Hiérarchie des sources :
1. Bloomberg / CapIQ → 2. yfinance / SEC EDGAR → 3. EU Registries → 4. Crunchbase
5. Private triangulation → 6. LLM fallback (revenue uniquement, taggé `estimated`)

Chaque métrique est taguée `[verified]` / `[estimated]` / `[inferred]` dans les outputs.
