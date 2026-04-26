class DealScore:
    def __init__(self, strategy, synergies, risk, lbo, valuation):
        self.strategy = strategy
        self.synergies = synergies
        self.risk = risk
        self.lbo = lbo
        self.valuation = valuation

    def compute(self):
        """
        Weighted IC score (0-100)
        """

        score = (
            self.strategy * 0.25 +
            self.synergies * 0.25 +
            self.lbo * 0.20 +
            self.valuation * 0.15 +
            self.risk * 0.15
        )

        return {
            "ic_score": round(score, 2),
            "recommendation": self._recommend(score)
        }

    def _recommend(self, score):
        if score >= 75:
            return "STRONG BUY"
        if score >= 60:
            return "BUY"
        if score >= 45:
            return "WATCH"
        return "NO GO"