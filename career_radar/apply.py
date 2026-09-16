"""Assisted application preparation.

CareerRadar prepares an application; it never submits one. It composes a greeting
the candidate can paste into the site's message box and opens the posting, but
the send stays with the person — that is both what the platforms' terms allow and
the only way to keep an account from being flagged.

The greeting is held to the same evidence rule as everything else: it may only
claim what the candidate's blocks support, and it must contain no contact
details, because the model never receives them.
"""

from __future__ import annotations

import re
from uuid import uuid4

from .agent import AgentService, GreetingOutput
from .database import Database, now_iso
from .resume import EMAIL_RE, PHONE_RE, SKILLS, ResumeError
from .schemas import Application, CandidateProfile, ChatCitation, Evidence, JobSnapshot
from .scoring import normalize_skill, score_job

MAX_GREETING = 300
TEMPLATE_PHRASES = ("温馨提示", "简历模板", "虚构示例", "请根据实际情况", "仅供参考")


def _evidence_index(profile: CandidateProfile) -> dict[str, Evidence]:
    return {item.block_id: item for item in profile.evidence}


def fill_citations(application: Application, profile: CandidateProfile) -> None:
    """Attach verbatim quotes from the cited blocks; the model never authors them."""
    index = _evidence_index(profile)
    application.greeting_citations = [
        ChatCitation(
            source_type=index[block_id].source_type, source_id=index[block_id].source_id,
            block_id=block_id, quote=index[block_id].quote, section=index[block_id].section,
        )
        for block_id in application.greeting_evidence_ids
        if block_id in index
    ]


def validate_greeting(application: Application, profile: CandidateProfile, snapshot: JobSnapshot) -> None:
    greeting = (application.greeting or "").strip()
    if not greeting:
        raise ResumeError("打招呼语为空")
    if len(greeting) > MAX_GREETING:
        raise ResumeError(f"打招呼语过长（{len(greeting)} 字，上限 {MAX_GREETING}）")
    if any(phrase in greeting for phrase in TEMPLATE_PHRASES):
        raise ResumeError("打招呼语包含模板提示语")

    # Contact details must never appear: the model is not given them, so anything
    # here was invented. The real contact is merged in locally at export time.
    if PHONE_RE.search(greeting):
        raise ResumeError("打招呼语出现了电话号码，模型不应接触联系方式")
    if EMAIL_RE.search(greeting):
        raise ResumeError("打招呼语出现了邮箱地址，模型不应接触联系方式")

    index = _evidence_index(profile)
    if not application.greeting_evidence_ids:
        raise ResumeError("打招呼语缺少证据")
    unknown = [block_id for block_id in application.greeting_evidence_ids if block_id not in index]
    if unknown:
        raise ResumeError("打招呼语引用了无效证据")
    quotes = " ".join(index[block_id].quote for block_id in application.greeting_evidence_ids)

    # Naming the role ("贵司的 Python 后端工程师岗位") is a fact about the posting,
    # not a claim about the candidate, so title words are not held to the quotes.
    title_terms = snapshot.title.lower()

    for number in re.findall(r"\d+(?:\.\d+)?%?", greeting):
        if number not in quotes:
            raise ResumeError(f"打招呼语出现未经证实的数字：{number}")
    for token, display in SKILLS.items():
        lowered = greeting.lower()
        if token not in lowered or token in title_terms:
            continue
        if token not in quotes.lower():
            raise ResumeError(f"打招呼语出现未经证实的技能：{display}")


def fallback_greeting(profile: CandidateProfile, snapshot: JobSnapshot) -> tuple[str, list[str]]:
    """Deterministic greeting, citing a block that actually supports its claim."""
    wanted = {normalize_skill(skill) for skill in snapshot.required_skills}
    for item in profile.evidence:
        lowered = item.quote.lower()
        for token, display in SKILLS.items():
            if token in lowered and normalize_skill(display) in wanted:
                return (
                    f"您好，我关注到贵司的「{snapshot.title}」岗位。"
                    f"我过往经历中与岗位要求相关的部分包括 {display}，"
                    f"具体内容已整理在简历中，期待有机会进一步沟通。",
                    [item.block_id],
                )
    # Nothing lines up with the posting, so say something true and unspecific
    # rather than asserting a match that the evidence does not support.
    blocks = [item.block_id for item in profile.evidence[:1]]
    greeting = (
        f"您好，我对贵司的「{snapshot.title}」岗位很感兴趣，"
        f"相关经历已整理在简历中，期待有机会进一步沟通。"
    )
    return (greeting, blocks)


class ApplyService:
    """Creates applications and prepares their greeting."""

    def __init__(self, database: Database, agent: AgentService):
        self.database = database
        self.agent = agent

    def create(self, profile_id: str, snapshot_id: str) -> Application:
        profile = self.database.get_profile(profile_id)
        snapshot = self.database.get_snapshot(snapshot_id)
        if not profile or not profile.confirmed:
            raise ResumeError("请先确认候选人画像")
        if not snapshot or not self.database.snapshot_belongs_to_profile(snapshot_id, profile_id):
            raise ResumeError("岗位快照不属于当前候选人")
        existing = self.database.get_application_for_snapshot(snapshot_id, profile_id)
        if existing:
            return existing
        stamp = now_iso()
        application = Application(
            application_id=f"app_{uuid4().hex[:12]}", profile_id=profile_id,
            snapshot_id=snapshot_id, company=snapshot.company, title=snapshot.title,
            url=snapshot.canonical_url, status="DRAFT",
            application_deadline=snapshot.application_deadline,
            created_at=stamp, updated_at=stamp,
        )
        return self.database.save_application(application)

    async def prepare(self, application_id: str) -> Application:
        application = self.database.get_application(application_id)
        if not application:
            raise ResumeError("投递记录不存在")
        profile = self.database.get_profile(application.profile_id)
        snapshot = self.database.get_snapshot(application.snapshot_id)
        if not profile or not snapshot:
            raise ResumeError("候选人画像或岗位快照不存在")

        application.status = "READY"
        greeting, evidence_ids, source = await self._compose(profile, snapshot)
        application.resume_export_id = self.database.latest_export_for_snapshot(
            application.snapshot_id, application.profile_id
        )

        def apply_greeting(text: str, ids: list[str], from_source: str) -> None:
            application.greeting = text
            application.greeting_evidence_ids = ids
            application.source = from_source
            fill_citations(application, profile)

        apply_greeting(greeting, evidence_ids, source)
        try:
            validate_greeting(application, profile, snapshot)
        except ResumeError as exc:
            # A greeting has a deterministic equivalent that is grounded by
            # construction, so fall back to it rather than leaving the user with
            # nothing. The reason is recorded and surfaced: substituting silently
            # is what would make the evidence rule meaningless.
            fallback_text, fallback_ids = fallback_greeting(profile, snapshot)
            apply_greeting(fallback_text, fallback_ids, "fallback")
            try:
                validate_greeting(application, profile, snapshot)
            except ResumeError:
                application.status = "DRAFT"
                application.error = str(exc)
                # Clear it. A recorded-but-unvalidated greeting would still read as
                # ready to the UI and the helper, which only check for non-empty
                # text — the exact leak this validation exists to prevent.
                application.greeting = ""
                application.greeting_evidence_ids = []
                application.greeting_citations = []
                self.database.save_application(application)
                raise exc
            application.note = f"模型生成的打招呼语未通过证据校验（{exc}），已改用本地确定性版本。"
        application.error = None
        return self.database.save_application(application)

    async def _compose(self, profile: CandidateProfile, snapshot: JobSnapshot) -> tuple[str, list[str], str]:
        fallback_text, fallback_ids = fallback_greeting(profile, snapshot)
        if not self.agent.settings.api_key:
            return fallback_text, fallback_ids, "fallback"
        try:
            output: GreetingOutput
            output, source = await self.agent.compose_greeting(profile, snapshot)
        except Exception:
            return fallback_text, fallback_ids, "fallback"
        text = (output.greeting or "").strip()
        ids = [str(value) for value in output.evidence_ids if str(value)]
        # A model answer that cannot be validated fails the task rather than being
        # silently replaced — substituting would hide an ungrounded claim.
        return (text or fallback_text), (ids or fallback_ids), source

    def set_status(self, application_id: str, status: str) -> Application:
        application = self.database.get_application(application_id)
        if not application:
            raise ResumeError("投递记录不存在")
        allowed = {
            "DRAFT", "READY", "OPENED", "SUBMITTED", "ASSESSMENT",
            "INTERVIEW", "OFFER", "REJECTED", "REPLIED", "SKIPPED",
        }
        if status not in allowed:
            raise ResumeError(f"无效的投递状态：{status}")
        if status == "SUBMITTED" and not application.greeting:
            raise ResumeError("还没有生成打招呼语")
        application.status = status
        if status == "SUBMITTED":
            application.submitted_at = now_iso()
        return self.database.save_application(application)


def missing_skills_for(profile: CandidateProfile, snapshot: JobSnapshot) -> list[str]:
    return score_job(profile, snapshot).missing_skills
