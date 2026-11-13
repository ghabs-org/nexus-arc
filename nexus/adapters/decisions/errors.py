"""Sanitized typed-decision transport failures shared by runtime consumers."""

TRANSIENT = {
    "rate_limited",
    "timeout",
    "unreachable",
    "invalid_response",
    "input_too_large",
    "refused",
}


class DecisionCallError(RuntimeError):
    def __init__(self, category):
        self.category = category if category in TRANSIENT | {"unauthorized", "config"} else "config"
        super().__init__("Decision call failed: " + self.category)


class DecisionAnswers(dict):
    """Answer mapping plus optional model/usage metadata; values remain ordinary JSON."""

    def __init__(self, answers):
        super().__init__(answers)
        self.metadata = {}
