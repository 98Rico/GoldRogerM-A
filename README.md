# Gold Roger

Plateforme d’analyse financière et de valorisation d’entreprises pilotée par IA.

À partir d’un nom de société (ou ticker) et d’un type (`public`/`private`), Gold Roger exécute 5 agents spécialisés en séquence, agrège leurs sorties dans des modèles Pydantic, puis peut exporter :
- un fichier Excel (DCF dynamique + comps + sensibilité + dashboard),
- un deck PowerPoint (investment-grade),
- une API REST (FastAPI) pour automatiser l’orchestration.

## Fonctionnalités

- **Orchestration multi-agents (5 étapes)** : fondamentaux → marché → modèle financier → valorisation → thèse.
- **Web search intégré** via un tool `web_search` (scraping Yahoo Finance + DuckDuckGo HTML).
- **Sortie structurée** : `AnalysisResult` (Pydantic).
- **Traçabilité** : chaque brique peut retourner `sources` (titres + URLs) pour justifier les chiffres/affirmations.
- **Exports** :
  - Excel : `goldroger/exporters/excel.py` (openpyxl)
  - PowerPoint : `goldroger/exporters/pptx.py` (python-pptx)
- **API REST** : `goldroger/api.py` (FastAPI)
- **CLI Rich** : `goldroger/cli.py`

## Stack

- Python 3.12
- `uv` (gestion env + deps)
- Mistral AI (`mistralai`)
- `httpx`, `python-dotenv`, `pydantic`, `rich`
- `openpyxl` (Excel), `python-pptx` (PowerPoint)
- `fastapi`, `uvicorn` (API)

## Prérequis

- Python `>=3.12`
- Un compte Mistral + une clé API

## Installation

Avec `uv` (recommandé) :

```bash
uv sync
```

> Note: selon ta configuration macOS, `uv` peut utiliser un cache non accessible. Si tu vois une erreur de permissions sur `~/.cache/uv`, tu peux lancer ponctuellement avec `UV_CACHE_DIR=/tmp/uv-cache`.

## Configuration

Créer un fichier `.env` à la racine (ne pas committer) :

```bash
cp .env.example .env
```

Puis renseigner :

```env
MISTRAL_API_KEY=...
```

Le repo ignore déjà `.env` via `.gitignore`.

## Usage CLI

### Analyse complète (JSON + affichage console)

```bash
uv run python -m goldroger.cli -c "LVMH" -t public
```

### Sources / citations

Les réponses des agents incluent un champ `sources` (liste de chaînes) quand c’est possible.  
Format attendu : `"Title — https://..."`.

Objectif :
- permettre de **retracer** les TAM/CAGR, revenus estimés, multiples, news/catalysts,
- distinguer explicitement les chiffres **disclosed** vs **(est.)**.

### Mode M&A (deal sourcing → fit → diligence → execution → LBO)

```bash
uv run python -m goldroger.cli -c "Longchamp" -t private --mode ma --acquirer "LVMH" --objective "expand in premium leather goods"
```

### Export Excel / PowerPoint

```bash
uv run python -m goldroger.cli -c "LVMH" -t public --excel --pptx --outdir outputs
```

> Note: les exports Excel/PPTX sont actuellement disponibles uniquement pour `--mode equity`.

### Sauvegarde du JSON

```bash
uv run python -m goldroger.cli -c "LVMH" -t public -o outputs/lvmh.json
```

## Usage API (FastAPI)

Démarrer le serveur :

```bash
uv run uvicorn goldroger.api:app --reload
```

### UI (test)

Une UI minimaliste est servie par l’API :
- Ouvre `http://127.0.0.1:8000/ui`
- Le bouton **Run** appelle `POST /analyze` et affiche le JSON
- Les exports (Excel/PPTX) sont téléchargeables via des liens (servis par `GET /files`)

Endpoints :

- `GET /health` → `{ "ok": true }`
- `GET /ui` → page HTML de test
- `GET /files?path=outputs/...` → téléchargement d’artefacts (limité à `./outputs`)
- `POST /analyze` → lance l’orchestrateur et renvoie le résultat (et éventuellement les chemins d’exports)

Exemple de payload :

```json
{
  "company": "LVMH",
  "company_type": "public",
  "mode": "equity",
  "export_excel": true,
  "export_pptx": true,
  "output_dir": "outputs"
}
```

Exemple de payload (mode M&A) :

```json
{
  "company": "Longchamp",
  "company_type": "private",
  "mode": "ma",
  "acquirer": "LVMH",
  "objective": "expand in premium leather goods"
}
```

## Architecture du projet

```
./
├── goldroger/
│   ├── agents/
│   │   ├── base.py            # boucle agent + retries + tool web_search
│   │   └── specialists.py     # 5 agents spécialisés
│   ├── models/__init__.py     # modèles Pydantic (AnalysisResult, etc.)
│   ├── utils/json_parser.py   # parsing JSON robuste (fallbacks)
│   ├── orchestrator.py        # séquence les 5 agents et assemble AnalysisResult
│   ├── exporters/
│   │   ├── excel.py           # export XLSX (DCF dynamique)
│   │   └── pptx.py            # export PPTX (deck)
│   ├── cli.py                 # CLI Rich
│   └── api.py                 # FastAPI
├── pyproject.toml             # deps + metadata
└── .env.example
```

### Pipeline d’analyse (5 agents)

1. **DataCollectorAgent** (`DataCollector`)  
   Collecte : description, business model, avantages, risques, infos société.
2. **SectorAnalystAgent** (`SectorAnalyst`)  
   Marché : TAM, CAGR, segment, tendances, compétiteurs.
3. **FinancialModelerAgent** (`FinancialModeler`)  
   Données financières + projections (3 ans).
4. **ValuationEngineAgent** (`ValuationEngine`)  
   Valorisation : DCF + multiples comps + transactions.
5. **ReportWriterAgent** (`ReportWriter`)  
   Thèse d’investissement : bull/base/bear, catalysts, questions clés.

Le tout est orchestré par `goldroger/orchestrator.py` et validé/parsé via `goldroger/utils/json_parser.py`.

### Pipeline M&A (5 agents)

1. **DealSourcingAgent** (`DealSourcing`) — deal sourcing / pipeline building  
2. **StrategicFitAgent** (`StrategicFit`) — strategic fit + synergies + structure  
3. **DueDiligenceAgent** (`DueDiligence`) — diligence plan + red flags  
4. **DealExecutionAgent** (`DealExecution`) — workplan + materials + negotiations  
5. **LBOAgent** (`LBO`) — LBO feasibility + IRR ranges (high-level)  

Point d’entrée : `goldroger/orchestrator.py:run_ma_analysis`.

## Exports

### Excel (openpyxl)

Le fichier généré contient typiquement :
- `Dashboard` (KPIs + recommandation),
- `DCF Model` (hypothèses éditables + formules),
- `Comparables`,
- `Sensitivity` (matrice WACC × TGR),
- `Financials` (income statement + projections).

Point d’entrée : `goldroger/exporters/excel.py:generate_excel`.

### PowerPoint (python-pptx)

Deck 16:9 avec slides :
- Title, Company Overview, Market & Competition, Financial Snapshot,
- Valuation Summary, Investment Thesis, Catalysts & Risks.

Point d’entrée : `goldroger/exporters/pptx.py:generate_pptx`.

## Dépannage (FAQ)

### `ImportError: cannot import name 'Mistral' from 'mistralai'`

Le SDK récent expose `Mistral` via `mistralai.client`. Le code du projet utilise :
- `from mistralai.client import Mistral`

### `Missing MISTRAL_API_KEY`

Ajoute `MISTRAL_API_KEY` dans `.env` ou exporte-la dans ton shell :

```bash
export MISTRAL_API_KEY="..."
```

## Private companies (notes)

Pour les boîtes privées, il est normal d’avoir plus de champs en **N/A** : beaucoup de données (revenus, marges, cash-flow) ne sont pas publiques.

Bonnes pratiques :
- Utiliser `-t private` : `uv run python -m goldroger.cli -c "Longchamp" -t private`
- Ajouter des infos dans le nom (si besoin) : ex. `-c "Longchamp luxury leather goods Paris"`
- Privilégier des **estimations** explicites quand il n’y a pas de chiffres officiels (le modèle essaie déjà de les marquer en `(est.)`).
- Vérifier les champs `sources` pour comprendre d’où viennent les estimations et ajuster le “working” si nécessaire.

### Erreurs de permissions `uv` cache (`~/.cache/uv`)

Lance avec :

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run python -m goldroger.cli -c "LVMH"
```

## Roadmap (idées)

- Améliorer la collecte “web search” (sources, citations, extraction plus robuste).
- Ajouter des exports plus “banque d’affaires” (template PPTX, tables comps, graphiques).
- Ajouter du caching (résultats web + agents) et des logs structurés.
- Ajouter des tests (parsing JSON, génération Excel/PPTX).
