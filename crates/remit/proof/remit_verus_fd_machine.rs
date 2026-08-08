
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

pub open spec fn serve_keyed_step(st: ServeState, o: nat) -> ServeState {
    ServeState { recorded: st.recorded, served: st.served.insert(o, st.recorded[o]) }
}

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
        assert(st.recorded.dom().contains(o2));
        assert(o2 != o);
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
            assert(st.recorded.dom().contains(o2));
            assert(st2.served[o2] == st.served[o2]);
        }
    }
}

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

}
