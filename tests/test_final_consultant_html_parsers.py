from __future__ import annotations

import unittest
from unittest.mock import patch

from career_engine.sources.adapters.official_html import OfficialHtmlAdapter


class FinalConsultantHtmlParserTests(unittest.TestCase):
    def test_tribepad_paginates_until_saudi_job(self) -> None:
        page1 = """
        <a href='/jobs/job/Engineer/100'>
          <span class='job-list-title'>Engineer</span>
          <span itemprop='address'>London, United Kingdom</span>
        </a>
        <a>Page number 2</a>
        """
        page2 = """
        <a href='/jobs/job/Design-Manager/200'>
          <span class='job-list-title'>Design Manager</span>
          <span itemprop='address'>Riyadh, Saudi Arabia</span>
        </a>
        """
        with patch(
            'career_engine.sources.adapters.official_html.network.fetch_text',
            side_effect=[page1, page2],
        ) as fetch:
            jobs = OfficialHtmlAdapter().search(
                company='Buro Happold|https://vacancies.burohappold.com/jobs/search/-1/|tribepad',
                location='Saudi Arabia',
                limit=25,
            )
        self.assertEqual([(j.role, j.location) for j in jobs], [('Design Manager', 'Riyadh, Saudi Arabia')])
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(fetch.call_args_list[1].args[0], 'https://vacancies.burohappold.com/jobs/search/-1/2')

    def test_wpjm_ignores_expired_saudi_and_keeps_live_listing(self) -> None:
        html = """
        <li class='post-1 job_listing type-job_listing status-publish'>
          <a href='https://mjobs.meinhardtgroup.com/job/urban-planner-designer/'>
            <h3 class='job-listing-loop-job__title'>Urban Planner / Designer</h3>
            <div class='job-location location'>Singapore</div>
          </a></li>
        <li class='post-48530 job_listing type-job_listing status-expired'>
          <a href='https://mjobs.meinhardtgroup.com/?post_type=job_listing&amp;p=48530'>
            <h3 class='job-listing-loop-job__title'>Mechanical Engineer</h3>
            <div class='job-location location'>Saudi Arabia</div>
          </a></li>
        """
        with patch('career_engine.sources.adapters.official_html.network.fetch_text', return_value=html):
            jobs = OfficialHtmlAdapter().search(
                company='Meinhardt|https://mjobs.meinhardtgroup.com/|wpjm',
                location='Saudi Arabia',
                limit=25,
            )
        self.assertEqual(jobs, [])

    def test_wpjm_query_style_live_job_is_accepted(self) -> None:
        html = """
        <li class='post-48530 job_listing type-job_listing status-publish'>
          <a href='https://mjobs.meinhardtgroup.com/?post_type=job_listing&amp;p=48530'>
            <h3 class='job-listing-loop-job__title'>Mechanical Engineer</h3>
            <div class='job-location location'>Saudi Arabia</div>
          </a></li>
        """
        with patch('career_engine.sources.adapters.official_html.network.fetch_text', return_value=html):
            jobs = OfficialHtmlAdapter().search(
                company='Meinhardt|https://mjobs.meinhardtgroup.com/|wpjm',
                location='Saudi Arabia',
            )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].external_job_id, '48530')
        self.assertEqual(jobs[0].role, 'Mechanical Engineer')


if __name__ == '__main__':
    unittest.main()
