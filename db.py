import logging
from datetime import datetime, timezone

from bson import ObjectId
from pymongo import MongoClient, DESCENDING
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError
from config import MONGO_URI, DB_NAME

logger = logging.getLogger(__name__)

_client = None

# Default section map — mirrors the hard-coded dict in solver.py.
# Stored here so db.py and solver.py share the same authoritative default.
_DEFAULT_SECTION_MAP = {
    "1": ["1A", "1B", "1C", "1K"],
    "2": ["2A", "2B", "2C", "2K"],
    "3": ["3A", "3B", "3C", "3D"],
    "4": ["4A", "4B", "4C", "4D"],
    "5": ["5A", "5B", "5C", "5D"],
    "6": ["6A", "6B", "6C", "6D"],
    "7": ["7A", "7B", "7C"],
    "8": ["8A", "8B", "8C"],
}


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


# ---------------------------------------------------------------------------
# Timetable result persistence (#7)
# ---------------------------------------------------------------------------

def save_timetable_result(semester: str, result: dict) -> str:
    """Persist a solver result to the ``timetables`` collection.

    Stores timetable grids, faculty timetables, workload summary, and
    solver stats. Returns the inserted document _id as a string.
    """
    db = get_db()
    doc = {
        "semester": semester,
        "generated_at": datetime.now(timezone.utc),
        "status": result.get("status"),
        "stats": result.get("stats", {}),
        "timetables": result.get("timetables", {}),
        "faculty_timetables": result.get("faculty_timetables", {}),
        "workload": result.get("workload", {}),
    }
    inserted = db["timetables"].insert_one(doc)
    return str(inserted.inserted_id)


def list_timetable_results(semester: str) -> list:
    """Return saved result metadata (no grids) for *semester*, newest first.

    Each item: ``{"id", "generated_at", "status", "stats"}``.
    """
    db = get_db()
    cursor = db["timetables"].find(
        {"semester": semester},
        {"timetables": 0, "faculty_timetables": 0, "workload": 0},
        sort=[("generated_at", DESCENDING)],
        limit=20,
    )
    results = []
    for doc in cursor:
        results.append({
            "id": str(doc["_id"]),
            "generated_at": doc.get("generated_at"),
            "status": doc.get("status", "?"),
            "stats": doc.get("stats", {}),
        })
    return results


def load_timetable_result(result_id: str) -> dict | None:
    """Load a full timetable result by its string _id.  Returns None on miss."""
    db = get_db()
    try:
        doc = db["timetables"].find_one({"_id": ObjectId(result_id)}, {"_id": 0})
    except Exception:
        return None
    if doc is None:
        return None
    doc.pop("generated_at", None)
    doc.pop("semester", None)
    return doc


def delete_timetable_result(result_id: str) -> bool:
    """Delete a saved timetable result by id.  Returns True if deleted."""
    db = get_db()
    try:
        res = db["timetables"].delete_one({"_id": ObjectId(result_id)})
        return res.deleted_count > 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# App settings (#10)
# ---------------------------------------------------------------------------

def get_settings() -> dict:
    """Return the app settings document, or {} if none saved yet."""
    try:
        db = get_db()
        return db["settings"].find_one({"type": "app_settings"}, {"_id": 0}) or {}
    except Exception:
        return {}


def save_settings(settings: dict):
    """Upsert the app settings document."""
    db = get_db()
    settings["type"] = "app_settings"
    db["settings"].update_one({"type": "app_settings"}, {"$set": settings}, upsert=True)


def get_section_map() -> dict:
    """Return the semester→[sections] map from DB, falling back to defaults.

    The DB value is set via the Settings page (pages/0_Settings.py).
    """
    try:
        settings = get_settings()
        section_map = settings.get("section_map")
        if section_map and isinstance(section_map, dict) and section_map:
            return section_map
    except Exception:
        pass
    return dict(_DEFAULT_SECTION_MAP)
