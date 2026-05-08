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
        acceptable_peer_buckets=("materials_general",),
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

