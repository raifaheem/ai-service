import asyncio

_active_streams: set[asyncio.Task] = set()
_shutdown_event = asyncio.Event()
SHUTDOWN_TIMEOUT = 30


def register_stream(task: asyncio.Task) -> None:
    _active_streams.add(task)
    task.add_done_callback(_active_streams.discard)


def active_streams() -> set[asyncio.Task]:
    return _active_streams


def is_shutting_down() -> bool:
    return _shutdown_event.is_set()


def signal_shutdown() -> None:
    _shutdown_event.set()
