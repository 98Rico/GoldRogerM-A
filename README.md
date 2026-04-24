# Gold Roger

Plateforme d’analyse financière et de valorisation d’entreprises pilotée par IA.

À partir d’un nom de société (ou ticker) et d’un type (`public`/`private`), Gold Roger exécute 5 agents spécialisés en séquence, agrège leurs sorties dans des modèles Pydantic, puis peut exporter :
- un fichier Excel (DCF dynamique + comps + sensibilité + dashboard),
- un deck PowerPoint (investment-grade),
- une API REST (FastAPI) pour automatiser l’orchestration.

---

## 🚀 Modes disponibles (NOUVEAU)

Gold Roger fonctionne maintenant en **3 modes produits distincts** :

### 📊 1. Equity Analyst (mode par défaut)
Analyse financière complète :
- fondamentaux
- marché
- projections financières
- valuation DCF
- investment thesis

👉 Sortie : `AnalysisResult`

---

### 🤝 2. M&A Analyst
Pipeline complet de deal-making :
- deal sourcing (identification cibles)
- strategic fit (synergies + structure)
- due diligence (risques + red flags)
- execution plan (deal roadmap)
- LBO view (IRR + structure capital)

👉 Sortie : `MAResult`

---

### 🔎 3. Opportunity Sourcing (NOUVEAU)
Mode “fund / deal discovery” :
- screening de marché
- identification d’opportunités
- génération de pipeline de cibles
- scoring stratégique

👉 Objectif : remplacer le travail de sourcing d’un analyste PE / VC

---

## ⚙️ Architecture IA + Finance (MAJEUR UPDATE)

Le système est maintenant **hybride IA + moteur financier déterministe** :

### 🧠 LLM (agents)
- collecte data
- hypothèses financières
- narration (thesis, market, risks)

### 📉 Engine Python (ValuationService)
- DCF déterministe
- calcul free cash flows
- terminal value (Gordon Growth)
- enterprise value

👉 séparation claire :
> LLM = intelligence qualitative  
> Python = vérité financière

---

### 🔥 Nouveau pipeline valuation


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

## Fonctionnalités

- **Orchestration multi-agents (5 étapes)** : fondamentaux → marché → modèle financier → valorisation → thèse.
- **Web search intégré** via un tool `web_search` (scraping Yahoo Finance + DuckDuckGo HTML).
- **Sortie structurée** : `AnalysisResult` (Pydantic).
- **Valuation hybride (NOUVEAU)** :
  - LLM pour hypothèses
  - Python pour DCF deterministic
- **Traçabilité** : chaque brique peut retourner `sources` (titres + URLs)
- **Exports** :
  - Excel : `goldroger/exporters/excel.py` (openpyxl)
  - PowerPoint : `goldroger/exporters/pptx.py` (python-pptx)
- **API REST** : `goldroger/api.py` (FastAPI)
- **CLI Rich** : `goldroger/cli.py`

---

## Stack

- Python 3.12
- `uv` (gestion env + deps)
- Mistral AI (`mistralai`)
- `httpx`, `python-dotenv`, `pydantic`, `rich`
- `openpyxl`, `python-pptx`
- `fastapi`, `uvicorn`

---

## Prérequis

- Python `>=3.12`
- Un compte Mistral + une clé API

---

## Installation

```bash
uv sync
Configuration
cp .env.example .env
MISTRAL_API_KEY=...
Usage CLI
Equity Analysis (par défaut)
uv run python -m goldroger.cli -c "LVMH" -t public
M&A Mode
uv run python -m goldroger.cli -c "Longchamp" -t private --mode ma --acquirer "LVMH"
Opportunity Sourcing Mode
uv run python -m goldroger.cli --mode sourcing --sector "luxury goods"
Sources / citations

Les agents peuvent retourner :

"Title — https://..."

Objectifs :

traçabilité des chiffres
distinction disclosed vs (est.)
auditabilité des assumptions
⚙️ API (FastAPI)
uv run uvicorn goldroger.api:app --reload
Modes API
{
  "company": "LVMH",
  "mode": "equity"
}
{
  "company": "Longchamp",
  "mode": "ma",
  "acquirer": "LVMH"
}
{
  "mode": "sourcing",
  "sector": "luxury goods"
}
Architecture du projet
goldroger/
├── agents/
│   ├── base.py
│   └── specialists.py
├── core/
│   └── valuation_service.py   # NEW (DCF engine)
├── orchestrator.py            # UPDATED (hybrid pipeline)
├── models/
├── exporters/
├── cli.py
└── api.py
Pipeline d’analyse (Equity)
DataCollector
SectorAnalyst
FinancialModeler
ValuationEngine (LLM assumptions)
ReportWriter
Pipeline M&A
DealSourcing
StrategicFit
DueDiligence
DealExecution
LBO
🚀 Exports
Excel
DCF model
comps
sensitivity
financials dashboard
PowerPoint
IB-style deck (16:9)
thesis + valuation + catalysts
🧠 Key improvements (IMPORTANT)
✔ séparation LLM vs Finance Engine
✔ DCF deterministic (Excel-grade accuracy)
✔ orchestration stable multi-mode
✔ 3 produits réels (Equity / M&A / Sourcing)
Roadmap
Opportunity Sourcing engine v2 (ranking + scoring)
Comparable automation (multiples scraping)
caching + performance layer
vector memory for deals
SaaS UI (Next.js dashboard)
⚠️ Notes private companies
données souvent estimées
marges / revenue = proxies sectoriels
sources prioritaires pour validation