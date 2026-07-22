// n2_fd_machine_keyed.rs
// ======================
// NEW EXPERIMENT N2 (part a) -- the NON-VACUOUS FD treatment exp6 dictated.
//
// Instead of defining served() to be the correct lookup (the shipped
// tautology), FD is here a THEOREM about a state machine: invocations RECORD
// values under their ordinals, a SERVE step delivers a value, and the
// invariant "every served ordinal was delivered the value its own invocation
// recorded" is proved INDUCTIVELY over the steps. FD (distinct recorded
// values are served distinctly) is then derived from the invariant.
//
// The non-vacuity certificate is the companion file n2_fd_machine_stock.rs:
// the SAME machine with the serve step replaced by the stock #6663 rule
// (deliver ordinal 0's recorded value to everyone) FAILS its preservation
// lemma. The property can now be false -- so proving it means something.
//
// Verify:  verus n2_fd_machine_keyed.rs
// Expect:  "verification results:: 6 verified, 0 errors"
//          (5 proof items + main; count may shift by one across Verus
//           versions -- the acceptance criterion is 0 errors here AND the
//           preservation failure in the stock file.)
//
// Toolchain: the paper's pin, Verus 0.2026.05.03.8b81855.

use vstd::prelude::*;

verus! {

// ---- State: what invocations recorded, and what serving delivered ----------
pub struct ServeState {
    /// resume ordinal -> value that invocation supplied (durably recorded)
    pub recorded: Map<nat, nat>,
    /// resume ordinal -> value the plane actually delivered to that branch
    pub served: Map<nat, nat>,
}

/// THE invariant: every branch was served the value its own invocation
/// recorded. This is what "resume means resume" means on the fork axis.
pub open spec fn inv(st: ServeState) -> bool {
    forall|o: nat| #[trigger] st.served.dom().contains(o) ==>
        st.recorded.dom().contains(o) && st.served[o] == st.recorded[o]
}

pub open spec fn init() -> ServeState {
    ServeState { recorded: Map::empty(), served: Map::empty() }
}

/// Step 1: an invocation records its value under a fresh ordinal.
pub open spec fn record_step(st: ServeState, o: nat, v: nat) -> ServeState {
    ServeState { recorded: st.recorded.insert(o, v), served: st.served }
}

/// Step 2 (KEYED rule -- REMIT's discipline): serve ordinal o its OWN
/// recorded value.
pub open spec fn serve_keyed_step(st: ServeState, o: nat) -> ServeState {
    ServeState { recorded: st.recorded, served: st.served.insert(o, st.recorded[o]) }
}

// ---- Inductive proof of the invariant --------------------------------------

proof fn lemma_init_inv()
    ensures inv(init()),
{
    assert(init().served.dom() =~= Set::<nat>::empty());
}

proof fn lemma_record_preserves(st: ServeState, o: nat, v: nat)
    requires
        inv(st),
        !st.recorded.dom().contains(o),
    ensures
        inv(record_step(st, o, v)),
{
    let st2 = record_step(st, o, v);
    assert forall|o2: nat| #[trigger] st2.served.dom().contains(o2) implies
        st2.recorded.dom().contains(o2) && st2.served[o2] == st2.recorded[o2] by {
        assert(st.served.dom().contains(o2));
        assert(st.recorded.dom().contains(o2));     // from inv(st)
        assert(o2 != o);                            // o was fresh in recorded
        assert(st2.recorded.dom().contains(o2));
        assert(st2.recorded[o2] == st.recorded[o2]);
        assert(st2.served[o2] == st.served[o2]);
    }
}

proof fn lemma_serve_keyed_preserves(st: ServeState, o: nat)
    requires
        inv(st),
        st.recorded.dom().contains(o),
    ensures
        inv(serve_keyed_step(st, o)),
{
    let st2 = serve_keyed_step(st, o);
    assert forall|o2: nat| #[trigger] st2.served.dom().contains(o2) implies
        st2.recorded.dom().contains(o2) && st2.served[o2] == st2.recorded[o2] by {
        if o2 == o {
            assert(st2.served[o] == st.recorded[o]);
        } else {
            assert(st.served.dom().contains(o2));
            assert(st.recorded.dom().contains(o2)); // from inv(st)
            assert(st2.served[o2] == st.served[o2]);
        }
    }
}

// ---- FD, derived from the invariant (not from a definition) ----------------

/// Fork determinism as a consequence of the inductive invariant: two branches
/// whose invocations recorded distinct values were served distinct values.
proof fn theorem_fd_from_inv(st: ServeState, o1: nat, o2: nat)
    requires
        inv(st),
        st.served.dom().contains(o1),
        st.served.dom().contains(o2),
        st.recorded[o1] != st.recorded[o2],
    ensures
        st.served[o1] != st.served[o2],
{
    assert(st.served[o1] == st.recorded[o1]);
    assert(st.served[o2] == st.recorded[o2]);
}

fn main() {}

} // verus!
