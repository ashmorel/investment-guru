from app.services.guru.schemas import RotationAdvicePayload
from app.services.guru.service import _ROTATION_INSTRUCTION, _rotation_invalid_groups


def test_rotation_payload_shape_and_no_money_fields():
    p = RotationAdvicePayload(
        market_view="Leaning to trim megacap tech toward lighter groups.",
        groups=[{"name": "Big Tech", "weight_pct": "54.00",
                 "observation": "Up strongly, now 54% of the book.", "signal": "trim"}],
        rotations=[{"from_group": "Big Tech", "to_group": "Financials",
                    "rationale": "Reduce concentration.", "conviction": "med"}],
        caveats=["Limited trend history."],
        disclaimer="Educational, not regulated financial advice.",
    )
    assert p.rotations[0].conviction == "med"
    assert p.groups[0].signal == "trim"
    # Guardrail: no amount/quantity/price fields anywhere in the schema
    text = " ".join(RotationAdvicePayload.model_json_schema()["$defs"].keys()) \
        + " " + " ".join(RotationAdvicePayload.model_fields)
    for banned in ("amount", "quantity", "shares", "price", "value_base", "gbp"):
        assert banned not in text.lower()


def test_rotation_instruction_carries_guardrails():
    t = _ROTATION_INSTRUCTION.lower()
    assert "only" in t and "context" in t  # reason only from provided context
    # no specific trades/figures
    assert "not" in t and ("amount" in t or "trade" in t or "price" in t)
    assert "disclaimer" in t


def test_rotation_invalid_groups_detects_unknown():
    p = RotationAdvicePayload(
        market_view="x", groups=[],
        rotations=[{"from_group": "Big Tech", "to_group": "Crypto",
                    "rationale": "y", "conviction": "low"}],
        caveats=[], disclaimer="z")
    assert _rotation_invalid_groups(p, {"Big Tech", "Financials", "Ungrouped"}) == {"Crypto"}
