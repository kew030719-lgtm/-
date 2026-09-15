"""Read-only audit of a local discovery run; prints no resume content."""
import argparse
import json
import re

from career_radar.config import Settings
from career_radar.database import Database
from career_radar.resume import validate_citations


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('run_id')
    args = parser.parse_args()
    db = Database(Settings.load().database_path)
    task = db.get_task(args.run_id)
    jobs = db.list_snapshots(args.run_id)
    profile = db.get_profile(task['payload']['profile_id'])
    with db.connect() as conn:
        rows = conn.execute("SELECT id,status FROM tasks WHERE kind='comparison' AND json_extract(payload,'$.run_id')=? ORDER BY created_at DESC", (args.run_id,)).fetchall()
    reports = []
    for row in rows:
        report = db.get_comparison_by_task(row['id'])
        record = dict(row)
        if report:
            refs = [ref for ranking in report.rankings for ref in ranking.citations]
            validate_citations(refs, profile.evidence + [b for j in jobs for b in j.blocks])
            record.update(source=report.source, rankings=len(report.rankings), plan_days=len(report.action_plan), citations=len(refs), citations_valid=True)
        reports.append(record)
    result = {
        'run_id': args.run_id, 'status': task['status'], 'message': task['message'],
        'jobs': len(jobs), 'unique_urls': len({j.canonical_url for j in jobs}),
        'unique_platform_ids': len({j.platform_job_id for j in jobs}),
        'fields_present': {field: sum(bool(getattr(j, field)) for j in jobs) for field in ['title','company','salary','experience','education','responsibilities','required_skills']},
        'suspect_companies': [j.snapshot_id for j in jobs if re.search(r'\d+-\d+K', j.company)],
        'suspect_descriptions': [j.snapshot_id for j in jobs if any(word in ' '.join(j.responsibilities) for word in ['公司名称 ', '看过该职位的人还看了', '个人中心'])],
        'all_evidence_in_snapshot': all(b.quote in j.cleaned_text for j in jobs for b in j.blocks),
        'reports': reports,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
