from __future__ import annotations

import re
from typing import Any


def _labelled_date(text: str, labels: tuple[str, ...]) -> str | None:
    label = "|".join(map(re.escape, labels))
    match = re.search(rf"(?:{label})[：:\s]*(20\d{{2}})[年/.-](\d{{1,2}})[月/.-](\d{{1,2}})日?", text)
    if not match:
        return None
    return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def extract_graduate_metadata(text: str, experience: str = "") -> dict[str, Any]:
    """Extract only explicitly stated graduate-job facts; never guess."""
    value = " ".join((text or "", experience or ""))
    if "实习" in value:
        recruitment_type = "internship"
    elif any(token in value for token in ("校招", "校园招聘", "应届生", "应届毕业生")):
        recruitment_type = "campus"
    elif any(token in value for token in ("社会招聘", "社招")):
        recruitment_type = "experienced"
    else:
        recruitment_type = "unknown"
    years = sorted({int(year) for year in re.findall(r"(?<!\d)(20\d{2})\s*届", value)})
    batch = next((token for token in ("提前批", "秋招", "春招", "补录") if token in value), None)
    experience_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:年|年以上)", experience)
    experience_years = float(experience_match.group(1)) if experience_match else None
    if any(token in experience for token in ("经验不限", "无需经验", "应届")):
        experience_years = 0.0
    return {
        "recruitment_type": recruitment_type,
        "graduation_years": years,
        "experience_requirement_years": experience_years,
        "recruitment_batch": batch,
        "published_date": _labelled_date(value, ("发布日期", "发布时间")),
        "application_deadline": _labelled_date(value, ("截止日期", "网申截止", "申请截止")),
        "conversion_opportunity": True if any(token in value for token in ("可转正", "转正机会", "实习转正")) else None,
    }
