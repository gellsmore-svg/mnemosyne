from datetime import datetime, timezone

from mnemosyne.db.governance import (
    bounded_governance_limit,
    get_agent_identity,
    list_agent_identities,
    list_trust_weighting_profiles,
)
from mnemosyne.db.indexes import seed_governance_defaults


def test_seed_governance_defaults_creates_shared_identity_and_weighting_profile() -> None:
    db = FakeDb()

    seed_governance_defaults(db)

    assert db.agent_identities.rows[0]["identity_id"] == "mnemosyne_shared"
    assert db.agent_identities.rows[0]["kind"] == "shared"
    assert db.agent_identities.rows[0]["weighting_profile_id"] == "default_balanced"
    assert db.trust_weighting_profiles.rows[0]["weighting_profile_id"] == "default_balanced"


def test_list_governance_rows_sorts_limits_and_serializes_dates() -> None:
    db = FakeDb()
    timestamp = datetime(2026, 5, 28, tzinfo=timezone.utc)
    db.agent_identities.rows.extend(
        [
            {"identity_id": "zeta", "updated_at": timestamp},
            {"identity_id": "alpha", "updated_at": timestamp},
        ]
    )

    rows = list_agent_identities(db, limit=1)

    assert rows == [{"identity_id": "alpha", "updated_at": "2026-05-28T00:00:00+00:00"}]


def test_get_governance_row_returns_serialized_match_or_none() -> None:
    db = FakeDb()
    timestamp = datetime(2026, 5, 28, tzinfo=timezone.utc)
    db.agent_identities.rows.append(
        {"_id": "internal", "identity_id": "mnemosyne_shared", "updated_at": timestamp}
    )

    assert get_agent_identity(db, "mnemosyne_shared") == {
        "identity_id": "mnemosyne_shared",
        "updated_at": "2026-05-28T00:00:00+00:00",
    }
    assert get_agent_identity(db, "missing") is None
    assert get_agent_identity(db, "") is None


def test_list_trust_weighting_profiles_uses_profile_sort_key() -> None:
    db = FakeDb()
    db.trust_weighting_profiles.rows.extend(
        [
            {"weighting_profile_id": "stable"},
            {"weighting_profile_id": "default_balanced"},
        ]
    )

    rows = list_trust_weighting_profiles(db)

    assert [row["weighting_profile_id"] for row in rows] == ["default_balanced", "stable"]


def test_bounded_governance_limit_clamps_values() -> None:
    assert bounded_governance_limit(0) == 1
    assert bounded_governance_limit(999) == 100
    assert bounded_governance_limit("bad") == 20


class FakeCursor(list):
    def sort(self, field, direction=1):
        super().sort(key=lambda row: row.get(field), reverse=direction < 0)
        return self

    def limit(self, value):
        return FakeCursor(self[:value])


class FakeCollection:
    def __init__(self):
        self.rows = []

    def find(self, _query, projection=None):
        rows = []
        for row in self.rows:
            if projection and projection.get("_id") == 0:
                rows.append({key: value for key, value in row.items() if key != "_id"})
            else:
                rows.append(dict(row))
        return FakeCursor(rows)

    def find_one(self, query, projection=None):
        row = next((item for item in self.rows if matches(item, query)), None)
        if row is None:
            return None
        if projection and projection.get("_id") == 0:
            return {key: value for key, value in row.items() if key != "_id"}
        return dict(row)

    def update_one(self, query, update, upsert=False):
        row = next((item for item in self.rows if matches(item, query)), None)
        if row is None:
            if not upsert:
                return None
            row = {**query, **update.get("$setOnInsert", {})}
            self.rows.append(row)
        row.update(update.get("$set", {}))
        return None


class FakeDb:
    def __init__(self):
        self.agent_identities = FakeCollection()
        self.trust_weighting_profiles = FakeCollection()


def matches(row, query):
    return all(row.get(key) == value for key, value in query.items())
