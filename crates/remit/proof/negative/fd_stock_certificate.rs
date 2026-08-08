
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

pub open spec fn serve_stock_step(st: ServeState, o: nat) -> ServeState {
    ServeState { recorded: st.recorded, served: st.served.insert(o, st.recorded[0]) }
}

proof fn lemma_init_inv()
    ensures inv(init()),
{
    assert(init().served.dom() =~= Set::<nat>::empty());
}

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

}
