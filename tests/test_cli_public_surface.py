"""Keep removed private-data and release commands out of the public CLI."""

import pytest

from mentat_bench import cli


@pytest.mark.parametrize("argv", [
    ["datacheck"],
    ["data", "verify"],
    ["release", "build"],
    ["fixtures", "native"],
    ["build", "--adapter", "empty", "--corpus", "c.jsonl", "--out", "o", "--data-dir", "data"],
    ["fixtures", "gen", "--out", "f", "--native"],
])
def test_removed_commands_and_flags_are_rejected(argv):
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(argv)


@pytest.mark.parametrize("argv", [
    ["build", "--adapter", "empty", "--out", "o"],
    ["evaluate", "--submission", "s", "--gold", "g", "--out", "o"],
    ["score", "--gold-answers", "g.jsonl"],
])
def test_jsonl_inputs_remain_required(argv):
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(argv)
