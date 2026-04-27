# Gold Roger — Roadmap Produit

## ✅ ÉTAT ACTUEL (Phases 1–4 complétées — Avril 2025)

Gold Roger est un moteur de valorisation institutionnel avec pipeline M&A complet, LBO engine, et couche data vérifiée.

### Ce qui fonctionne aujourd'hui :

**Valorisation (Phase 1 + 2)**
✔ Données financières vérifiées via **yfinance** (revenus, marges, beta, market cap, net debt)  
✔ **Estimations forward analystes** (consensus 1 an) utilisées en priorité sur le CAGR historique  
✔ WACC dérivé par **CAPM** — β réel, Rf=4.5%, ERP=5.5%, coût de la dette = intérêts / dette  
✔ **DCF institutionnel** : FCFF = EBITDA(1-T) + D&A×T - CapEx - ΔNWC (NWC incrémental)  
✔ **Path financier P/E + P/B** pour banques, assureurs, asset managers (détection automatique)  
✔ **Trading comps** ancrées au EV/EBITDA de marché live ; 20+ tables sectorielles de fallback  
✔ **BUY / HOLD / SELL** déterministe : EV blended − Net Debt / Shares vs prix actuel (±15%)  
✔ **Matrice de sensibilité** WACC × terminal growth (5×5)  
✔ **Confidence tagging** : `verified` / `estimated` / `inferred`  

**LBO Engine (Phase 2)**  
✔ Modèle LBO déterministe : entry EV → leverage → FCF sweep → exit → **IRR / MOIC**  
✔ Gates de faisabilité : leverage < 6.5x, IRR > 15% hurdle  
✔ Attaché automatiquement à chaque valorisation equity  

**SOTP (Phase 2)**  
✔ Sum-of-the-Parts : valorisation segment par segment avec holdco discount  
✔ Multiples sectoriels par segment  

**Deal Sourcing & M&A (Phase 3)**  
✔ `run_ma_analysis()` : pipeline M&A complet (sourcing → fit → DD → execution → LBO → IC scoring)  
✔ `run_pipeline()` : génération automatique d'un pipeline d'acquisitions avec IC scoring par cible  
✔ **IC Scoring institutionnel** (0–100) : 6 dimensions (stratégie, synergies, financial, LBO, intégration, risque)  
✔ Gates durs : si LBO / risk / financial < seuil minimum → NO GO automatique  
✔ Next steps générés automatiquement par niveau de recommandation  

**Infrastructure (Phase 4)**  
✔ **Cache TTL** (1h yfinance, 24h ticker) — plus de requêtes redondantes  
✔ **Logging structuré JSON-lines** : run_id, timings par step, WACC method, audit trail  
✔ Exports Excel (5 feuilles) + PowerPoint institutionnel  
✔ API FastAPI + CLI  

---

## Architecture des données (pipeline actuel)

```
┌─────────────────────────────────────────────────────────────┐
│              REAL DATA LAYER (Phase 0)                      │
│  resolve_ticker() → fetch_market_data() → MarketData        │
│  [verified: price, beta, margins, forward estimates, P/B]   │
└─────────────────────────┬───────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│              LLM LAYER (qualitative only)                   │
│  Fundamentals · Market · Assumptions · Thesis               │
│  M&A: Sourcing · Strategic Fit · DD · Execution             │
│  [NO financial numbers from LLM]                            │
└─────────────────────────┬───────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│         VALUATION ENGINE (pure Python, deterministic)       │
│                                                             │
│  Path A: Standard  → DCF + EV/EBITDA + EV/Revenue          │
│  Path B: Financial → DCF + P/E + P/B  (banks/insurers)     │
│  Path C: SOTP      → Segment × sector multiple             │
│                                                             │
│  WACC: CAPM (β réel) → LLM assumption → sector default     │
│  Growth: forward analyst → CAGR historique → sector default │
│  Blended EV (50/30/20) → BUY/HOLD/SELL                     │
│  LBO engine (toujours calculé, peut être INFEASIBLE)        │
└─────────────────────────┬───────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│              M&A SCORING (IC layer)                         │
│  6 dimensions · gates durs · STRONG BUY / BUY / WATCH / NO GO │
└─────────────────────────┬───────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│              EXPORT LAYER                                   │
│  Excel (DCF workbook + sensitivity) · PowerPoint · API      │
└─────────────────────────────────────────────────────────────┘
```

---

## 🎯 PROCHAINES PRIORITÉS

### À faire (court terme)

| # | Priorité | Item | Impact |
|---|----------|------|--------|
| 1 | HIGH | Pondération DCF=0% pour les financières (DCF tire le blended vers le bas pour les banques) | Précision JPM/GS |
| 2 | HIGH | Tests unitaires sur le valuation engine | Fiabilité prod |
| 3 | MED | Timeout par agent LLM (20-40s max) | Robustesse CLI |
| 4 | MED | Retry LLM si JSON invalide (max 2x) | Stabilité |
| 5 | MED | Scénarios DCF : bear/base/bull | Qualité output |
| 6 | LOW | SaaS UI Next.js | Produit |

### Banques / financières — fix précision
Le DCF donne une EV très basse pour les banques (EBITDA margin ≈ 0%) ce qui tire le blended vers le bas.  
Fix : détecter le secteur financier → mettre le poids DCF à 0%, redistribuer sur P/E (60%) + P/B (40%).

---

## ⚠️ RÈGLE ABSOLUE

**Le LLM ne produit JAMAIS de chiffres financiers finaux.**

Hiérarchie des sources :
1. `yfinance` (verified) — toujours prioritaire
2. Estimations LLM (estimated) — fallback pour sociétés privées
3. Defaults sectoriels (inferred) — dernier recours

Dans cet ordre. Toujours.

---

## 🏁 DEFINITION OF DONE — V1.0

✔ 0 crash CLI  
✔ Données financières vérifiées (yfinance) pour toute société publique  
✔ WACC CAPM sur données réelles  
✔ DCF + LBO stables et défendables  
✔ BUY/HOLD/SELL fiable vs market cap  
✔ M&A pipeline complet (sourcing → IC scoring)  
✔ Exports fiables Excel + PPT  
✔ Cache + logging en production  
