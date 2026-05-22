import logging

from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError
from config import MONGO_URI, DB_NAME

logger = logging.getLogger(__name__)

_client = None


def get_client():
    """Return a cached MongoClient instance.

    Configures sensible timeouts so the app fails fast instead of
    hanging when MongoDB is unreachable.
    """
    global _client
    if _client is None:
        _client = MongoClient(
            MONGO_URI,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            socketTimeoutMS=10000,
            retryWrites=True,
        )
        # Verify the connection is alive on first use
        try:
            _client.admin.command("ping")
            logger.info("Connected to MongoDB at %s", DB_NAME)
        except (ConnectionFailure, ServerSelectionTimeoutError) as e:
            _client = None
            raise ConnectionError(
                f"Cannot connect to MongoDB ({MONGO_URI[:30]}…): {e}"
            ) from e
    return _client


def get_db():
    """Return the default database."""
    return get_client()[DB_NAME]
