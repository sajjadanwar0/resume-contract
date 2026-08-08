use vstd::prelude::*;

verus! {

pub open spec fn served_keyed(supplied: Map<nat, nat>, ordinal: nat) -> nat
    recommends supplied.dom().contains(ordinal)
{
    supplied[ordinal]
}

pub open spec fn served_stock(supplied: Map<nat, nat>, ordinal: nat) -> nat
    recommends supplied.dom().contains(0)
{
    supplied[0]
}

proof fn fd_keyed(supplied: Map<nat, nat>, o1: nat, o2: nat)
    requires
        supplied.dom().contains(o1),
        supplied.dom().contains(o2),
        supplied[o1] != supplied[o2],
    ensures
        served_keyed(supplied, o1) != served_keyed(supplied, o2),
{
}

proof fn fd_stock(supplied: Map<nat, nat>, o1: nat, o2: nat)
    requires
        supplied.dom().contains(0),
        supplied.dom().contains(o1),
        supplied.dom().contains(o2),
        supplied[o1] != supplied[o2],
    ensures
        served_stock(supplied, o1) != served_stock(supplied, o2),
{
}

fn main() {}

}
