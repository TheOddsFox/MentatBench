"""The chain guard: an ordered_chain decision with two members of one bound class (one market's Yes and No, say) is
rejected at admission and counted as a group rejection; a chain across two classes is admitted."""

import json

from mentat_bench.adapters.base import usage
from mentat_bench.harness import fixed
from mentat_bench.manifest import DEFAULT_PROFILE


class Chain:
    """Binds each question to its class (the question text) and negates label No. Its group answer is one ordered chain
    over the first and the last member of the candidate."""

    name = "chain"
    supports_groups = True

    def decide_binding(self, ctx):
        return {"status": "bound", "class": ctx["question"], "negated": ctx["label"] == "No", "missing": []}, usage(self.name)

    def decide_relation(self, a, b):
        return "NONE", usage(self.name)

    def decide_group(self, ctx):
        members = [m["id"] for m in ctx["members"]]
        return [{"kind": "ordered_chain", "members": [members[0], members[-1]]}], usage(self.name)


def _market(name, event):
    question = f"Will {name} win the cup?"
    return [{"id": f"v:{name}:1", "question": question, "label": "Yes", "event_title": event},
            {"id": f"v:{name}:2", "question": question, "label": "No", "event_title": event}]


def test_a_chain_inside_one_market_is_rejected_and_counted(tmp_path):
    """Complementary members (one class, p and not-p): a degenerate chain, rejected at admission."""
    summary = fixed.build(_market("alpha", "E"), Chain(), tmp_path / "sub", dict(DEFAULT_PROFILE))
    assert summary["groups_committed"] == 0 and summary["groups_rejected"] == 1
    assert summary["group_candidates"] == 1
    theory = json.loads((tmp_path / "sub" / "theory.json").read_text(encoding="utf-8"))
    assert theory["constraints"] == []


def test_a_chain_of_one_class_with_the_same_negation_is_a_duplicate_and_rejected(tmp_path):
    """Two outcomes of one class with the same negation (the same claim twice): a degenerate chain, rejected."""
    records = [{"id": "v:alpha:1", "question": "Will Ember win the cup?", "label": "Yes", "event_title": "E"},
               {"id": "v:beta:1", "question": "Will Ember win the cup?", "label": "Yes", "event_title": "E"}]
    summary = fixed.build(records, Chain(), tmp_path / "sub", dict(DEFAULT_PROFILE))
    assert summary["group_candidates"] == 1 and summary["groups_committed"] == 0 and summary["groups_rejected"] == 1
    theory = json.loads((tmp_path / "sub" / "theory.json").read_text(encoding="utf-8"))
    assert theory["constraints"] == []


def test_a_chain_across_two_classes_is_admitted(tmp_path):
    records = _market("alpha", "E") + _market("beta", "E")  # the event's candidate: alpha Yes .. beta No
    summary = fixed.build(records, Chain(), tmp_path / "sub", dict(DEFAULT_PROFILE))
    assert summary["groups_committed"] == 1 and summary["groups_rejected"] == 2  # the two market candidates are rejected
    assert summary["constraints"] >= 1
