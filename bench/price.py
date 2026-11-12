"""List-price cost per model, from config/prices.toml (spec §10)."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

from bench.config import CONFIG
from bench.transcripts import Usage

FREE_MODELS = {"", "<synthetic>"}


@dataclass(frozen=True)
class ModelPrice:
    input: float
    cache_write: float
    cache_read: float
    output: float


@dataclass(frozen=True)
class Prices:
    models: dict[str, ModelPrice]

    def knows(self, model: str) -> bool:
        return model in FREE_MODELS or any(model.startswith(key) for key in self.models)

    def for_model(self, model: str) -> ModelPrice:
        matches = [key for key in self.models if model.startswith(key)]
        if not matches:
            raise KeyError(f"no price for model {model!r}; add it to config/prices.toml")
        return self.models[max(matches, key=len)]


def load_prices(path: Path = CONFIG / "prices.toml") -> Prices:
    data = tomllib.loads(path.read_text())
    models = {}
    for vendor in data.values():
        for model, p in vendor["models"].items():
            models[model] = ModelPrice(p["input"], p["cache_write"], p["cache_read"], p["output"])
    return Prices(models)


def cost_usd(model: str, usage: Usage, prices: Prices) -> float:
    if model in FREE_MODELS:
        return 0.0
    p = prices.for_model(model)
    return (
        usage.input * p.input
        + usage.cache_write * p.cache_write
        + usage.cache_read * p.cache_read
        + usage.output * p.output
    ) / 1_000_000
