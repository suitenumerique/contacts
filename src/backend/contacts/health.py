"""Worker healthcheck.

Each worker consumes a queue of its own container, ``ping.<hostname>``. Only
the worker of that container can answer a ping in this queue. ``python
worker.py --ping`` sends one ping and waits for the answer. An answer shows
that the broker, the consumer, a worker thread and the result backend work.
"""

import logging
import socket
import threading

import dramatiq
import redis
from dramatiq.brokers.redis import RedisBroker
from dramatiq.middleware import Middleware

logger = logging.getLogger(__name__)

PING_QUEUE = f"ping.{socket.gethostname()}"

# How long the prober waits for the answer (milliseconds).
PING_TIMEOUT = 5_000

# How long is_alive() waits for its probe thread (milliseconds). PING_TIMEOUT
# does not limit a call to Redis or to the database that never returns.
PROBE_DEADLINE = 6_000


@dramatiq.actor(
    queue_name=PING_QUEUE,
    # A worker process runs its pending task with the lowest number first.
    # The other tasks have priority 0. The priority does not stop a task in
    # progress.
    priority=-1,
    store_results=True,
    max_retries=0,
    # The prober does not wait for a ping that is this old (milliseconds).
    max_age=10_000,
    result_ttl=30_000,  # milliseconds
)
def ping():
    """Answer the healthcheck."""
    return "pong"


def is_alive():
    """Send a ping to the worker of this container.

    Return True if the worker answers before PROBE_DEADLINE, False if not.
    """
    alive = False

    def probe():
        nonlocal alive
        try:
            alive = ping.send().get_result(block=True, timeout=PING_TIMEOUT) == "pong"
        # Any failure means that the worker is not healthy.
        except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            logger.warning("The worker did not answer the ping: %r", exc)

    # A daemon thread does not keep the process alive. The caller can exit
    # while a call of the probe still blocks.
    thread = threading.Thread(target=probe, daemon=True)
    thread.start()
    thread.join(PROBE_DEADLINE / 1000)
    if thread.is_alive():
        logger.warning("The ping did not finish in %d ms.", PROBE_DEADLINE)
        return False
    return alive


class DropPingQueue(Middleware):
    """Delete the ping queue of this container when its worker stops.

    The queue can hold pings that no worker fetched. Its dead-letter queue
    can hold pings that became too old. Each container has its own queue.
    Without the cleanup, these keys stay in Redis after the container is
    gone. The cleanup runs when the worker stops normally. It does not run
    when the container crashes.
    """

    def after_worker_shutdown(self, broker, worker):
        """Delete the Redis keys of the ping queue."""
        try:
            broker.flush(PING_QUEUE)
        except redis.RedisError:
            logger.warning("Could not delete the queue %s.", PING_QUEUE, exc_info=True)


_broker = dramatiq.get_broker()
# The eager broker of the tests has no Redis client and nothing to delete.
if isinstance(_broker, RedisBroker):
    _broker.add_middleware(DropPingQueue())
