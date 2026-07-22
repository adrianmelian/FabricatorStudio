# _dev/test_aim_all_at_child.py
"""Offscreen tests for the FABRICATE-mode aimer function
(maya_tools/rigging/joint_orient/joint_orient_app.py: aim_all_at_child).
No live Maya scene required — the module only needs to be IMPORTABLE
(mayapy provides maya.cmds/maya.api without maya.standalone.initialize(),
same pattern as _dev/test_ik_arm.py and _dev/test_installer_logic.py).

Coverage is deliberately narrow: `_pick_chain_continuation` is the one
piece of aim_all_at_child's multi-child logic factored to take plain
data (child, depth, length) tuples with zero cmds calls, so the
deepest-subtree / longest-bone-tiebreak / genuine-tie rule is provable
without a live scene. `_is_default_aimer_state` is the other pure-data
helper (the honesty gate deciding what counts as authored state worth
confessing to overwriting) and gets the same treatment.

Everything else in aim_all_at_child (_bone_length, _max_subtree_depth,
_reachable_set/_true_roots/_ordered_from_set, the end-joint
parent-frame copy, the live aimConstraint/condition DG network) needs
real joints and a real scene graph — NOT covered here. See the report
back to the dispatching agent for what a live-Maya pass would still
need to confirm.

Run:
"C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" _dev/test_aim_all_at_child.py
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

FAILURES = []


def check(name, fn):
    try:
        fn()
        print(f"  ok: {name}")
    except Exception as exc:
        import traceback
        FAILURES.append(f"{name}: {exc!r}")
        print(f"FAIL: {name}: {exc!r}")
        traceback.print_exc()


def _joa():
    from maya_tools.rigging.joint_orient import joint_orient_app as joa
    return joa


# ─── module shape ────────────────────────────────────────────────────

def test_module_imports_and_public_api_shape():
    joa = _joa()
    assert hasattr(joa, 'aim_all_at_child')
    import inspect
    sig = inspect.signature(joa.aim_all_at_child)
    assert list(sig.parameters) == ['joints']
    assert sig.parameters['joints'].default is None


def test_seed_aimer_from_detection_untouched():
    # Acceptance: no change to seed_aimer_from_detection behavior. Cheap
    # sentinel — the function still exists with its documented contract
    # (detect, never fabricate) rather than having been touched/renamed.
    joa = _joa()
    assert 'detect, never' in joa.seed_aimer_from_detection.__doc__.replace('\n', ' ')


# ─── _pick_chain_continuation: deepest subtree wins ──────────────────

def test_pick_deepest_subtree_wins_over_shallow_leaf():
    joa = _joa()
    # UE-style upperarm: lowerarm (deep chain) vs upperarm_twist_01 (leaf).
    candidates = [('lowerarm', 3, 12.0), ('upperarm_twist_01', 0, 3.0)]
    winner, losers = joa._pick_chain_continuation(candidates)
    assert winner == 'lowerarm', winner
    assert losers == [('upperarm_twist_01', 0, 3.0)], losers


def test_pick_deepest_subtree_wins_even_with_shorter_bone():
    joa = _joa()
    # Depth strictly decides — a longer BUT shallower bone still loses.
    candidates = [('deep_short', 5, 1.0), ('shallow_long', 1, 50.0)]
    winner, losers = joa._pick_chain_continuation(candidates)
    assert winner == 'deep_short', winner
    assert losers == [('shallow_long', 1, 50.0)], losers


# ─── tie on depth -> longest bone tiebreak ───────────────────────────

def test_pick_tiebreaks_on_longest_bone_when_depth_ties():
    joa = _joa()
    candidates = [('a', 2, 5.0), ('b', 2, 8.0), ('c', 0, 99.0)]
    winner, losers = joa._pick_chain_continuation(candidates)
    assert winner == 'b', winner
    assert set(losers) == {('a', 2, 5.0), ('c', 0, 99.0)}, losers


def test_pick_tiebreak_respects_length_tol():
    joa = _joa()
    # Within tol -> still a tie (both count as "longest"), so no unique
    # winner even though the depth-tie already narrowed the field.
    winner, losers = joa._pick_chain_continuation(
        [('a', 2, 5.0000), ('b', 2, 5.00005)], length_tol=1e-4)
    assert winner is None, winner
    assert losers == [('a', 2, 5.0000), ('b', 2, 5.00005)], losers

    # Outside tol -> resolves cleanly.
    winner, losers = joa._pick_chain_continuation(
        [('a', 2, 5.0), ('b', 2, 5.001)], length_tol=1e-4)
    assert winner == 'b', winner


# ─── genuine tie on both -> unresolvable, skip ───────────────────────

def test_pick_genuine_tie_on_both_returns_none():
    joa = _joa()
    candidates = [('a', 2, 5.0), ('b', 2, 5.0)]
    winner, losers = joa._pick_chain_continuation(candidates)
    assert winner is None, winner
    assert losers == candidates, losers


def test_pick_three_way_genuine_tie_returns_none():
    joa = _joa()
    candidates = [('a', 1, 4.0), ('b', 1, 4.0), ('c', 1, 4.0)]
    winner, losers = joa._pick_chain_continuation(candidates)
    assert winner is None, winner
    assert losers == candidates, losers


# ─── edge shapes ──────────────────────────────────────────────────────

def test_pick_empty_candidates_returns_none_empty():
    joa = _joa()
    assert joa._pick_chain_continuation([]) == (None, [])


def test_pick_single_candidate_always_wins():
    joa = _joa()
    winner, losers = joa._pick_chain_continuation([('only', 0, 1.0)])
    assert winner == 'only', winner
    assert losers == [], losers


# ─── _is_default_aimer_state: the overwrite-honesty gate ─────────────

def test_default_state_is_local_with_zero_offset():
    joa = _joa()
    assert joa._is_default_aimer_state(
        {'aim_target': 'Local', 'aim_offset': [0.0, 0.0, 0.0]}) is True


def test_default_state_tolerates_float_noise():
    joa = _joa()
    assert joa._is_default_aimer_state(
        {'aim_target': 'Local', 'aim_offset': [1e-8, -1e-8, 0.0]}) is True


def test_non_default_when_target_is_not_local():
    joa = _joa()
    for target in ('World', 'Parent', 'lowerarm'):
        assert joa._is_default_aimer_state(
            {'aim_target': target, 'aim_offset': [0.0, 0.0, 0.0]}) is False, target


def test_non_default_when_local_but_offset_authored():
    joa = _joa()
    # An authored twist/roll on Local (e.g. a hand-tweaked frame) must
    # still count as non-default — it is exactly the case
    # aim_all_at_child would silently clobber otherwise.
    assert joa._is_default_aimer_state(
        {'aim_target': 'Local', 'aim_offset': [0.0, 0.0, 180.0]}) is False


def test_none_state_counts_as_default():
    joa = _joa()
    # No prior aimer state to lose.
    assert joa._is_default_aimer_state(None) is True


def main():
    check("module_imports_and_public_api_shape",
          test_module_imports_and_public_api_shape)
    check("seed_aimer_from_detection_untouched",
          test_seed_aimer_from_detection_untouched)
    check("pick_deepest_subtree_wins_over_shallow_leaf",
          test_pick_deepest_subtree_wins_over_shallow_leaf)
    check("pick_deepest_subtree_wins_even_with_shorter_bone",
          test_pick_deepest_subtree_wins_even_with_shorter_bone)
    check("pick_tiebreaks_on_longest_bone_when_depth_ties",
          test_pick_tiebreaks_on_longest_bone_when_depth_ties)
    check("pick_tiebreak_respects_length_tol",
          test_pick_tiebreak_respects_length_tol)
    check("pick_genuine_tie_on_both_returns_none",
          test_pick_genuine_tie_on_both_returns_none)
    check("pick_three_way_genuine_tie_returns_none",
          test_pick_three_way_genuine_tie_returns_none)
    check("pick_empty_candidates_returns_none_empty",
          test_pick_empty_candidates_returns_none_empty)
    check("pick_single_candidate_always_wins",
          test_pick_single_candidate_always_wins)
    check("default_state_is_local_with_zero_offset",
          test_default_state_is_local_with_zero_offset)
    check("default_state_tolerates_float_noise",
          test_default_state_tolerates_float_noise)
    check("non_default_when_target_is_not_local",
          test_non_default_when_target_is_not_local)
    check("non_default_when_local_but_offset_authored",
          test_non_default_when_local_but_offset_authored)
    check("none_state_counts_as_default",
          test_none_state_counts_as_default)

    if FAILURES:
        print(f"AIM ALL AT CHILD OFFSCREEN TESTS: {len(FAILURES)} FAILED")
        sys.exit(1)
    print("AIM ALL AT CHILD OFFSCREEN TESTS: OK")


if __name__ == "__main__":
    main()
