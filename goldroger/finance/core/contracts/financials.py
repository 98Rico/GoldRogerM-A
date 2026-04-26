from pydantic import BaseModel

class FinancialsModel(BaseModel):
    revenue_current: float
    revenue_series: list[float]
    ebitda_margin: float

    def is_valid(self):
        return self.revenue_current > 0 and len(self.revenue_series) > 0