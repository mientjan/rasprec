"""Subprocess cleanup shared by streaming readers and thumbnail jobs."""

import asyncio


async def reap(proc):
    if proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass

    async def drain(stream):
        while await stream.read(65536):
            pass

    # A killed child can still have a paused pipe reader. Drain buffered output
    # rather than waiting forever for pipe closure; memory stays chunk-bounded.
    await asyncio.gather(
        proc.wait(),
        *(
            drain(stream)
            for stream in (getattr(proc, "stdout", None), getattr(proc, "stderr", None))
            if stream is not None and hasattr(stream, "read")
        ),
    )


async def stop_process(proc):
    # Shield cleanup from cancellation so a cancelled request cannot leak FFmpeg.
    task = asyncio.create_task(reap(proc))
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise
