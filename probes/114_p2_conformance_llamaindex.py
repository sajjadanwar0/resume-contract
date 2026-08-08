import asyncio
import json
import traceback
from importlib.metadata import version
from workflows import Workflow, Context, step
from workflows.events import (
    StartEvent,
    StopEvent,
    InputRequiredEvent,
    HumanResponseEvent,
)

RESULTS = {"llama_index_workflows_version": version("llama-index-workflows")}

def make_wf_A(eff):
    class WA(Workflow):
        @step
        async def pre(self, ctx: Context, ev: StartEvent) -> InputRequiredEvent:
            eff["pre"] += 1
            await ctx.store.set("charge_id", f"ch_{eff['pre']:03d}")

            return InputRequiredEvent(prefix="approve?")

        @step
        async def post(self, ctx: Context, ev: HumanResponseEvent) -> StopEvent:
            eff["post"] += 1
            cid = await ctx.store.get("charge_id")

            return StopEvent(result=f"{cid}:answer={ev.response}")

    return WA(timeout=20)

async def a1_snapshot_resume():
    eff = {"pre": 0, "post": 0}
    wf = make_wf_A(eff)
    handler = wf.run()
    snapshot = None

    async for ev in handler.stream_events():
        if isinstance(ev, InputRequiredEvent):
            snapshot = handler.ctx.to_dict()
            await handler.cancel_run()
            break

    try:
        await handler
    except Exception:
        pass

    pre_at_snapshot = eff["pre"]

    ctx2 = Context.from_dict(wf, snapshot)
    h2 = wf.run(ctx=ctx2)
    h2.ctx.send_event(HumanResponseEvent(response="YES"))
    res = await asyncio.wait_for(h2, timeout=20)

    return {
        "pre_execs_at_snapshot": pre_at_snapshot,
        "pre_execs_total": eff["pre"],
        "post_execs_total": eff["post"],
        "result": str(res),
        "violation_PC_EO_pre_reexecuted": eff["pre"] > pre_at_snapshot,
    }

async def a2_fork_two_answers():
    eff = {"pre": 0, "post": 0}
    wf = make_wf_A(eff)
    handler = wf.run()
    snapshot = None

    async for ev in handler.stream_events():
        if isinstance(ev, InputRequiredEvent):
            snapshot = handler.ctx.to_dict()
            await handler.cancel_run()
            break
    try:
        await handler
    except Exception:
        pass

    async def resume_with(ans):
        ctx = Context.from_dict(wf, snapshot)
        h = wf.run(ctx=ctx)
        h.ctx.send_event(HumanResponseEvent(response=ans))

        return str(await asyncio.wait_for(h, timeout=20))

    r_yes = await resume_with("YES")
    r_no = await resume_with("NO")

    return {
        "resume_YES": r_yes,
        "resume_NO": r_no,
        "violation_FD_second_answer_ignored": ("YES" in r_no) or (r_yes == r_no),
    }

async def a3_dual_response_live():
    eff = {"pre": 0, "post": 0}
    wf = make_wf_A(eff)
    handler = wf.run()
    async for ev in handler.stream_events():
        if isinstance(ev, InputRequiredEvent):
            handler.ctx.send_event(HumanResponseEvent(response="FIRST"))
            handler.ctx.send_event(HumanResponseEvent(response="SECOND"))
            break
    res = await asyncio.wait_for(handler, timeout=20)

    return {
        "post_execs": eff["post"],
        "result": str(res),
        "violation_CO_post_fired_twice": eff["post"] > 1,
    }

def make_wf_B(eff):
    class WB(Workflow):
        @step
        async def only(self, ctx: Context, ev: StartEvent) -> StopEvent:
            eff["prefix"] += 1
            resp = await ctx.wait_for_event(
                HumanResponseEvent,
                waiter_id="approval-1",
                waiter_event=InputRequiredEvent(prefix="approve?"),
            )
            eff["suffix"] += 1
            return StopEvent(result=f"answer={resp.response}")

    return WB(timeout=20)

async def b1_wait_for_event_restore():
    eff = {"prefix": 0, "suffix": 0}
    wf = make_wf_B(eff)
    handler = wf.run()
    snapshot = None

    async for ev in handler.stream_events():
        if isinstance(ev, InputRequiredEvent):
            snapshot = handler.ctx.to_dict()
            await handler.cancel_run()
            break

    try:
        await handler
    except Exception:
        pass
    prefix_at_snapshot = eff["prefix"]

    ctx2 = Context.from_dict(wf, snapshot)
    h2 = wf.run(ctx=ctx2)
    h2.ctx.send_event(HumanResponseEvent(response="YES"))
    res = await asyncio.wait_for(h2, timeout=20)

    return {
        "prefix_execs_at_snapshot": prefix_at_snapshot,
        "prefix_execs_total": eff["prefix"],
        "suffix_execs_total": eff["suffix"],
        "result": str(res),
        "violation_PC_EO_prefix_reexecuted": eff["prefix"] > prefix_at_snapshot,
    }

async def c1_nonserializable_store():
    eff = {"pre": 0, "post": 0}
    wf = make_wf_A(eff)
    handler = wf.run()
    outcome = {}

    async for ev in handler.stream_events():
        if isinstance(ev, InputRequiredEvent):
            await handler.ctx.store.set("bad", lambda x: x)
            try:
                snap = handler.ctx.to_dict()
                outcome["to_dict"] = "succeeded"
                try:
                    Context.from_dict(wf, snap)
                    outcome["from_dict"] = "succeeded"
                except Exception as e:
                    outcome["from_dict"] = f"raised {type(e).__name__}"
                outcome["bad_key_in_snapshot"] = "bad" in json.dumps(snap, default=str)
            except Exception as e:
                outcome["to_dict"] = f"raised {type(e).__name__}"
            await handler.cancel_run()
            break
    try:
        await handler
    except Exception:
        pass

    outcome["violation_CV_silent_corruption"] = (
        outcome.get("to_dict") == "succeeded"
        and outcome.get("from_dict", "").startswith("raised")
    )

    return outcome

async def main():
    tests = {
        "A1_snapshot_resume_PC_EO": a1_snapshot_resume,
        "A2_fork_determinism_FD": a2_fork_two_answers,
        "A3_dual_response_CO": a3_dual_response_live,
        "B1_wait_for_event_restore_PC_EO": b1_wait_for_event_restore,
        "C1_nonserializable_store_CV": c1_nonserializable_store,
    }
    for name, fn in tests.items():
        try:
            RESULTS[name] = await fn()
        except Exception:
            RESULTS[name] = {"probe_error": traceback.format_exc(limit=4)}
    print(json.dumps(RESULTS, indent=2, default=str))

asyncio.run(main())
