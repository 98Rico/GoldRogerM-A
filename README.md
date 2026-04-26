# Gold Roger — Next Steps (ROADMAP PRODUIT)

## 🚨 CONTEXTE ACTUEL

Gold Roger est un prototype avancé d’IA financière capable de :

✔ analyser une entreprise (Equity)
✔ faire du M&A (deal pipeline complet)
✔ générer une valuation DCF déterministe
✔ produire des exports Excel / PPT
✔ fonctionner en orchestration multi-agents

MAIS le système n’est PAS encore stable en production.

---

## ❌ PROBLÈMES CRITIQUES ACTUELS

### 1. LLM INSTABLE
- réponses parfois vides ("")
- JSON incomplet ou cassé
- champs manquants fréquents

### 2. DATA MODEL FRAGILE
- `Fundamentals` / `Financials` parfois invalides
- fallback systématique déclenché
- incohérences entre agents

### 3. VALUATION ENGINE OK MAIS DÉPENDANT DU INPUT
- DCF fonctionne
- MAIS dépend de données LLM non fiables

### 4. ORCHESTRATOR PAS ROBUSTE
- pas de retry
- pas de validation forte avant engine
- pas de recovery intelligent

---

## 🎯 OBJECTIF FINAL

Transformer Gold Roger en :

👉 AI Investment Analyst OS production-grade

Capable de :
- analyser une société en < 30 sec
- produire un memo type McKinsey / PE
- générer un deck d’investissement automatiquement
- sourcer des deals comme un fonds VC/PE
- fonctionner sans crash LLM

---

## 🔧 PRIORITÉ #1 — STABILITÉ DATA (CRITIQUE)

### À FAIRE EN PREMIER

#### 1. VALIDATION STRICTE Pydantic
- bloquer les objets incomplets
- forcer schema complet
- éviter fallback silencieux

#### 2. RETRY SYSTEM LLM
Si JSON invalide :
- retry 1x avec prompt corrigé
- retry 2x avec "STRICT JSON ONLY"

#### 3. GUARANTEE FIELDS
Toujours garantir :
- revenue_current
- ebitda_margin
- sector
- description

---

## ⚙️ PRIORITÉ #2 — ORCHESTRATION ROBUSTE

### Ajouter :

#### 1. STEP GUARDS
Avant chaque step :
- validation modèle
- fallback contrôlé
- logging debug propre

#### 2. TIMEOUT PAR AGENT
- éviter blocage CLI (actuellement lent)
- cap max 20–40s par agent

#### 3. PARALLELISATION (FUTUR)
- Market + Financials en parallèle

---

## 📉 PRIORITÉ #3 — VALUATION ENGINE PRO

### Améliorations :

- normalisation inputs stricte
- sanity checks (marges, growth)
- clamp values (WACC, growth)
- fallback macro sectoriel

---

## 🧠 PRIORITÉ #4 — M&A SCORING

Remplacer scoring actuel par :

- Investment Committee scoring model
- risk weighting réel
- synergy score breakdown
- LBO feasibility score

---

## 🚀 PRIORITÉ #5 — PERFORMANCE

### Ajouter :

- caching Mistral responses
- deduplication prompts
- memoization financials
- async agent execution (future)

---

## 📦 PRIORITÉ #6 — PRODUIT

### UX CLI / API :

- logs plus clean
- mode verbose / debug toggle
- export direct (Excel / PPT)

---

## 🧱 ARCHITECTURE CIBLE (FINAL)

LLM Layer
↓
Validated JSON Layer (STRICT)
↓
Pydantic Models (SAFE)
↓
Valuation Engine (PURE PYTHON)
↓
Scoring Engine (M&A / Equity)
↓
Export Layer (Excel / PPT / API)

---

## 🧠 VISION PRODUIT

Gold Roger doit devenir :

👉 "AI Equity Research + PE Deal Engine"

capable de remplacer :
- analyste equity research
- associate M&A
- junior VC sourcing

---

## ⚠️ CRITICAL RULE

Aucun output LLM ne doit atteindre le moteur financier
sans validation stricte.

---

## 🏁 DEFINITION OF DONE (VERSION 1.0)

✔ 0 crash CLI  
✔ 0 fallback silencieux  
✔ 100% JSON validé  
✔ retry intelligent actif  
✔ valuation stable  
✔ M&A scoring cohérent  
✔ export fiable  

---

## 🚀 NEXT IMPLEMENTATION STEP (IMMEDIATE)

1. ajouter retry system LLM
2. rendre parse_model strict fail-fast option
3. ajouter validation pre-engine
4. stabiliser Fundamentals + Financials
5. corriger orchestrator dependency chain

---

## 💬 RESULTAT ATTENDU

Une fois fait :

👉 Gold Roger devient vendable en B2B (fonds / PE / VC / banques)