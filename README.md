# Gold Roger — Moteur de Valorisation Institutionnel

## Architecture Globale

```
┌──────────────────────────────────────────────────────────────────┐
│                    DATA LAYER (pluggable)                         │
│  Bloomberg → Capital IQ → Refinitiv → yfinance → Crunchbase      │
│                          → SEC EDGAR                              │
│  DataRegistry: priority chain, auto-fallback, credential-gated   │
│  PeerFinder: 4-6 listed peers fetched live via yfinance           │
└────────────────────────┬─────────────────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────────────────┐
│                    LLM LAYER (qualitative only)                   │
│  Fundamentals · Market · Financials · Assumptions · Thesis        │
│  M&A: Sourcing · Strategic Fit · DD · Execution · LBO            │
│  PeerFinder: identifies comparable listed companies               │
│  [RULE: LLM never produces financial numbers used in valuation]  │
└────────────────────────┬─────────────────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────────────────┐
│              VALUATION ENGINE (pure Python, deterministic)        │
│                                                                   │
│  Path A: Standard  → DCF + EV/EBITDA comps + EV/Revenue tx       │
│  Path B: Financial → P/E + P/B  (banks, insurers, asset mgrs)    │
│  Path C: SOTP      → Segment × sector multiple                   │
│                                                                   │
│  Comps anchored to REAL peer multiples (not sector table avg)     │
│  WACC: CAPM (β réel) → LLM assumption → sector default           │
│  Growth: analyst forward estimate (fade curve 5Y) → CAGR → def.  │
│  Bear/Base/Bull: 3 full DCF scenarios with driver-level deltas    │
│  LBO engine: IRR/MOIC/feasibility (skipped for mega-caps >$500B) │
│  IC Scoring: 6 dimensions from agent outputs, not neutral 5/10   │
└────────────────────────┬─────────────────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────────────────┐
│                    EXPORT LAYER                                   │
│  PowerPoint (10 slides):                                          │
│    Title · Overview · Market · Financials · Valuation Summary     │
│    Football Field · Peer Comps · IC Score · Thesis · Risks        │
│  Excel: DCF workbook + sensitivity matrix                         │
│  API: FastAPI · CLI                                               │
└──────────────────────────────────────────────────────────────────┘
```

---

## Ce qui fonctionne (Phases 1–10)

### Données & Sources

| Source | Disponibilité | Données |
|--------|--------------|---------|
| **yfinance** | Toujours (gratuit) | Prix, beta, marges, EV, forward estimates |
| **SEC EDGAR** | Toujours (gratuit) | Revenus annuels US (10-K) |
| **Companies House** | Toujours (gratuit, 🇬🇧) | Comptes annuels, SIC, statut |
| **Infogreffe** | Toujours (gratuit, 🇫🇷) | CA déclaré, résultat, code NAF |
| **Handelsregister** | Toujours (gratuit, 🇩🇪) | Profil société, Bundesanzeiger best-effort |
| **KVK** | Toujours (gratuit, 🇳🇱) | Comptes, secteur SBI |
| **Registro Mercantil** | Toujours (gratuit, 🇪🇸) | Comptes annuels BORME |
| **Crunchbase** | Si `CRUNCHBASE_API_KEY` (freemium) | Revenus estimés, funding, headcount |
| **Web Search** | Toujours (DuckDuckGo) | Données privées, presse, rapports |
| **Bloomberg BLP** | Si `BLOOMBERG_API_KEY` | Tout (temps réel, privé, M&A comps) |
| **Capital IQ** | Si `CAPITALIQ_USERNAME` + `CAPITALIQ_PASSWORD` | M&A comps, transactions, private |
| **Refinitiv** | Si `REFINITIV_APP_KEY` | Équivalent Capital IQ |

Pour activer Bloomberg/CapIQ/Crunchbase : ajouter les variables dans `.env`.

> **Note** : Les taux de change EUR/GBP/CHF/CAD sont récupérés **live via yfinance** (`EURUSD=X`, etc.) avec fallback hardcoded si yfinance indisponible.

### Data Provider Registry

```python
from goldroger.data.registry import DEFAULT_REGISTRY

# Voir quelles sources sont actives
print(DEFAULT_REGISTRY.available_providers())  # ['yfinance', 'crunchbase', 'sec_edgar']

# Fetch avec fallback automatique
data = DEFAULT_REGISTRY.fetch("AAPL")  # essaie Bloomberg → CapIQ → yfinance → Crunchbase → EDGAR
```

### Peer Comparables (Phase 5)

Pour toute société (publique ou privée), le système identifie automatiquement 4–6 sociétés cotées comparables via LLM, puis récupère leurs vrais multiples de marché via yfinance.

- **Privées** : les multiples sectoriels codés en dur sont remplacés par de vrais comparables
- **Publiques** : les comps sont ancrées aux multiples réels du secteur live
- Les multiples peers (médiane P25/P75) alimentent directement le DCF et le football field

### Scénarios Bear / Base / Bull (Phase 5)

Chaque analyse produit 3 DCF complets avec des hypothèses indépendantes :

| Driver | Bear | Base | Bull |
|--------|------|------|------|
| Revenue growth delta | −5pp | 0 | +5pp |
| EBITDA margin delta | −200bps | 0 | +200bps |
| WACC delta | +150bps | 0 | −100bps |
| Terminal growth delta | −50bps | 0 | +50bps |
| Exit multiple factor | 0.80× | 1.0× | 1.20× |

Output : football field EV par méthode (DCF / Comps / Blended) × scénario.

### IC Scoring enrichi (Phase 5)

Les 6 dimensions sont maintenant dérivées des outputs agents, pas neutres à 5.0/10 :

| Dimension | Source |
|-----------|--------|
| Strategy | `strategic_fit.fit_score` (High=8.5, Med=6.5, Low=3.0) |
| Synergies | `strategic_fit.key_synergies` count + impact quality |
| Integration | `strategic_fit.integration_complexity` (inverse) |
| Risk | `due_diligence.red_flags` severity count |
| Financial | `upside_pct` from valuation engine |
| LBO | `lbo_output.irr` from deterministic engine |

### Private Company Handling

Pour une société privée :
1. **yfinance** → None (pas de ticker)
2. **Crunchbase** → revenue range estimé si `CRUNCHBASE_API_KEY` set
3. **Name Resolver** → `data/name_resolver.py` — résout le nom commercial vers l'identifiant correct par source (LLM one-shot + normalisation accents/suffixes légaux)
4. **EU Registries** → Infogreffe 🇫🇷, Companies House 🇬🇧, Handelsregister 🇩🇪 — chaque provider teste toutes les variantes du nom
5. **FinancialModelerAgent** → web search pour revenus/marges, taggé `estimated`
   - Si `revenue_current` toujours null après 2 tentatives → **revenue fallback** : appel LLM ciblé pour débloquer le football field
5. **PeerFinderAgent** → trouve 4–6 comparables cotés → multiples réels
6. **Valuation** :
   - Si revenus trouvés → DCF + comps peers réels + bear/base/bull
   - Si aucune donnée revenue → DCF/comps **omis** (affiché `N/A`), peer multiples de référence uniquement
   - **Jamais de valeurs placeholder** — `N/A` honnête plutôt que chiffres fabriqués
7. **Pas de BUY/HOLD/SELL** (pas de prix coté) → IC scoring M&A uniquement

### Opportunity Screening (M&A Pipeline)

Quand un client cherche des opportunités dans un secteur ou veut trouver des cibles comparables à une société de référence :

```bash
# Pipeline par secteur
uv run python -m goldroger.cli --mode pipeline --buyer "LVMH" --focus "Premium beauty brands Europe"

# Cibles comparables à une société de référence
uv run python -m goldroger.cli --mode pipeline --buyer "Salesforce" --focus "CRM middleware SaaS Series B Europe"
```

Le `SourcingAgent` identifie des cibles, puis `run_pipeline()` fait tourner une analyse M&A complète sur chaque cible avec IC scoring et football field.

### PowerPoint Output (10 slides)

1. **Title** — Nom, secteur, recommandation, implied value, upside
2. **Company Overview** — Business model, avantages compétitifs
3. **Market & Competition** — TAM, CAGR, concurrents, trends
4. **Financial Snapshot** — KPIs + projections
5. **Valuation Summary** — DCF / Comps / Transactions (table + conclusion)
6. **Football Field** — Bear/Base/Bull × méthode, ranges
7. **Peer Comparables** — Table des comparables réels avec médiane
8. **IC Score Breakdown** — 6 dimensions + rationale + next steps
9. **Investment Thesis** — Thesis + Bull/Base/Bear narrative
10. **Catalysts & Risks** — Catalysts + risques clés

---

## Commandes

Tous les exports (PPT + Excel) sont automatiquement sauvegardés dans un sous-dossier horodaté :
`outputs/<Company>_<YYYYMMDD_HHMMSS>/`

### 1. Equity publique — NVIDIA

```bash
uv run python -m goldroger.cli --company "NVIDIA" --excel --pptx
```

Output : `outputs/NVIDIA_20260427_143022/NVIDIA_analysis.xlsx` + `NVIDIA_analysis.pptx`

### 2. Société privée — Longchamp

```bash
uv run python -m goldroger.cli --company "Longchamp" --type private --excel --pptx
```

Output : `outputs/Longchamp_20260427_143022/Longchamp_analysis.xlsx` + `Longchamp_analysis.pptx`

### 3. Pipeline sourcing — Carlyle / B2B SaaS Europe

```bash
# Standard (avec web search, ~5 min)
uv run python -m goldroger.cli \
  --mode pipeline \
  --company "sourcing" \
  --buyer "Carlyle Group" \
  --focus "European B2B SaaS, ARR €5M–€50M, founder-led" \
  --pptx

# Rapide pour démo (sans web search, ~1 min)
uv run python -m goldroger.cli \
  --mode pipeline \
  --company "sourcing" \
  --buyer "Carlyle Group" \
  --focus "European B2B SaaS, ARR €5M–€50M, founder-led" \
  --pptx --quick
```

Output : `outputs/sourcing_20260427_143022/pipeline_deck.pptx`
Retourne 3 cibles avec IC scoring, football field, et rationale de valorisation.

### Autres exemples

```bash
# Banque (path P/E + P/B automatique)
uv run python -m goldroger.cli --company "JPM" --excel --pptx

# M&A (acquéreur → cible spécifique)
uv run python -m goldroger.cli --company "Figma" --mode ma --acquirer "Adobe" --pptx
```

---

## Vision — Remplacer l'analyste M&A

L'objectif est de produire une analyse meilleure qu'un analyste M&A humain sur toutes les tâches courantes : sourcing, valorisation, due diligence, memo IC, PPT, Excel.

**Ce que l'outil bat déjà un analyste sur** : vitesse, cohérence, couverture systématique, zéro biais d'ancrage, disponibilité 24/7.

**Les trois gaps restants** (voir NextSteps) :
1. **Private company data depth** — triangulation depuis 6–8 signaux (headcount, funding, web traffic, press, transaction comps) — moteur construit, à enrichir
2. **Transaction comps sans CapIQ** — scraping de press releases M&A pour ancrer les valorisations sur de vraies transactions
3. **Output polish** — vrais graphiques PPT (bar chart, courbe DCF), Excel 3-statement, executive summary 1-pager

## Connectivité Data Dynamique

Le registre `DataRegistry` est conçu pour que n'importe quelle source soit connectable en 30 minutes sans toucher au moteur de valorisation. Priorité d'exécution : Bloomberg → CapIQ → Refinitiv → yfinance → Crunchbase → Companies House → Infogreffe → Handelsregister → EDGAR.

**Name Resolver** : pour les sociétés privées, `data/name_resolver.py` traduit automatiquement le nom commercial vers l'identifiant correct par source (raison sociale Infogreffe, registered name Companies House, slug Crunchbase, etc.) via LLM one-shot + normalisation accents/suffixes légaux.

**Europe-first, global-ready** : Companies House 🇬🇧, Infogreffe 🇫🇷, Handelsregister 🇩🇪, KVK 🇳🇱, Registro Mercantil 🇪🇸 intégrés. Architecture identique pour OpenCorporates (140+ pays).

Sources premium (stubs prêts) : PitchBook, Mergermarket, Dealogic, Preqin.

## LLM-Agnostique

L'outil tourne sur **Mistral (gratuit)** par défaut — aucune carte bancaire requise. Changer de modèle en une commande, sans toucher au code :

| Provider | Coût | Modèles utilisés | Commande d'install |
|----------|------|------------------|--------------------|
| **Mistral** (défaut) | Gratuit | mistral-small / mistral-large | _(déjà installé)_ |
| **Anthropic** | Payant | claude-haiku / claude-sonnet | `uv add --group anthropic anthropic` |
| **OpenAI** | Payant | gpt-4o-mini / gpt-4o | `uv add --group openai openai` |

```bash
# Via .env (persistant)
LLM_PROVIDER=mistral      # gratuit, défaut
LLM_PROVIDER=anthropic    # Claude — meilleure qualité thesis/DD
LLM_PROVIDER=openai       # GPT-4o

# Via CLI (override pour un run uniquement)
uv run python -m goldroger.cli --company "NVIDIA" --llm claude
uv run python -m goldroger.cli --company "NVIDIA" --llm mistral
```

Si un provider n'est pas installé, le message d'erreur indique la commande exacte à lancer. Chaque provider utilise un modèle "small" pour les agents rapides et "large" pour les analyses longues (thesis, DD) — sans configuration supplémentaire.

## Règle Absolue

**Le LLM ne produit JAMAIS de chiffres financiers finaux.**

Hiérarchie des sources (dans l'ordre, toujours) :
1. **Providers premium** (Bloomberg, CapIQ) — si credentials
2. **yfinance / SEC EDGAR** — gratuit, sociétés cotées
3. **Crunchbase** — revenus estimés, startups/privées (freemium)
4. **Estimations LLM** (web search) — fallback sociétés privées, taggé `estimated`
5. **Defaults sectoriels** — dernier recours, taggé `inferred`

---

## Ajouter une source de données

Implémenter `DataProvider` :

```python
from goldroger.data.providers.base import DataProvider
from goldroger.data.fetcher import MarketData

class MyProvider(DataProvider):
    name = "my_source"
    requires_credentials = True

    def is_available(self):
        return bool(os.getenv("MY_API_KEY"))

    def fetch(self, ticker: str) -> MarketData | None:
        # ... fetch and return MarketData
        ...

# Enregistrer en tête de liste
from goldroger.data.registry import DEFAULT_REGISTRY
DEFAULT_REGISTRY.register(MyProvider())
```

---

## Performance

Temps typiques par run (après optimisations v8) :

| Scénario | Durée estimée |
|----------|--------------|
| Equity publique (NVIDIA) | ~5–10 min |
| Société privée (Sézane) | ~1–2 min |
| Pipeline sourcing `--quick` | ~1–2 min |
| Pipeline sourcing standard | ~4–6 min |

Agents qui font des recherches web (cap 3 rounds) : Fundamentals, Market Analysis, FinancialModeler (privées), PeerFinder.
Agents sans web search (réponse directe) : ValuationAssumptions, ReportWriter, LBO, DealExecution.

## Tests

```bash
uv run python -m pytest tests/ -v
```

20 tests couvrant : WACC CAPM, DCF projections forward, LBO IRR/MOIC/feasibility, scénarios Bear/Base/Bull.

---

## Definition of Done — V1.0

✔ 0 crash CLI  
✔ Données financières vérifiées (yfinance) pour toute société publique  
✔ WACC CAPM sur données réelles  
✔ DCF + LBO stables et défendables  
✔ Bear/Base/Bull football field  
✔ Peer comparables réels (pas de sector table hardcodé)  
✔ IC scoring dérivé des agents (pas 5.0/10 neutral)  
✔ BUY/HOLD/SELL fiable vs market cap  
✔ M&A pipeline complet (sourcing → IC scoring)  
✔ PPT 10 slides institutionnel  
✔ Exports fiables Excel + PPT  
✔ Cache + logging en production  
✔ Architecture data pluggable (Bloomberg/CapIQ prêts à brancher)  
✔ Crunchbase intégré (freemium, privées)  
✔ 20 tests unitaires valuation engine  
✔ LLM-agnostique (Mistral défaut gratuit, Anthropic/OpenAI en option)  
✔ EU registries pour sociétés privées (Infogreffe, Companies House, Handelsregister)  
✔ Name Resolver — identifiants légaux corrects par source  
✔ No placeholder values — N/A honnête plutôt que données fabriquées  
✔ Football field fonctionnel pour sociétés privées (revenue fallback)  
✔ KVK 🇳🇱 + Registro Mercantil 🇪🇸 intégrés  
✔ Fuzzy name matching (difflib) dans Infogreffe + Companies House  
✔ SOTP auto-detect pour conglomérats (segments → compute_sotp)  
✔ Scenario narratives Bear/Base/Bull  
✔ Live FX rates via yfinance  
✔ Target price (per-share) séparé de l'Implied EV — zéro ambiguïté unités  
✔ Mega-cap : tx comps exclus (poids 0) pour MCap >$500B  
✔ Private companies : recommandation ATTRACTIVE / NEUTRAL / EXPENSIVE  
✔ Revenue lock dans thesis agent — zéro contradiction inter-sections  
