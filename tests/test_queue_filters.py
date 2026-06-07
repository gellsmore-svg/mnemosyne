from tirzah.db.queue import bounded_limit, job_filter_query


def test_job_filter_query_combines_status_reason_and_text_filters() -> None:
    query = job_filter_query(
        status="failed",
        reason="source_missing",
        query_text="data/ingest/a.md",
    )

    assert query["status"] == "failed"
    assert query["reason"] == "source_missing"
    assert query["$or"][0]["path"].pattern == "data/ingest/a\\.md"
    assert query["$or"][1]["checksum_sha256"].pattern == "data/ingest/a\\.md"
    assert query["$or"][2]["details.dead_letter_path"].pattern == "data/ingest/a\\.md"
    assert not any("reason" in item for item in query["$or"])


def test_job_filter_query_searches_reason_when_exact_reason_is_absent() -> None:
    query = job_filter_query(query_text="duplicate")

    assert query["$or"][-1]["reason"].pattern == "duplicate"


def test_job_filter_limit_is_bounded() -> None:
    assert bounded_limit(0) == 1
    assert bounded_limit(500) == 100
