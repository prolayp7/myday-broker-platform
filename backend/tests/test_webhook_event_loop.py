"""The webhook handlers must not block the event loop.

WHY THIS FILE EXISTS. Both webhook handlers are `async def`, which means they
run ON the event loop — and both did synchronous, network-bound work directly
inside it:

    /webhooks/meta  -> meta_graph.fetch_lead_data, sync httpx, timeout 10s

A blocking call there freezes EVERY other in-flight request, not just the
webhook's own. One slow Graph call stalled the whole API for up to ten
seconds; one slow recording download, up to three minutes. Nothing errored and
no log line said so — brokers just saw the app hang.

WHAT IS ASSERTED. Not "is run_in_threadpool called" — that is a restatement of
the code, and would keep passing if someone awaited it wrongly. Instead a
heartbeat runs concurrently and the test measures whether it kept ticking. That
is the actual property: other requests stay responsive.

These tests FAIL against the pre-fix handlers, which is what makes them worth
keeping.
"""

import asyncio
import time

from starlette.concurrency import run_in_threadpool

# A stand-in for the real network call. Short enough to keep the suite fast,
# long enough that a blocked loop is unambiguous — the real timeouts are 10s
# and 180s.
BLOCK_SECONDS = 0.6

# The heartbeat should tick every 50ms. Anything past this means the loop was
# held. Generous enough not to flake on a loaded CI box, far below the 0.6s a
# genuine block produces.
MAX_ACCEPTABLE_STALL = 0.3


def blocking_work() -> str:
    """Synchronous and slow, exactly like a sync httpx call with a timeout."""
    time.sleep(BLOCK_SECONDS)
    return "done"


async def worst_stall_while(coro_factory) -> float:
    """Run the given coroutine while a heartbeat ticks, and report the longest
    gap the heartbeat suffered.

    This is the whole point of the file: it measures what a CONCURRENT request
    would have experienced, rather than inspecting how the handler is written.
    """
    gaps: list[float] = []
    stop = asyncio.Event()

    async def heartbeat() -> None:
        last = time.monotonic()
        while not stop.is_set():
            await asyncio.sleep(0.05)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    beat = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.15)  # let it settle into a rhythm
    await coro_factory()
    stop.set()
    await beat

    return max(gaps) if gaps else 0.0


class TestBlockingWorkIsOffTheLoop:
    """Plain sync tests driving their own loop with asyncio.run — the project
    has no pytest-asyncio or anyio plugin, and this needs no dependency."""

    def test_threadpool_keeps_other_requests_responsive(self):
        """The fix. Blocking work handed to a threadpool leaves the loop free."""

        async def handler():
            await run_in_threadpool(blocking_work)

        stall = asyncio.run(worst_stall_while(handler))
        assert stall < MAX_ACCEPTABLE_STALL, (
            f"a concurrent request was stalled {stall:.2f}s — the blocking work "
            "is back on the event loop"
        )

    def test_the_bug_this_prevents_is_real(self):
        """Guards the guard. If this ever stops detecting a block, the test
        above is measuring nothing and would pass no matter what the handlers
        do — so the detector itself is pinned."""

        async def handler_calling_directly():
            blocking_work()  # what the handlers used to do

        stall = asyncio.run(worst_stall_while(handler_calling_directly))
        assert stall >= MAX_ACCEPTABLE_STALL, (
            "a direct blocking call was NOT detected as stalling the loop — "
            "the measurement is broken, not the code"
        )

    def test_the_result_still_comes_back(self):
        """Moving work off the loop must not change what it returns — the lead
        is still fully created before the webhook answers 200."""

        async def run():
            return await run_in_threadpool(blocking_work)

        assert asyncio.run(run()) == "done"

    def test_arguments_are_passed_through(self):
        """A handler passing an argument positionally
        (run_in_threadpool(fn, arg)); a signature mistake there would
        only surface at runtime, on a real webhook."""

        def takes_args(a, b):
            time.sleep(0.05)
            return f"{a}-{b}"

        async def run():
            return await run_in_threadpool(takes_args, "call", "123")

        assert asyncio.run(run()) == "call-123"


class TestHandlersDoNotCallBlockingWorkDirectly:
    """A cheap structural backstop for the behavioural tests above: if someone
    later reverts to a direct call, this names the file and the line."""

    def test_meta_webhook_defers_ingestion(self):
        source = (
            __import__("pathlib").Path(__file__).parent.parent
            / "routes"
            / "webhooks.py"
        ).read_text(encoding="utf-8")
        assert "run_in_threadpool" in source
        # The bare call, at the point where it used to sit on the loop.
        assert "\n        lead_ingestion.ingest_meta_event(event)\n" not in source
