"""Reparse legacy job-only fields from retained text; back up SQLite before edits."""
import argparse
import hashlib
import re
import sqlite3
from datetime import datetime

from career_radar.config import Settings
from career_radar.database import Database
from career_radar.schemas import Evidence, JobSnapshot


def repair(job):
    source = job.cleaned_text
    company = re.search(r'^「.+?招聘」_(.+?)招聘-BOSS直聘', source)
    gap = r'\s*(?:(?:来自BOSS直聘|BOSS直聘|boss|kanzhun|直聘)\s*)?'
    description = re.search(r'职' + gap + r'位' + gap + r'描' + gap + r'述\s+(.+?)\s+竞争力分析', source)
    if not company or not description:
        return None
    text = description.group(1)
    text = re.split(r'\s+认证资质\s+|\s+其它信息\s+', text)[0]
    text = re.sub(r'\s+[\u4e00-\u9fff]{1,4}(?:女士|先生)\s+(?:在线\s+)?[^。]{0,120}$', '', text)
    text = re.sub(r'\s+[\u4e00-\u9fff]{2,4}\s+(?:刚刚活跃|今日活跃|在线)\s+.{0,100}?·\s*招聘者$', '', text)
    for skill in job.required_skills:
        if text.startswith(skill + ' '):
            text = text[len(skill):].lstrip()
    job.company = company.group(1)
    header = source.split('感兴趣', 1)[0]
    experience = re.search(r'经验不限|在校/应届|\d+\s*[-–—~至]\s*\d+\s*年|\d+\s*年以上', header)
    job.experience = experience.group(0) if experience else job.experience
    job.responsibilities = [text]
    values = [job.title, job.company, job.salary, job.experience, job.education, *job.responsibilities]
    job.cleaned_text = ' '.join(filter(None, values + job.required_skills))
    job.content_hash = hashlib.sha256('\n'.join(values + job.required_skills).encode()).hexdigest()
    job.blocks = [Evidence(source_type='job', source_id=job.snapshot_id,
        block_id=f'job-{i:02d}-{hashlib.sha256(value.encode()).hexdigest()[:10]}', quote=value, section='职位原文')
        for i, value in enumerate(values, 1) if value]
    return job


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('run_id')
    args = parser.parse_args()
    settings = Settings.load()
    database = Database(settings.database_path)
    backup = settings.database_path.with_name(f'career_radar.before-field-repair-{datetime.now():%Y%m%d%H%M%S}.db')
    with sqlite3.connect(backup) as target, database.connect() as source:
        source.backup(target)
    count = 0
    with database.connect() as connection:
        rows = connection.execute('SELECT id,payload FROM job_snapshots WHERE run_id=?', (args.run_id,)).fetchall()
        for row in rows:
            job = repair(JobSnapshot.model_validate_json(row['payload']))
            if job:
                connection.execute('UPDATE job_snapshots SET payload=?,content_hash=? WHERE id=?', (job.model_dump_json(), job.content_hash, row['id']))
                count += 1
    print(f'Repaired {count} snapshots; backup: {backup}')


if __name__ == '__main__':
    main()
