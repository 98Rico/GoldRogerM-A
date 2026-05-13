from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SectorProfile:
    key: str
    label: str
    acceptable_peer_buckets: tuple[str, ...] = field(default_factory=tuple)
    forbidden_peer_buckets: tuple[str, ...] = field(default_factory=tuple)
    typical_multiples: tuple[str, ...] = field(default_factory=tuple)
    terminal_growth_range: tuple[float, float] = (0.015, 0.03)
    demand_drivers: tuple[str, ...] = field(default_factory=tuple)
    margin_drivers: tuple[str, ...] = field(default_factory=tuple)
    common_risks: tuple[str, ...] = field(default_factory=tuple)
    fallback_market_context: tuple[str, ...] = field(default_factory=tuple)
    fallback_catalysts: tuple[str, ...] = field(default_factory=tuple)


_ARCHETYPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "healthtech_platform": (
        "healthtech",
        "digital health",
        "medical scheduling",
        "provider workflow",
        "telemedicine",
        "patient engagement",
    ),
    "fintech_digital_bank_payments": (
        "fintech",
        "digital bank",
        "neobank",
        "payments",
        "card issuance",
        "interchange",
        "lending",
        "compliance",
    ),
    "hrtech_saas": (
        "hr tech",
        "hrtech",
        "human resources",
        "hcm",
        "payroll",
        "workforce management",
        "talent",
    ),
    "b2b_saas": (
        "b2b saas",
        "subscription software",
        "enterprise workflow",
        "arr",
        "seat expansion",
    ),
    "marketplace": (
        "marketplace",
        "take rate",
        "gross merchandise value",
        "gmv",
        "seller liquidity",
    ),
    "consumer_brand": (
        "consumer brand",
        "apparel",
        "beauty",
        "wellness",
        "d2c",
        "retail expansion",
    ),
    "industrial_private": (
        "industrial",
        "manufacturing",
        "automation",
        "capital equipment",
        "order backlog",
    ),
    "professional_services": (
        "consulting",
        "advisory",
        "professional services",
        "utilization",
        "billable rate",
    ),
    "healthcare_services": (
        "healthcare services",
        "care delivery",
        "provider network",
        "reimbursement",
        "patient volumes",
    ),
    "premium_device_platform": (
        "apple",
        "iphone",
        "ipad",
        "mac",
        "wearable",
        "device",
        "installed base",
        "ecosystem",
        "services attach",
        "app store",
    ),
    "consumer_hardware_ecosystem": (
        "smartphone",
        "consumer electronics",
        "device upgrade",
        "services attach",
        "ecosystem",
    ),
    "tobacco_nicotine_cash_return": (
        "tobacco",
        "nicotine",
        "combustible",
        "reduced-risk",
        "rrp",
        "excise",
        "litigation",
        "pricing",
        "volume decline",
        "dividend",
        "cash return",
    ),
    "commodity_cyclical_aluminum": (
        "aluminum",
        "aluminium",
        "lme",
        "smelter",
        "recycling",
        "low-carbon",
        "energy cost",
        "commodity cycle",
        "cbam",
        "alumina",
    ),
    "software_platform": (
        "software",
        "saas",
        "cloud",
        "enterprise software",
        "platform",
    ),
    "semiconductor": (
        "semiconductor",
        "chip",
        "foundry",
        "memory",
        "gpu",
        "wafer",
    ),
    "financials": (
        "bank",
        "insurance",
        "deposit",
        "lending",
        "combined ratio",
    ),
    "consumer_staples": (
        "consumer staples",
        "pricing",
        "volume",
        "brand",
        "distribution",
    ),
    "healthcare": (
        "pharma",
        "drug",
        "pipeline",
        "trial",
        "medical device",
    ),
    "default": (),
}


_ARCHETYPE_FALLBACKS: dict[str, dict[str, tuple[str, ...] | str]] = {
    "healthtech_platform": {
        "label": "healthtech_platform",
        "demand_drivers": (
            "provider and clinic digitalization",
            "patient adoption of digital access and telehealth",
            "workflow stickiness and platform expansion",
        ),
        "margin_drivers": (
            "software mix and recurring subscriptions",
            "sales efficiency and retention",
            "implementation/support cost leverage",
        ),
        "risks": (
            "clinical/workflow switching friction",
            "regulatory and data-privacy requirements",
            "competitive platform pressure",
        ),
        "catalysts": (
            "Next operating update: provider growth, retention, and monetization.",
            "Product rollout update: workflow adoption and engagement durability.",
            "Regulatory/policy updates affecting digital healthcare operations.",
        ),
    },
    "fintech_digital_bank_payments": {
        "label": "fintech_digital_bank_payments",
        "demand_drivers": (
            "customer-account growth and engagement",
            "payments volume and card usage",
            "product expansion across banking and adjacent services",
        ),
        "margin_drivers": (
            "interchange and fee mix",
            "credit/funding cost dynamics",
            "operating leverage versus compliance and risk costs",
        ),
        "risks": (
            "credit quality and macro sensitivity",
            "regulatory/compliance requirements",
            "competition from incumbents and other fintech platforms",
        ),
        "catalysts": (
            "Next operating update: customer growth, ARPU, and margin guidance.",
            "Credit/funding update: delinquency, loss, and funding-cost trends.",
            "Regulatory updates affecting product scope or economics.",
        ),
    },
    "hrtech_saas": {
        "label": "hrtech_saas",
        "demand_drivers": (
            "SMB/enterprise HR software adoption",
            "seat growth and module expansion",
            "retention and upsell durability",
        ),
        "margin_drivers": (
            "subscription mix",
            "sales and onboarding efficiency",
            "operating leverage in support and G&A",
        ),
        "risks": (
            "budget-cycle pressure in SMB/enterprise customers",
            "competition in payroll/HCM stack",
            "implementation complexity and churn risk",
        ),
        "catalysts": (
            "Next operating update: ARR/retention and margin trajectory.",
            "Product roadmap update: payroll/HCM module adoption.",
            "Macro hiring trends affecting seat expansion.",
        ),
    },
    "b2b_saas": {
        "label": "b2b_saas",
        "demand_drivers": (
            "enterprise software spending",
            "net retention and account expansion",
            "new-logo acquisition efficiency",
        ),
        "margin_drivers": (
            "subscription gross margin",
            "sales productivity",
            "operating leverage",
        ),
        "risks": (
            "IT budget tightening",
            "competitive pricing pressure",
            "platform concentration risk",
        ),
        "catalysts": (
            "Next operating update: ARR growth, retention, and margin guidance.",
            "Go-to-market update: sales efficiency and payback trends.",
            "Product/pricing update: evidence of durable expansion.",
        ),
    },
    "marketplace": {
        "label": "marketplace",
        "demand_drivers": (
            "GMV growth and transaction frequency",
            "buyer/seller liquidity depth",
            "geographic/category expansion",
        ),
        "margin_drivers": (
            "take-rate evolution",
            "marketing efficiency",
            "fulfillment and support-cost leverage",
        ),
        "risks": (
            "demand cyclicality",
            "competition and subsidy intensity",
            "regulatory pressure in platform economics",
        ),
        "catalysts": (
            "Next operating update: GMV, take-rate, and contribution margin trends.",
            "Liquidity update: buyer/seller growth and retention.",
            "Regulatory/platform updates affecting marketplace economics.",
        ),
    },
    "consumer_brand": {
        "label": "consumer_brand",
        "demand_drivers": (
            "brand momentum and repeat purchase",
            "channel mix (D2C vs wholesale/retail)",
            "new category and geographic expansion",
        ),
        "margin_drivers": (
            "pricing power",
            "gross-margin mix and input costs",
            "marketing efficiency",
        ),
        "risks": (
            "consumer demand volatility",
            "inventory and markdown risk",
            "channel concentration",
        ),
        "catalysts": (
            "Next operating update: growth, gross margin, and inventory discipline.",
            "Channel update: D2C performance versus wholesale/retail partners.",
            "Product launch update: evidence of repeat demand and pricing resilience.",
        ),
    },
    "industrial_private": {
        "label": "industrial_private",
        "demand_drivers": (
            "industrial production and end-market demand",
            "order intake and backlog conversion",
            "aftermarket/service attachment",
        ),
        "margin_drivers": (
            "utilization and mix",
            "input-cost management",
            "operating leverage and productivity",
        ),
        "risks": (
            "cyclical demand swings",
            "project-execution variability",
            "input-cost and supply-chain pressure",
        ),
        "catalysts": (
            "Next operating update: orders, backlog, and margin guidance.",
            "Execution update: delivery cadence and cost-control progress.",
            "Macro/industrial activity updates affecting demand visibility.",
        ),
    },
    "professional_services": {
        "label": "professional_services",
        "demand_drivers": (
            "client-project pipeline and renewal rates",
            "advisory demand in core verticals",
            "cross-sell and account expansion",
        ),
        "margin_drivers": (
            "utilization rates",
            "billable-rate realization",
            "delivery efficiency",
        ),
        "risks": (
            "project timing volatility",
            "talent retention and wage inflation",
            "client concentration",
        ),
        "catalysts": (
            "Next operating update: utilization, bookings, and margin cadence.",
            "Talent update: hiring, retention, and wage trend impact.",
            "Client-mix update: demand breadth across key verticals.",
        ),
    },
    "healthcare_services": {
        "label": "healthcare_services",
        "demand_drivers": (
            "patient volumes and case mix",
            "provider-network reach",
            "service-line expansion",
        ),
        "margin_drivers": (
            "payer mix and reimbursement",
            "labor productivity",
            "occupancy/utilization dynamics",
        ),
        "risks": (
            "reimbursement policy changes",
            "labor cost pressure",
            "regulatory/compliance intensity",
        ),
        "catalysts": (
            "Next operating update: patient volumes, payer mix, and margin guidance.",
            "Reimbursement/policy updates with potential earnings impact.",
            "Capacity and staffing updates affecting service throughput.",
        ),
    },
    "premium_device_platform": {
        "label": "premium_device_platform",
        "demand_drivers": (
            "device upgrade cycles",
            "services attach to installed base",
            "ecosystem engagement and retention",
        ),
        "margin_drivers": (
            "services mix",
            "product mix and premiumization",
            "supply-chain efficiency",
        ),
        "risks": (
            "App Store/platform-policy regulation",
            "product-cycle weakness",
            "supply-chain and geographic concentration",
        ),
        "catalysts": (
            "Next earnings update: device demand, services growth, and margin guidance.",
            "Product/software cycle updates: signs of sustained upgrade activity.",
            "Platform/regulatory developments: potential impact on ecosystem monetization.",
        ),
    },
    "consumer_hardware_ecosystem": {
        "label": "consumer_hardware_ecosystem",
        "demand_drivers": (
            "device replacement cycles",
            "services attach and recurring usage",
            "installed-base engagement",
        ),
        "margin_drivers": (
            "mix",
            "component costs",
            "operating leverage",
        ),
        "risks": (
            "competition",
            "regulatory pressure",
            "demand-cycle volatility",
        ),
        "catalysts": (
            "Next earnings update: demand, services attach, and margin guidance.",
            "Product cycle updates: evidence of replacement-cycle support.",
            "Policy/platform updates: potential impact on monetization.",
        ),
    },
    "tobacco_nicotine_cash_return": {
        "label": "tobacco_nicotine_cash_return",
        "demand_drivers": (
            "pricing power",
            "combustible volume and mix",
            "reduced-risk product adoption",
        ),
        "margin_drivers": (
            "excise passthrough",
            "product mix",
            "operating leverage and cost control",
        ),
        "risks": (
            "regulation and excise changes",
            "litigation and enforcement",
            "combustible volume decline",
            "illicit trade",
        ),
        "catalysts": (
            "Next earnings update: pricing, volume, and margin guidance.",
            "Reduced-risk product update: progress on mix transition.",
            "Regulatory/tax updates: potential impact on cash returns.",
        ),
    },
    "commodity_cyclical_aluminum": {
        "label": "commodity_cyclical_aluminum",
        "demand_drivers": (
            "aluminum demand and end-market industrial activity",
            "recycling and low-carbon aluminum demand",
            "regional supply/demand balance",
        ),
        "margin_drivers": (
            "LME aluminum pricing",
            "alumina/raw-material costs",
            "energy costs and operating efficiency",
        ),
        "risks": (
            "commodity-cycle volatility",
            "energy-cost shocks",
            "trade/regulatory shifts (including CBAM)",
        ),
        "catalysts": (
            "Next earnings update: realized prices, energy costs, and margin guidance.",
            "Operational updates: smelting, alumina, recycling, and energy assets.",
            "Commodity/regulatory updates: LME pricing and trade-policy developments.",
        ),
    },
}


_PROFILES: dict[str, SectorProfile] = {
    "technology_software": SectorProfile(
        key="technology_software",
        label="Technology / Software",
        acceptable_peer_buckets=(
            "software_services_platform",
            "consumer_hardware_ecosystem",
            "networking_infrastructure",
        ),
        forbidden_peer_buckets=("tobacco_nicotine",),
        typical_multiples=("EV/EBITDA", "EV/Revenue"),
        terminal_growth_range=(0.025, 0.035),
        demand_drivers=("enterprise software adoption", "cloud migration", "AI enablement"),
        margin_drivers=("software mix", "operating leverage", "R&D discipline"),
        common_risks=("pricing pressure", "platform competition", "regulation"),
        fallback_market_context=(
            "Demand trend: enterprise/software demand remains product-cycle and budget-cycle sensitive.",
            "Competitive trend: platform and ecosystem competition can pressure growth durability.",
            "Regulatory trend: antitrust and platform-policy changes can affect monetization.",
        ),
        fallback_catalysts=(
            "Next earnings update: demand, margins, and guidance.",
            "Product/software roadmap update: evidence of adoption durability.",
            "Regulatory/platform-policy updates: potential impact on monetization.",
        ),
    ),
    "technology_semiconductors": SectorProfile(
        key="technology_semiconductors",
        label="Technology / Semiconductors",
        acceptable_peer_buckets=(
            "semiconductors",
            "semiconductor_equipment",
            "networking_infrastructure",
        ),
        forbidden_peer_buckets=("tobacco_nicotine",),
        typical_multiples=("EV/EBITDA", "EV/Revenue"),
        terminal_growth_range=(0.02, 0.03),
        demand_drivers=("AI/data-center demand", "cyclical electronics demand", "inventory normalization"),
        margin_drivers=("mix", "utilization", "pricing discipline"),
        common_risks=("cyclicality", "geopolitics", "supply concentration"),
        fallback_market_context=(
            "Demand trend: semiconductor demand is cyclical and tied to end-market refresh cycles.",
            "Competitive trend: performance and cost curves drive share shifts.",
            "Regulatory trend: export controls and geopolitics can influence growth visibility.",
        ),
        fallback_catalysts=(
            "Next earnings update: utilization, backlog, and margin guidance.",
            "AI/data-center demand update: evidence of sustained order strength.",
            "Policy/export-control developments: potential impact on demand mix.",
        ),
    ),
    "technology_consumer_electronics": SectorProfile(
        key="technology_consumer_electronics",
        label="Technology / Consumer Electronics",
        acceptable_peer_buckets=(
            "consumer_hardware_ecosystem",
            "software_services_platform",
            "networking_infrastructure",
            "semiconductors",
            "semiconductor_equipment",
        ),
        forbidden_peer_buckets=("tobacco_nicotine",),
        typical_multiples=("EV/EBITDA", "EV/Revenue"),
        terminal_growth_range=(0.02, 0.03),
        demand_drivers=("device upgrade cycles", "services attach", "ecosystem engagement"),
        margin_drivers=("mix", "services monetization", "supply-chain efficiency"),
        common_risks=("upgrade-cycle weakness", "competition", "regulation"),
        fallback_market_context=(
            "Demand trend: hardware demand remains replacement-cycle driven.",
            "Platform/services trend: ecosystem monetization remains a key valuation driver.",
            "Regulatory trend: platform-policy and antitrust pressure remain material risks.",
        ),
        fallback_catalysts=(
            "Next earnings update: demand, margins, and guidance.",
            "Product/software update: evidence of upgrade-cycle support.",
            "Regulatory/platform-policy updates: impact on services monetization.",
        ),
    ),
    "consumer_staples_tobacco": SectorProfile(
        key="consumer_staples_tobacco",
        label="Consumer Staples / Tobacco",
        acceptable_peer_buckets=(
            "tobacco_nicotine",
            "consumer_staples_adjacent",
            "beverages_adjacent",
            "household_products_adjacent",
            "retail_adjacent",
        ),
        forbidden_peer_buckets=(
            "software_services_platform",
            "consumer_hardware_ecosystem",
            "semiconductors",
            "semiconductor_equipment",
            "networking_infrastructure",
            "other_adjacent_tech",
        ),
        typical_multiples=("EV/EBITDA", "P/E", "FCF yield"),
        terminal_growth_range=(0.01, 0.02),
        demand_drivers=("pricing", "volume mix", "reduced-risk adoption"),
        margin_drivers=("excise tax passthrough", "product mix", "operating leverage"),
        common_risks=("regulation", "litigation", "illicit trade", "volume decline"),
        fallback_market_context=(
            "Demand trend: tobacco demand is pricing-led with structural volume pressure.",
            "Competitive trend: reduced-risk products and brand pricing power drive mix shifts.",
            "Regulatory trend: excise, flavor, and marketing rules remain key valuation risks.",
        ),
        fallback_catalysts=(
            "Next earnings update: pricing, volume, and margin guidance.",
            "Reduced-risk product update: evidence of mix transition durability.",
            "Regulatory/tax updates: potential impact on profitability and cash returns.",
        ),
    ),
    "consumer_staples_beverages": SectorProfile(
        key="consumer_staples_beverages",
        label="Consumer Staples / Beverages",
        acceptable_peer_buckets=("beverages_adjacent", "consumer_staples_adjacent"),
        terminal_growth_range=(0.015, 0.025),
    ),
    "consumer_staples_household": SectorProfile(
        key="consumer_staples_household",
        label="Consumer Staples / Household Products",
        acceptable_peer_buckets=("household_products_adjacent", "consumer_staples_adjacent"),
        terminal_growth_range=(0.015, 0.025),
    ),
    "consumer_discretionary_retail": SectorProfile(
        key="consumer_discretionary_retail",
        label="Consumer Discretionary / Retail",
        acceptable_peer_buckets=("retail_adjacent", "consumer_staples_adjacent"),
        terminal_growth_range=(0.015, 0.025),
    ),
    "healthcare_pharma": SectorProfile(
        key="healthcare_pharma",
        label="Healthcare / Pharma",
        acceptable_peer_buckets=("healthcare_pharma", "healthcare_medtech"),
        terminal_growth_range=(0.015, 0.025),
    ),
    "healthcare_medtech": SectorProfile(
        key="healthcare_medtech",
        label="Healthcare / Medtech",
        acceptable_peer_buckets=("healthcare_medtech", "healthcare_pharma"),
        terminal_growth_range=(0.02, 0.03),
    ),
    "financials_banks": SectorProfile(
        key="financials_banks",
        label="Financials / Banks",
        acceptable_peer_buckets=("financials_banks", "financials_insurance"),
        typical_multiples=("P/E", "P/B"),
        terminal_growth_range=(0.015, 0.025),
    ),
    "financials_insurance": SectorProfile(
        key="financials_insurance",
        label="Financials / Insurance",
        acceptable_peer_buckets=("financials_insurance", "financials_banks"),
        typical_multiples=("P/E", "P/B"),
        terminal_growth_range=(0.015, 0.025),
    ),
    "industrials": SectorProfile(
        key="industrials",
        label="Industrials",
        acceptable_peer_buckets=("industrials_general",),
        terminal_growth_range=(0.015, 0.025),
    ),
    "energy_oil_gas": SectorProfile(
        key="energy_oil_gas",
        label="Energy / Oil & Gas",
        acceptable_peer_buckets=("energy_oil_gas",),
        typical_multiples=("EV/EBITDA", "FCF yield"),
        terminal_growth_range=(0.01, 0.02),
    ),
    "utilities": SectorProfile(
        key="utilities",
        label="Utilities",
        acceptable_peer_buckets=("utilities_general",),
        terminal_growth_range=(0.01, 0.02),
    ),
    "real_estate_reit": SectorProfile(
        key="real_estate_reit",
        label="Real Estate / REITs",
        acceptable_peer_buckets=("real_estate_reit",),
        terminal_growth_range=(0.01, 0.02),
    ),
    "materials_chemicals_mining": SectorProfile(
        key="materials_chemicals_mining",
        label="Materials / Chemicals / Mining",
        acceptable_peer_buckets=(
            "aluminum_metals",
            "metals_mining",
            "construction_materials_adjacent",
            "chemicals_adjacent",
            "materials_general",
        ),
        terminal_growth_range=(0.01, 0.02),
    ),
    "telecom_media": SectorProfile(
        key="telecom_media",
        label="Telecom / Media",
        acceptable_peer_buckets=("telecom_media",),
        terminal_growth_range=(0.01, 0.02),
    ),
    "default": SectorProfile(
        key="default",
        label="Default fallback",
        acceptable_peer_buckets=(),
        terminal_growth_range=(0.015, 0.025),
        fallback_market_context=(
            "Demand trend: market demand is mixed and data availability is limited in this run.",
            "Competitive trend: peer positioning should be interpreted with caution.",
            "Regulatory trend: policy and macro conditions remain key external variables.",
        ),
        fallback_catalysts=(
            "Next earnings update: demand, margins, and guidance.",
            "Strategy update: evidence of execution and cash-flow resilience.",
            "Regulatory/macro updates: potential impact on valuation assumptions.",
        ),
    ),
}


def detect_sector_profile(sector: str, industry: str = "") -> str:
    s = f"{sector or ''} {industry or ''}".lower()
    if any(k in s for k in ("tobacco", "nicotine", "cigarette", "smoke-free")):
        return "consumer_staples_tobacco"
    if any(k in s for k in ("beverage", "soft drink", "brewer", "distiller")):
        return "consumer_staples_beverages"
    if any(k in s for k in ("household", "personal care", "home care", "consumer defensive")):
        return "consumer_staples_household"
    if any(k in s for k in ("consumer electronics", "smartphone", "device", "tablet", "wearable", "computer hardware")):
        return "technology_consumer_electronics"
    if any(k in s for k in ("semiconductor", "chip", "foundry", "memory", "gpu", "fabless")):
        return "technology_semiconductors"
    if any(k in s for k in ("software", "saas", "cloud", "internet", "platform", "technology")):
        return "technology_software"
    if any(k in s for k in ("bank", "banking", "consumer finance")):
        return "financials_banks"
    if any(k in s for k in ("insurance", "insurer")):
        return "financials_insurance"
    if any(k in s for k in ("pharma", "pharmaceutical", "biotech")):
        return "healthcare_pharma"
    if any(k in s for k in ("medtech", "medical device", "healthcare equipment")):
        return "healthcare_medtech"
    if any(k in s for k in ("energy", "oil", "gas", "upstream", "downstream")):
        return "energy_oil_gas"
    if any(k in s for k in ("utility", "electric", "water", "power")):
        return "utilities"
    if any(k in s for k in ("reit", "real estate", "property")):
        return "real_estate_reit"
    if any(k in s for k in ("chemical", "mining", "metals", "materials")):
        return "materials_chemicals_mining"
    if any(k in s for k in ("telecom", "media", "communication services", "wireless")):
        return "telecom_media"
    if any(k in s for k in ("retail", "e-commerce", "consumer discretionary")):
        return "consumer_discretionary_retail"
    if "industrial" in s:
        return "industrials"
    return "default"


def get_sector_profile(sector: str, industry: str = "") -> SectorProfile:
    return _PROFILES.get(detect_sector_profile(sector, industry), _PROFILES["default"])


def detect_company_archetype(
    company: str = "",
    ticker: str = "",
    sector: str = "",
    industry: str = "",
) -> str:
    txt = f"{company or ''} {ticker or ''} {sector or ''} {industry or ''}".lower()
    if any(k in txt for k in ("doctolib", "healthtech", "digital health", "telemedicine")):
        return "healthtech_platform"
    if any(k in txt for k in ("revolut", "monzo", "wise", "adyen", "fintech", "digital bank", "payments")):
        return "fintech_digital_bank_payments"
    if any(k in txt for k in ("personio", "hr tech", "hrtech", "human resources", "hcm", "payroll platform")):
        return "hrtech_saas"
    if any(k in txt for k in ("b2b saas", "subscription software", "enterprise workflow")):
        return "b2b_saas"
    if any(k in txt for k in ("marketplace", "gmv", "take rate", "platform marketplace")):
        return "marketplace"
    if any(k in txt for k in ("consumer brand", "apparel", "beauty", "wellness", "d2c")):
        return "consumer_brand"
    if any(k in txt for k in ("industrial", "manufacturing", "automation", "capital equipment")):
        return "industrial_private"
    if any(k in txt for k in ("professional services", "consulting", "advisory")):
        return "professional_services"
    if any(k in txt for k in ("healthcare services", "care delivery", "provider network")):
        return "healthcare_services"
    if "aapl" in txt or "apple" in txt:
        return "premium_device_platform"
    if any(k in txt for k in ("tobacco", "nicotine", "bats", "bti", "british american tobacco")):
        return "tobacco_nicotine_cash_return"
    if any(k in txt for k in ("norsk hydro", "nhy", "aluminum", "aluminium", "alumina")):
        return "commodity_cyclical_aluminum"
    if any(k in txt for k in ("consumer electronics", "smartphone", "device", "wearable")):
        return "consumer_hardware_ecosystem"
    if any(k in txt for k in ("software", "saas", "cloud", "platform")):
        return "software_platform"
    if any(k in txt for k in ("semiconductor", "chip", "foundry", "memory", "gpu")):
        return "semiconductor"
    if any(k in txt for k in ("bank", "insurance", "financial")):
        return "financials"
    if any(k in txt for k in ("pharma", "biotech", "medical", "healthcare")):
        return "healthcare"
    if any(k in txt for k in ("consumer staples", "beverage", "household products")):
        return "consumer_staples"
    return "default"


def archetype_keywords(archetype: str) -> tuple[str, ...]:
    return _ARCHETYPE_KEYWORDS.get(archetype, ())


def archetype_fallback(archetype: str) -> dict[str, tuple[str, ...] | str]:
    return _ARCHETYPE_FALLBACKS.get(archetype, {})
