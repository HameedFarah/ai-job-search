"""Regression coverage for Red Sea Global / SuccessFactors JD extraction."""

from __future__ import annotations

from career_engine.sources.adapters.successfactors import (
    SuccessFactorsAdapter,
    _JobDescriptionTextParser,
)


def test_successfactors_accepts_block_jobdescription_root() -> None:
    parser = _JobDescriptionTextParser()
    parser.feed(
        """
        <div class="jobdescription">
          <h2>Job Purpose</h2>
          <p>Support reception and retail operations while maintaining service standards.</p>
          <h2>Job Responsibilities</h2>
          <ul><li>Coordinate daily guest-facing activities.</li><li>Maintain operational records.</li></ul>
          <h2>Qualification and Experience</h2>
          <p>Relevant professional experience is required.</p>
          <h2>Essential Skills</h2>
          <p>Communication and stakeholder coordination.</p>
        </div>
        """
    )
    text = parser.text()
    assert "Job Purpose" in text
    assert "Job Responsibilities" in text
    assert "Qualification and Experience" in text
    assert "Essential Skills" in text
    assert "Coordinate daily guest-facing activities" in text


def test_successfactors_search_keeps_full_block_root_detail(monkeypatch) -> None:
    adapter = SuccessFactorsAdapter()
    monkeypatch.setattr(
        adapter,
        "_enumerate",
        lambda base, requested, offline: [
            (
                "857334523",
                "Specialist - Reception and Retail",
                "/job/Specialist-Reception-and-Retail/857334523/",
            )
        ],
    )
    monkeypatch.setattr(
        adapter,
        "_load_detail",
        lambda url, offline: """
          <div class="jobdescription">
            <h2>Job Purpose</h2><p>Support reception and retail operations.</p>
            <h2>Job Responsibilities</h2><p>Coordinate guest-facing activities and records.</p>
            <h2>Qualification and Experience</h2><p>Relevant experience required.</p>
            <h2>Essential Skills</h2><p>Communication and coordination.</p>
          </div>
        """,
    )

    jobs = adapter.search(
        company="Red Sea Global|https://careers.theredsea.sa/",
        limit=1,
        fetch_full=True,
        offline=False,
    )

    assert len(jobs) == 1
    job = jobs[0]
    assert job.external_job_id == "857334523"
    assert len(job.description_text) > 150
    assert "Job Responsibilities" in job.description_text
    assert "Qualification and Experience" in job.description_text
    assert not job.extra.get("detail_fetch_error")
