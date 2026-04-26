# Gold Roger

Plateforme d’analyse financière et de valorisation d’entreprises pilotée par IA.

À partir d’un nom de société (ou ticker) et d’un type (`public`/`private`), Gold Roger exécute 5 agents spécialisés en séquence, agrège leurs sorties dans des modèles Pydantic, puis peut exporter :

- un fichier Excel (DCF dynamique + comps + sensibilité + dashboard),
- un deck PowerPoint (investment-grade),
- une API REST (FastAPI) pour automatiser l’orchestration.

---

# 🚀 Modes disponibles (NOUVEAU)

Gold Roger fonctionne maintenant en **3 produits distincts** :

---

## 📊 1. Equity Analyst (mode par défaut)

Analyse financière complète :

- fondamentaux
- marché
- projections financières
- valorisation DCF
- investment thesis

👉 Sortie : `AnalysisResult`

---

## 🤝 2. M&A Analyst

Pipeline complet de deal-making :

- deal sourcing (identification cibles)
- strategic fit (synergies + structure)
- due diligence (risques + red flags)
- execution plan (deal roadmap)
- LBO view (IRR + structure capital)

👉 Sortie : `MAResult`

---

## 🔎 3. Opportunity Sourcing (NOUVEAU)

Mode “fund / deal discovery” :

- screening de marché
- identification d’opportunités
- génération de pipeline de cibles
- scoring stratégique

👉 Objectif : remplacer le travail de sourcing d’un analyste PE / VC

---

# ⚙️ Architecture IA + Finance (MAJEUR UPDATE)

Le système est maintenant **hybride IA + moteur financier déterministe** :

---

## 🧠 LLM (Agents)

- collecte data
- hypothèses financières
- narration (thesis, market, risks)

---

## 📉 Engine Python (ValuationService)

- DCF déterministe
- calcul free cash flows
- terminal value (Gordon Growth)
- enterprise value

👉 séparation clé :

> LLM = intelligence qualitative  
> Python = vérité financière

---

## 🔥 Nouveau pipeline valuation


FinancialModeler (LLM)
↓
ValuationEngine (LLM assumptions)
↓
ValuationService (DCF engine Python)
↓
ValuationResult (EV + outputs)
↓
ReportWriter (LLM thesis)


---

# 📦 Fonctionnalités

- Orchestration multi-agents (5 étapes)
- Web search intégré (`web_search`)
- Sortie structurée (`Pydantic`)
- Valuation hybride :
  - LLM pour hypothèses
  - Python pour DCF deterministic
- Traçabilité des sources (`Title — URL`)
- Exports :
  - Excel (`openpyxl`)
  - PowerPoint (`python-pptx`)
- API REST (`FastAPI`)
- CLI Rich

---

# 🧰 Stack

- Python 3.12
- `uv`
- Mistral AI (`mistralai`)
- `httpx`
- `pydantic`
- `rich`
- `openpyxl`
- `python-pptx`
- `fastapi`, `uvicorn`

---

# ⚙️ Prérequis

- Python ≥ 3.12
- API Key Mistral

---

# 🚀 Installation

```bash
uv sync
Configuration
cp .env.example .env
MISTRAL_API_KEY=...
💻 Usage CLI
📊 Equity Analysis (default)
uv run python -m goldroger.cli -c "LVMH" -t public
🤝 M&A Mode
uv run python -m goldroger.cli -c "Longchamp" -t private --mode ma --acquirer "LVMH"
🔎 Opportunity Sourcing Mode
uv run python -m goldroger.cli --mode sourcing --sector "luxury goods"
🔗 Sources / citations

Les agents peuvent retourner :

"Title — https://..."

Objectifs :

traçabilité des chiffres
distinction disclosed vs (est.)
auditabilité des assumptions
⚙️ API (FastAPI)
uv run uvicorn goldroger.api:app --reload
API modes
Equity
{
  "company": "LVMH",
  "mode": "equity"
}
M&A
{
  "company": "Longchamp",
  "mode": "ma",
  "acquirer": "LVMH"
}
Sourcing
{
  "mode": "sourcing",
  "sector": "luxury goods"
}
🧱 Architecture du projet
goldroger/
├── agents/
│   ├── base.py
│   └── specialists.py
├── finance/
│   └── core/
│       └── valuation_service.py   # DCF engine
├── orchestrator.py                # multi-mode pipeline
├── models/
├── exporters/
├── cli.py
└── api.py
🔄 Pipelines
📊 Equity pipeline
DataCollector
SectorAnalyst
FinancialModeler
ValuationEngine (LLM assumptions)
ReportWriter
🤝 M&A pipeline
DealSourcing
StrategicFit
Due Diligence
DealExecution
LBO
📤 Exports
Excel
DCF model
comps
sensitivity
financial dashboard
PowerPoint
IB-style deck (16:9)
thesis + valuation + catalysts
🧠 Key improvements (IMPORTANT)

✔ séparation LLM vs Finance Engine
✔ DCF deterministic (Excel-grade logic)
✔ orchestration multi-mode stable
✔ 3 produits réels (Equity / M&A / Sourcing)

🗺️ Roadmap
Opportunity Sourcing v2 (ranking + scoring)
automated comps scraping
caching + performance layer
vector memory for deals
SaaS UI (Next.js dashboard)
⚠️ Notes — Private companies
données souvent estimées
marges / revenus = proxies sectoriels
sources prioritaires pour validation
🚀 Où on en est (IMPORTANT)

Gold Roger est maintenant un prototype avancé de plateforme d’analyse type banque d’affaires + PE tool, avec :

✅ Déjà fonctionnel
pipeline equity complet
pipeline M&A complet
valuation engine DCF deterministic
exports Excel / PPT
API FastAPI
orchestration multi-agents
❌ Problèmes actuels (critiques)
robustesse des outputs LLM encore fragile
mismatch entre modèles LLM et engine finance
certains champs financiers manquants (revenue_series)
parsing JSON parfois vide ('')
pas encore de caching ni retry intelligent
🎯 Objectif final

Transformer Gold Roger en :

📈 “AI Investment Analyst OS”

capable de :

analyser une entreprise en <30 secondes
produire un memo type McKinsey / PE
générer un deck investment-grade automatiquement
sourcer des deals comme un analyste VC/PE
🔧 Prochaines étapes prioritaires
stabilisation des data models (zéro crash LLM)
validation layer automatique (schema enforcement)
retry intelligent des agents
caching des calls Mistral
scoring M&A plus réaliste (IC memo style)

👉 Une fois ces étapes faites :
Gold Roger passe de prototype → produit vendable


---

# 💬 si tu veux next step

Je peux maintenant t’aider à faire le vrai saut important :

👉 **:contentReference[oaicite:0]{index=0}**

avec :
- validation automatique JSON
- retry agent intelligent
- schema enforcement strict
- zero None crash guarantee

C’est là que ton projet devient “vendable sérieusement”.