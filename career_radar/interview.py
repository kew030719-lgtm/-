"""Per-posting interview preparation.

The global 7-day plan lives on Comparison.action_plan and covers the top three
postings. This module is scoped to one posting: a day-by-day plan plus interview
questions, every one tied to an evidence block.

Evidence discipline matches the rest of the product, but the division of labour
is different here: the model writes prose and **the backend picks the evidence**.
The configured endpoint cannot fill structured fields reliably (see
agent.InterviewPrepOutput), so asking it for block ids produced empty lists. It
does write clean `类别|问题` lines, so the backend parses those, matches each
question against the blocks that could support it, and drops any question
nothing supports. The invariant is unchanged — ungrounded content never reaches
the user — but it no longer depends on the model choosing a citation.
"""

from __future__ import annotations

import re
from uuid import uuid4

from .agent import AgentService, InterviewPrepOutput
from .database import Database, now_iso
from .resume import SKILLS, ResumeError
from .schemas import (
    CandidateProfile, ChatCitation, Evidence, InterviewDay, InterviewPrep, InterviewQuestion, JobSnapshot,
)
from .scoring import normalize_skill, score_job

TEMPLATE_PHRASES = ("温馨提示", "简历模板", "虚构示例", "请根据实际情况", "仅供参考")

CATEGORIES = ("技术深挖", "项目经历", "能力缺口", "行为面", "反问")


def _evidence_index(profile: CandidateProfile, snapshot: JobSnapshot) -> dict[str, Evidence]:
    """Block id -> evidence, across both the candidate and the posting.

    A block id is a content hash with a section prefix, so candidate and job ids
    do not collide in practice.
    """
    index: dict[str, Evidence] = {}
    for item in [*profile.evidence, *snapshot.blocks]:
        index.setdefault(item.block_id, item)
    return index


def _reject_template_text(value: str) -> None:
    if any(phrase in value for phrase in TEMPLATE_PHRASES):
        raise ResumeError("面试准备包含模板提示语")


def _citations(evidence_ids: list[str], index: dict[str, Evidence]) -> list[ChatCitation]:
    return [
        ChatCitation(
            source_type=index[block_id].source_type, source_id=index[block_id].source_id,
            block_id=block_id, quote=index[block_id].quote, section=index[block_id].section,
        )
        for block_id in evidence_ids
        if block_id in index
    ]


def fill_citations(prep: InterviewPrep, profile: CandidateProfile, snapshot: JobSnapshot) -> None:
    """Attach verbatim quotes from the cited blocks. The model never authors them."""
    index = _evidence_index(profile, snapshot)
    for day in prep.days:
        day.citations = _citations(day.evidence_ids, index)
    for question in prep.questions:
        question.citations = _citations(question.evidence_ids, index)


def validate_interview(prep: InterviewPrep, profile: CandidateProfile, snapshot: JobSnapshot) -> None:
    """Every claim must be grounded; ungrounded questions fail the task."""
    index = _evidence_index(profile, snapshot)
    missing = {normalize_skill(skill) for skill in prep.missing_skills}

    for day in prep.days:
        _reject_template_text(f"{day.focus}{day.deliverable}")
        unknown = [block_id for block_id in day.evidence_ids if block_id not in index]
        if unknown:
            raise ResumeError(f"面试计划引用了无效证据：第 {day.day} 天")

    for question in prep.questions:
        if not question.evidence_ids:
            raise ResumeError(f"面试题缺少证据：{question.question_id}")
        unknown = [block_id for block_id in question.evidence_ids if block_id not in index]
        if unknown:
            raise ResumeError(f"面试题引用了无效证据：{question.question_id}")
        quotes = " ".join(index[block_id].quote for block_id in question.evidence_ids)
        lowered = question.question.lower()
        _reject_template_text(question.question)
        for number in re.findall(r"\d+(?:\.\d+)?%?", question.question):
            if number not in quotes:
                raise ResumeError(f"面试题出现未经证实的数字：{number}")
        for token, display in SKILLS.items():
            if token not in lowered:
                continue
            # A gap question names a skill the candidate lacks. What grounds it is
            # the posting's requirement, not the candidate's evidence, so the
            # candidate-side rule deliberately does not apply.
            if normalize_skill(display) in missing:
                continue
            if token not in quotes.lower():
                raise ResumeError(f"面试题出现未经证实的技能：{display}")


def _pick(items: list[Evidence], needle: str) -> Evidence | None:
    lowered = needle.lower()
    return next((item for item in items if lowered in item.quote.lower()), None)


def _display_skill(skill: str, snapshot: JobSnapshot) -> str:
    """Recover the posting's own spelling of a skill from its normalised form.

    score_job reports missing skills normalised ("kubernetes"), which would read
    badly inside a question.
    """
    for item in snapshot.required_skills:
        if normalize_skill(item) == skill:
            return item
    return skill


def fallback_interview(profile: CandidateProfile, snapshot: JobSnapshot,
                       missing_skills: list[str]) -> tuple[list[InterviewDay], list[InterviewQuestion]]:
    """Deterministic preparation used when no model is configured or the call fails.

    It only ever cites blocks that exist, so it passes validate_interview by
    construction rather than by exemption.
    """
    job_blocks = list(snapshot.blocks)
    candidate_blocks = [item for item in profile.evidence if item.section in {"项目", "经历", "技能", "用户补充"}]
    candidate_blocks = candidate_blocks or list(profile.evidence)
    job_anchor = job_blocks[0] if job_blocks else None
    candidate_anchor = candidate_blocks[0] if candidate_blocks else None

    def anchor(preferred: Evidence | None) -> list[str]:
        return [preferred.block_id] if preferred else []

    days: list[InterviewDay] = []
    plan = [
        ("通读岗位证据，列出必备技能与硬性门槛", "岗位要求清单与自评表", [job_anchor] if job_anchor else []),
        ("补齐最高频的技能缺口，完成一个最小可运行示例", "可运行的最小示例", []),
        ("把示例扩展为带测试与错误处理的小项目", "带测试的小项目", []),
        ("按岗位职责改写一段项目经历，保留可核验指标", "改写后的项目经历", [candidate_anchor] if candidate_anchor else []),
        ("准备 8 个项目追问，用 STAR 结构口述回答", "口头回答的要点卡片", [candidate_anchor] if candidate_anchor else []),
        ("完成一次限时模拟面试，记录薄弱问题", "薄弱问题清单", []),
        ("修订简历与作品说明，投递排名最高且无硬性冲突的岗位", "定稿简历", []),
    ]
    for number, (focus, deliverable, blocks) in enumerate(plan, start=1):
        days.append(InterviewDay(
            day=number, focus=focus, deliverable=deliverable,
            evidence_ids=[item.block_id for item in blocks if item],
        ))

    questions: list[InterviewQuestion] = []
    for skill in missing_skills[:3]:
        # Only ask about a gap we can actually point at in the posting.
        display = _display_skill(skill, snapshot)
        block = _pick(job_blocks, display)
        if block is None:
            continue
        questions.append(InterviewQuestion(
            question_id=f"q_{uuid4().hex[:10]}",
            question=f"岗位要求 {display}。你有相关经验吗？如果没有，你打算如何补齐？",
            category="能力缺口",
            why_asked="该要求出现在岗位原文中，但简历证据里没有对应内容。",
            answer_hint="如实说明现状，给出具体的学习或迁移路径；不要虚构经历。",
            evidence_ids=[block.block_id],
        ))
    for skill in profile.skills[:4]:
        block = _pick(candidate_blocks, skill)
        if block is None:
            continue
        questions.append(InterviewQuestion(
            question_id=f"q_{uuid4().hex[:10]}",
            question=f"请结合真实项目说明你如何使用 {skill}，遇到什么问题，结果如何。",
            category="技术深挖",
            why_asked=f"简历中有 {skill} 的证据，面试官通常会顺着简历追问。",
            answer_hint="用 STAR 结构回答，引用简历中可核验的细节。",
            evidence_ids=[block.block_id],
        ))
    if candidate_anchor:
        questions.append(InterviewQuestion(
            question_id=f"q_{uuid4().hex[:10]}",
            question="你最近一段经历中，最有代表性的产出是什么？",
            category="项目经历",
            why_asked="用于确认简历中项目经历的真实性与深度。",
            answer_hint="选一项能给出数字或结果的项目，说明你的具体职责。",
            evidence_ids=[candidate_anchor.block_id],
        ))
    if job_anchor:
        questions.append(InterviewQuestion(
            question_id=f"q_{uuid4().hex[:10]}",
            question="关于这个岗位，你想了解团队或业务上的哪些信息？",
            category="反问",
            why_asked="反问环节考察你对岗位的理解程度。",
            answer_hint="围绕岗位原文中的职责提出问题，避免只问薪资福利。",
            evidence_ids=[job_anchor.block_id],
        ))
    return days, questions


class InterviewService:
    """Creates and generates per-posting interview preparation."""

    def __init__(self, database: Database, agent: AgentService):
        self.database = database
        self.agent = agent

    def create(self, profile_id: str, snapshot_id: str) -> InterviewPrep:
        profile = self.database.get_profile(profile_id)
        snapshot = self.database.get_snapshot(snapshot_id)
        if not profile or not profile.confirmed:
            raise ResumeError("请先确认候选人画像")
        if not snapshot or not self.database.snapshot_belongs_to_profile(snapshot_id, profile_id):
            raise ResumeError("岗位快照不属于当前候选人")
        stamp = now_iso()
        prep = InterviewPrep(
            prep_id=f"prep_{uuid4().hex[:12]}", profile_id=profile_id, snapshot_id=snapshot_id,
            company=snapshot.company, title=snapshot.title, status="QUEUED",
            created_at=stamp, updated_at=stamp,
        )
        self.database.save_interview_prep(prep)
        return prep

    async def generate(self, prep_id: str) -> InterviewPrep:
        prep = self.database.get_interview_prep(prep_id)
        if not prep:
            raise ResumeError("面试准备不存在")
        profile = self.database.get_profile(prep.profile_id)
        snapshot = self.database.get_snapshot(prep.snapshot_id)
        if not profile or not snapshot:
            raise ResumeError("候选人画像或岗位快照不存在")
        prep.status = "RUNNING"
        self.database.save_interview_prep(prep)

        score = score_job(profile, snapshot)
        prep.missing_skills = score.missing_skills
        try:
            days, questions, source, note = await self._build(profile, snapshot, score.missing_skills)
            prep.days, prep.questions, prep.source, prep.note = days, questions, source, note
            # Quotes come from the cited blocks; the model never authors them.
            fill_citations(prep, profile, snapshot)
            try:
                validate_interview(prep, profile, snapshot)
            except ResumeError as exc:
                # The deterministic plan is grounded by construction, so an
                # unusable model answer degrades to it — visibly, never silently,
                # which is what keeps the evidence rule meaningful.
                fallback_days, fallback_questions = fallback_interview(
                    profile, snapshot, score.missing_skills
                )
                prep.days, prep.questions, prep.source = fallback_days, fallback_questions, "fallback"
                prep.note = f"模型的面试准备未通过证据校验（{exc}），已改用本地确定性版本。"
                fill_citations(prep, profile, snapshot)
                validate_interview(prep, profile, snapshot)
        except ResumeError as exc:
            # Persist the rejection: on reload a failed preparation must not look
            # like a successful one.
            prep.status = "FAILED_VALIDATION"
            prep.error = str(exc)
            self.database.save_interview_prep(prep)
            raise
        prep.status = "SUCCEEDED"
        prep.error = None
        return self.database.save_interview_prep(prep)

    async def _build(self, profile: CandidateProfile, snapshot: JobSnapshot,
                     missing_skills: list[str],
                     ) -> tuple[list[InterviewDay], list[InterviewQuestion], str, str]:
        """Model output when it is usable, the deterministic plan otherwise.

        The fallback covers "no model configured" and "the call failed" — it is
        NOT a safety net for unusable model output. Questions the model did return
        go through validate_interview unchanged, so a forged one fails the task
        instead of being quietly swapped for a deterministic question.
        """
        fallback_days, fallback_questions = fallback_interview(profile, snapshot, missing_skills)
        if not self.agent.settings.api_key:
            return fallback_days, fallback_questions, "fallback", ""
        try:
            output, source = await self.agent.prepare_interview(profile, snapshot, missing_skills)
        except Exception as exc:
            return fallback_days, fallback_questions, "fallback", f"模型调用失败（{type(exc).__name__}），已改用本地确定性版本。"
        days, questions = _normalise(output, profile, snapshot)
        if not questions:
            # Distinct from an ungrounded answer. An empty reply contains nothing
            # to conceal, so the deterministic plan is used and the reason is
            # recorded; only content that *exists but cannot be evidenced* fails
            # the task, because substituting for that would hide it.
            note = "模型没有返回可用的面试问题，已改用本地确定性版本。"
            return fallback_days, fallback_questions, "fallback", note
        return (days or fallback_days), questions, source, ""


def _why_asked(category: str) -> str:
    return {
        "技术深挖": "面试官通常会顺着简历里的技术细节追问。",
        "项目经历": "用于确认项目经历的真实性与深度。",
        "能力缺口": "该要求出现在岗位原文中，但简历证据里没有对应内容。",
        "行为面": "考察协作、推进与处理分歧的方式。",
        "反问": "反问环节考察你对岗位的理解程度。",
    }.get(category, "面试官常见追问方向。")


def _answer_hint(category: str) -> str:
    return {
        "技术深挖": "用 STAR 结构回答，引用简历中可核验的细节。",
        "项目经历": "选一项能给出数字或结果的项目，说明你的具体职责。",
        "能力缺口": "如实说明现状，给出具体的学习或迁移路径；不要虚构经历。",
        "行为面": "给出具体情境与你的实际做法，避免泛泛而谈。",
        "反问": "围绕岗位原文中的职责提问，避免只问薪资福利。",
    }.get(category, "结合真实经历作答，不要虚构。")


def _parse_rows(text: str, columns: int) -> list[list[str]]:
    """Parse `a|b` lines, ignoring anything that is not one.

    The model returns prose because it cannot fill structured fields reliably;
    this is where the structure comes from. An optional bullet or enumerator is
    tolerated — the prompt asks for neither, but a model that adds one has still
    answered usefully.
    """
    rows: list[list[str]] = []
    for raw in (text or "").splitlines():
        line = re.sub(r"^\s*(?:[-•·*]|\d+\s*[.、)）])\s*", "", raw.strip()).strip()
        if not line or "|" not in line:
            continue
        parts = [part.strip() for part in line.split("|")]
        parts += [""] * (columns - len(parts))
        rows.append(parts[:columns])
    return rows


def _ground_question(question: str, category: str, profile: CandidateProfile,
                     snapshot: JobSnapshot) -> str | None:
    """The block that supports this question, or None when nothing does.

    The model is not asked for block ids — it proved unable to supply them — so
    the backend picks. A question nothing supports is dropped, which keeps the
    "never show ungrounded content" rule intact without depending on the model.
    """
    lowered = question.lower()
    tokens = {token for token in SKILLS if token in lowered}
    # A question can carry specifics beyond skills — "结合你 2 年 Python 经验" — and
    # validate_interview checks every number against the cited quote. Matching on
    # skills alone picked a block that supported "python" but not "2", so the
    # question was rejected after being grounded. The chosen block has to support
    # the whole question, or the question is not groundable and is dropped.
    numbers = re.findall(r"\d+(?:\.\d+)?%?", question)

    def supports(item: Evidence) -> bool:
        quote = item.quote
        if tokens and not any(token in quote.lower() for token in tokens):
            return False
        return all(number in quote for number in numbers)

    candidate = list(profile.evidence)
    posting = list(snapshot.blocks)
    # A gap question names a requirement the posting states, so look there first.
    pools = (posting, candidate) if category == "能力缺口" else (candidate, posting)
    if tokens or numbers:
        for pool in pools:
            for item in pool:
                if supports(item):
                    return item.block_id
        return None
    # Nothing specific named: cite the posting, which is why it is being asked.
    for pool in (posting, candidate):
        if pool:
            return pool[0].block_id
    return None


def _normalise(output: InterviewPrepOutput, profile: CandidateProfile,
               snapshot: JobSnapshot) -> tuple[list[InterviewDay], list[InterviewQuestion]]:
    """Turn the model's prose into grounded structure, dropping what cannot be."""
    questions: list[InterviewQuestion] = []
    for category, question in _parse_rows(output.questions, 2):
        if not question:
            continue
        if category not in CATEGORIES:
            category = "技术深挖"
        block_id = _ground_question(question, category, profile, snapshot)
        if block_id is None:
            continue
        questions.append(InterviewQuestion(
            question_id=f"q_{uuid4().hex[:10]}", question=question[:300], category=category,
            why_asked=_why_asked(category),
            answer_hint=_answer_hint(category),
            evidence_ids=[block_id],
        ))

    days: list[InterviewDay] = []
    for index, (focus, deliverable) in enumerate(_parse_rows(output.days, 2)[:7], start=1):
        if not focus:
            continue
        days.append(InterviewDay(
            day=index, focus=focus[:200], deliverable=deliverable[:200], evidence_ids=[],
        ))
    return days, questions[:12]