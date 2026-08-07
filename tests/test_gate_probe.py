"""Throwaway: deliberately fails, to verify that a red matrix turns ci-ok red
(rather than skipping it) and that branch protection refuses the merge.

This file is not meant to be merged.
"""


def test_deliberate_failure_to_exercise_the_ci_gate():
    assert False, "deliberate failure: probing the required ci-ok check"
