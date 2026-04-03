from pymongo import MongoClient
from config import MONGO_URI, DB_NAME

_client = None


def get_client():
    """Return a cached MongoClient instance."""
    global _client
    if _client is None:
        _client = MongoClient(MONGO_URI)
    return _client


def get_db():
    """Return the default database."""
    return get_client()[DB_NAME]
