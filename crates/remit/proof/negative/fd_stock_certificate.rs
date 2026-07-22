// n2_fd_machine_stock.rs
// ======================
// NEW EXPERIMENT N2 (part b) -- the NON-VACUITY CERTIFICATE.
//
// Identical state machine and invariant to n2_fd_machine_keyed.rs, with one
// change: the serve step implements the STOCK LangGraph #6663 precedence --
// every invocation is delivered ordinal 0's recorded value, regardless of
// what it supplied. The invariant-preservation lemma for this step is FALSE
// (take recorded[o] != recorded[0]), so Verus must reject it.
//
// Verify:  verus n2_fd_machine_stock.rs
// Expect:  "verification results:: N verified, 1 errors" with
//          "postcondition not satisfied" at lemma_serve_stock_preserves.
//          (N counts the passing items incl. main().)
//
// Together with part a this is what exp6 demanded: an FD statement that the
// buggy rule FALSIFIES at the step where the bug lives -- so the keyed
// machine's clean discharge is a proof about a mechanism, not a definition
// restated. This pair belongs in crates/remit/proof/ in place of the
// tautological lemma_fd_distinct_values_served_distinctly.
//
// Toolchain: the paper's pin, Verus 0.2026.05.03.8b81855.

use vstd::prelude::*;

verus! {

pub struct ServeState {
    pub recorded: Map<nat, nat>,
    pub served: Map<nat, nat>,
}

pub open spec fn inv(st: ServeState) -> bool {
    forall|o: nat| #[trigger] st.served.dom().contains(o) ==>
        st.recorded.dom().contains(o) && st.served[o] == st.recorded[o]
}

pub open spec fn init() -> ServeState {
    ServeState { recorded: Map::empty(), served: Map::empty() }
}

pub open spec fn record_step(st: ServeState, o: nat, v: nat) -> ServeState {
    ServeState { recorded: st.recorded.insert(o, v), served: st.served }
}

/// The STOCK rule (#6663): whatever ordinal asks, deliver ordinal 0's
/// recorded value -- the first answer, re-served forever.
pub open spec fn serve_stock_step(st: ServeState, o: nat) -> ServeState {
    ServeState { recorded: st.recorded, served: st.served.insert(o, st.recorded[0]) }
}

proof fn lemma_init_inv()
    ensures inv(init()),
{
    assert(init().served.dom() =~= Set::<nat>::empty());
}

/// EXPECTED TO FAIL: with recorded[o] != recorded[0], the freshly served
/// ordinal o violates the invariant clause served[o] == recorded[o].
/// Verus's rejection of THIS lemma is the certificate that the invariant --
/// and hence the keyed machine's FD theorem -- has real content.
proof fn lemma_serve_stock_preserves(st: ServeState, o: nat)
    requires
        inv(st),
        st.recorded.dom().contains(0),
        st.recorded.dom().contains(o),
    ensures
        inv(serve_stock_step(st, o)),
{
}

fn main() {}

} // verus!
