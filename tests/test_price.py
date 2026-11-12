import pytest

from bench.price import cost_usd, load_prices
from bench.transcripts import Usage


def test_known_usage_on_known_model():
    prices = load_prices()
    u = Usage(input=1_000_000, cache_write=500_000, cache_read=2_000_000, output=100_000)
    # opus 5.5: 4 + 0.5*8 + 2*0.20 + 0.1*20
    assert cost_usd("claude-opus-5-5", u, prices) == pytest.approx(4 + 4 + 0.4 + 2)


def test_longest_prefix():
    prices = load_prices()
    assert prices.for_model("claude-opus-5-5").output == 20.0
    assert prices.for_model("claude-opus-5").output == 25.0
    assert prices.for_model("claude-opus-5-20260601").output == 25.0
    assert prices.for_model("claude-haiku-4-5-20251001").input == 1.0


def test_unknown_raises_and_synthetic_is_free():
    prices = load_prices()
    with pytest.raises(KeyError, match="claude-mystery-9"):
        prices.for_model("claude-mystery-9")
    assert cost_usd("<synthetic>", Usage(9, 9, 9, 9), prices) == 0.0
    assert cost_usd("", Usage(9, 9, 9, 9), prices) == 0.0


def test_openai_rows_from_the_page():
    prices = load_prices()
    assert prices.for_model("gpt-6-luna").input == 0.10
    assert prices.for_model("gpt-6-sol").output == 10.0
    assert prices.for_model("gpt-6-astra").cache_write == 12.50
    assert prices.for_model("gpt-5.6-sol").output == 20.0
