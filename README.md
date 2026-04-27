# Gold Roger — Roadmap Produit

## ✅ ÉTAT ACTUEL (Phase 1 COMPLÉTÉE — Avril 2025)

Gold Roger est un moteur de valorisation institutionnel équipé d'une couche LLM pour l'analyse qualitative.

### Ce qui fonctionne aujourd'hui :

✔ Données financières vérifiées via **yfinance** (revenus, marges, beta, market cap, net debt)  
✔ WACC dérivé par **CAPM** (β réel, Rf=4.5%, ERP=5.5%) — plus de défaut hardcodé  
✔ DCF institutionnel : FCFF = EBITDA(1-T) + D&A×T - CapEx - ΔNWC (NWC incrémental)  
✔ **Trading comps** ancrées au EV/EBITDA de marché en temps réel  
✔ **Multiples sectoriels** calibrés (20+ secteurs, EV/EBITDA + EV/Revenue + terminal growth)  
✔ Recommandation **BUY / HOLD / SELL** déterministe vs market cap actuel  
✔ Analyse de sensibilité WACC × terminal growth (matrice 5×5)  
✔ Confiance des données taguée : `verified` / `estimated` / `inferred`  
✔ Exports Excel (5 feuilles) + PowerPoint institutionnel  
✔ API FastAPI + CLI + orchestration multi-agents  

### Architecture des données (pipeline actuel) :

```
1. resolve_ticker()         → Yahoo Finance search → ticker
2. fetch_market_data()      → yfinance → MarketData (verified)
3. LLM agents               → analyse qualitative uniquement
4. ValuationService         → moteur déterministe pur Python
5. BUY/HOLD/SELL            → EV blended − Net Debt / Shares vs prix actuel
6. Thesis agent             → synthèse LLM sur chiffres vérifiés
```

---

## 🎯 OBJECTIF FINAL

Gold Roger = **AI Investment Banking Analyst OS**

Capable de :
- Valoriser une société en < 30 secondes avec une qualité PE/IB
- Produire un memo d'investissement type McKinsey
- Générer un deck automatiquement (Excel + PPT)
- Sourcer des deals comme un fonds VC/PE
- Fonctionner en production sans crash

---

## 🔜 PHASE 2 — PRÉCISION DE VALORISATION (PRIORITÉ ACTUELLE)

### 2.1 Estimations forward (analystes) pour remplacer le CAGR historique
**Problème** : Le DCF utilise le CAGR historique des revenus. Pour des sociétés premium (AAPL, MSFT), cela sous-estime la valeur car le marché price in des estimations forward.  
**Solution** : Intégrer les consensus d'analystes (yfinance `earningsEstimate`, `revenueEstimate`)

### 2.2 Valorisation P/E et P/B pour les financières
**Problème** : Le framework EV/EBITDA ne fonctionne pas pour les banques et assureurs.  
**Solution** : Détecter le secteur `Financials/Banking` → switcher vers multiples P/E et P/B

### 2.3 Revenus projetés par segment (pour conglomérats)
**Solution** : SOTP (Sum-of-the-Parts) — stub `valuation/sotp.py` existe, à implémenter

### 2.4 LBO model déterministe
**Problème** : `valuation/lbo.py` est un stub vide.  
**Solution** : Modèle LBO avec leverage schedule, debt paydown, IRR computation

---

## 🔜 PHASE 3 — DEAL SOURCING INSTITUTIONNEL

Une fois la valorisation parfaitement fiable :

- Critères acquéreur → screening automatique de cibles
- Scoring IC déterministe (stratégie / synergies / risque / LBO / valorisation)
- Ranking pipeline targets
- Feasibility LBO automatique
- Qualité PitchBook — génération pipeline deal

---

## 🔜 PHASE 4 — PRODUIT SAAS

- Interface Next.js (company search → instant memo)
- Caching Mistral + yfinance
- Multi-user / API keys
- Export automatique (Excel, PPT)
- Logging structuré (request_id, agent, raw/parsed output)

---

## 🏗 ARCHITECTURE CIBLE

```
Real Data Layer (yfinance / Bloomberg / SEC)
         ↓
 MarketData (verified, tagged)
         ↓
 LLM Layer (qualitative only — NO financial numbers)
         ↓
 Pydantic Validation (strict)
         ↓
 ValuationService (pure Python, deterministic)
   ├── DCF (CAPM WACC, real beta, real margins)
   ├── Trading Comps (market-implied + sector tables)
   ├── Transaction Comps (sector EV/Revenue)
   └── Blended EV → BUY/HOLD/SELL
         ↓
 M&A Scoring Engine (IC logic, LBO feasibility)
         ↓
 Export Layer (Excel / PPT / API)
```

---

## ⚠️ RÈGLE ABSOLUE

**Le LLM ne produit JAMAIS de chiffres financiers finaux.**  
Il est utilisé uniquement pour :
- Identifier le ticker d'une société
- Analyser le positionnement qualitatif
- Synthétiser la thèse d'investissement sur des chiffres vérifiés

---

## 🏁 DEFINITION OF DONE — V1.0

✔ 0 crash CLI  
✔ 0 fallback silencieux sur données financières clés  
✔ WACC dérivé par CAPM sur données réelles  
✔ DCF stable et défendable (PE associate level)  
✔ BUY/HOLD/SELL fiable vs market cap  
✔ M&A scoring cohérent et IC-grade  
✔ Exports fiables (Excel + PPT)  
✔ Forward estimates intégrés  
