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

## Ce qui fonctionne (Phases 1–5)

### Données & Sources

| Source | Disponibilité | Données |
|--------|--------------|---------|
| **yfinance** | Toujours (gratuit) | Prix, beta, marges, EV, forward estimates |
| **SEC EDGAR** | Toujours (gratuit) | Revenus annuels US (10-K) |
| **Crunchbase** | Si `CRUNCHBASE_API_KEY` (freemium) | Revenus estimés, funding, headcount (startups/privées) |
| **Web Search** | Toujours (DuckDuckGo) | Données privées, presse, rapports |
| **Bloomberg BLP** | Si `BLOOMBERG_API_KEY` | Tout (temps réel, privé, M&A comps) |
| **Capital IQ** | Si `CAPITALIQ_USERNAME` + `CAPITALIQ_PASSWORD` | M&A comps, transactions, private |
| **Refinitiv** | Si `REFINITIV_APP_KEY` | Équivalent Capital IQ |

Pour activer Bloomberg/CapIQ/Crunchbase : ajouter les variables dans `.env`.

> ⚠️ **Note** : Les taux de change EUR/GBP/CHF/CAD sont actuellement **codés en dur** dans `valuation_service.py` (`_FX` dict). Ils doivent être remplacés par des taux live via yfinance (`EURUSD=X`, `GBPUSD=X`, etc.) pour des analyses précises — voir NextSteps 3.1.

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
3. **FinancialModelerAgent** → web search pour revenus/marges (presse, rapports, etc.)
   - Autorisé à estimer si pas de données vérifiées, taggé `estimated`
4. **PeerFinderAgent** → trouve 4–6 comparables cotés → multiples réels
5. **Valuation** : DCF avec WACC sectoriel + comps peers réels + bear/base/bull
6. **Pas de BUY/HOLD/SELL** (pas de prix coté) → IC scoring M&A uniquement

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

```bash
# Analyse equity publique
uv run python -m goldroger.cli --company "NVIDIA"

# Banque (path P/E + P/B)
uv run python -m goldroger.cli --company "JPM"

# Société privée
uv run python -m goldroger.cli --company "Longchamp" --type private

# Avec export PPT
uv run python -m goldroger.cli --company "LVMH" --pptx --outdir outputs/

# M&A (acquéreur → cible)
uv run python -m goldroger.cli --company "Figma" --mode ma --acquirer "Adobe"

# Pipeline d'acquisitions (secteur)
uv run python -m goldroger.cli --mode pipeline --buyer "LVMH" --focus "Premium beauty brands Europe"
```

---

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
