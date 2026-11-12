from typing import Literal

Arm = Literal["A", "Aprime", "V"]
Source = Literal["history", "run"]


class Blocked(RuntimeError):
    """The tree contradicts the plan; stop and report instead of improvising."""
