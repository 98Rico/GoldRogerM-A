# NEXT STEPS — GOLD ROGER (PRODUCTION ROADMAP)

Objectif : transformer Gold Roger d’un prototype IA + finance en un produit vendable type
“AI Investment Analyst OS”.

---

## 1. STABILISATION CRITIQUE (BLOCKERS ACTUELS)

### 1.1 Zéro crash LLM
PROBLÈME:
- JSON parfois vide ('')
- parse_model fallback trop fréquent
- champs manquants (revenue_series, financials)

A FAIRE:
- ajouter retry automatique sur agents LLM (max 2-3 tentatives)
- validation stricte Pydantic AVANT parse_model
- reject response si JSON invalide

---

### 1.2 Normalisation des données financières
PROBLÈME:
- mismatch entre string / float / None
- revenue_series parfois absent
- Valuation engine fragile

A FAIRE:
- créer layer unique:
  FinancialNormalizer(financials_raw) -> FinancialsClean
- interdire toute string dans engine DCF
- standard interne = float only

---

### 1.3 Fix critique orchestrator (BUGS ACTUELS)
PROBLÈME:
- ValuationResult object not subscriptable
- mélange dict / object

A FAIRE:
- STANDARDISER RETURN TYPE:
  valuation_service.run_full_valuation MUST return:
  {
    "dcf": ...,
    "comps": ...,
    "transactions": ...,
    "blended": ...
  }

OU (meilleur):
- transformer en Pydantic ValuationResult propre

---

## 2. ARCHITECTURE CLEAN (IMPORTANT)

### 2.1 Séparation stricte des couches

LLM LAYER:
- DataCollectorAgent
- MarketAgent
- FinancialModelerAgent
- ThesisAgent

ENGINE LAYER:
- ValuationService (100% deterministic)
- no LLM inside

---

### 2.2 Contract system (NEW)
Créer:
class Contract:
    - input schema
    - output schema
    - validation rules

But:
=> aucun agent ne peut casser pipeline

---

## 3. ROBUSTESSE PRODUIT (VENTE READY)

### 3.1 Retry + fallback system
- retry LLM si JSON invalid
- fallback intelligent (sector averages)
- log all failures

---

### 3.2 Logging system (INDISPENSABLE)
Ajouter:
- request_id
- agent_name
- raw_response
- parsed_output
- error logs

---

### 3.3 Caching layer
- cache Mistral calls
- cache financials per company
- cache valuation outputs

---

## 4. VALUATION ENGINE (UPGRADE)

### 4.1 DCF upgrade
- explicit FCF model
- no implicit assumptions
- terminal value separate module

---

### 4.2 Sensitivity analysis (IMPORTANT FOR SALES)
Ajouter:
- WACC sensitivity
- growth sensitivity
- margin sensitivity

Output:
- matrix
- heatmap ready for Excel/PPT

---

## 5. M&A MODULE (PRODUCTIZATION)

### 5.1 IC Score system
Remplacer simple scoring par:
- Strategy score
- Synergy score
- Risk score
- LBO score
- Valuation score

Final:
IC_SCORE = weighted sum

---

### 5.2 Deal quality filter
- reject bad deals automatically
- rank pipeline targets

---

## 6. PRODUCTIZATION (CRUCIAL)

### 6.1 CLI → API first
CLI devient secondaire

PRIMARY:
FastAPI endpoints:
- /equity
- /ma
- /sourcing

---

### 6.2 Frontend (NEXT STEP MONEY)
Next.js dashboard:
- company search
- instant memo
- valuation chart
- export PPT button

---

## 7. DIFFERENTIATION (IMPORTANT BUSINESS)

Gold Roger doit devenir:

“AI Investment Banking Analyst OS”

capable de:

- remplacer analyst junior PE
- générer investment memo
- faire deal sourcing
- produire valuation models

---

## 8. ROADMAP FINAL (PRIORITÉ)

PHASE 1 (CRITIQUE):
- fix orchestrator crash
- unify ValuationResult
- enforce Pydantic strict mode
- eliminate None/"" outputs

PHASE 2:
- retry system LLM
- caching
- logging system

PHASE 3:
- sensitivity analysis DCF
- IC scoring M&A upgrade

PHASE 4:
- SaaS UI (Next.js)
- multi-user
- export automation

---

## RESULTAT FINAL ATTENDU

Gold Roger v1.0 =

- stable
- deterministic finance engine
- LLM only for reasoning
- production-grade valuation
- usable as SaaS backend for investors