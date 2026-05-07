"""Integration tests for graceful shutdown of SSE streams."""
import asyncio

from app import lifecycle


class TestStreamRegistration:
    def test_register_stream_tracks_task(self):
        async def _run():
            task = asyncio.current_task()
            assert task is not None
            lifecycle.register_stream(task)
            assert task in lifecycle.active_streams()

        asyncio.run(_run())

    def test_task_auto_discarded_on_completion(self):
        async def _stream_work():
            task = asyncio.current_task()
            assert task is not None
            lifecycle.register_stream(task)

        async def _run():
            task = asyncio.create_task(_stream_work())
            await task
            # done callbacks run on the next loop iteration
            await asyncio.sleep(0)
            assert task not in lifecycle.active_streams()

        asyncio.run(_run())

    def test_shutdown_signal_visible_everywhere(self):
        async def _run():
            assert lifecycle.is_shutting_down() is False
            lifecycle.signal_shutdown()
            assert lifecycle.is_shutting_down() is True
            lifecycle._shutdown_event.clear()
            assert lifecycle.is_shutting_down() is False

        asyncio.run(_run())
