"""Worker healthcheck.

Each worker consumes a queue of its own container, ``ping.<hostname>``. Only
the worker of that container can answer a ping sent to this queue. ``python
worker.py --ping`` sends one ping and waits for the answer. An answer shows
that the broker, the consumer, a worker thread and the result backend work.
"""

import logging
import socket

import dramatiq
import redis
from dramatiq.brokers.redis import RedisBroker
from dramatiq.middleware import Middleware

logger = logging.getLogger(__name__)

PING_QUEUE = f"ping.{socket.gethostname()}"

# How long the prober waits for the answer (milliseconds).
PING_TIMEOUT = 5_000


@dramatiq.actor(
    queue_name=PING_QUEUE,
    # A lower number runs first. The other tasks have priority 0.
    priority=-1,
    store_results=True,
    max_retries=0,
    # The prober has stopped waiting when a ping is this old (milliseconds).
    max_age=10_000,
    result_ttl=30_000,  # milliseconds
)
def ping():
    """Answer the healthcheck."""
    return "pong"


def is_alive():
    """Send a ping to the worker of this container and wait for its answer."""
    try:
        result = ping.send().get_result(block=True, timeout=PING_TIMEOUT)
    # Any failure means that the worker is not healthy.
    except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        logger.warning("The worker did not answer the ping: %r", exc)
        return False
    return result == "pong"


class DropPingQueue(Middleware):
    """Delete the ping queue of this container when its worker stops.

    A ping that the worker does not answer in time stays in the queue or in
    its dead-letter queue. Each container has its own queue, so these keys
    would stay in Redis after the container is gone. A container that crashes
    does not run this cleanup.
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
