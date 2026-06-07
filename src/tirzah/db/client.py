from __future__ import annotations

from pymongo import MongoClient
from pymongo.database import Database

from tirzah.config import MongoConfig


def get_database(config: MongoConfig) -> Database:
    client: MongoClient = MongoClient(config.uri, serverSelectionTimeoutMS=3000)
    client.admin.command("ping")
    return client[config.database]
