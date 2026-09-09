---
name: career-analysis
description: Analyze CareerRadar candidate profiles and job snapshots with validated evidence citations and deterministic backend scores.
license: MIT
compatibility: Requires the career-radar MCP server and a CareerRadar SQLite database.
allowed-tools: read_candidate_profile search_resume_evidence list_job_snapshots search_job_evidence save_role_recommendations save_job_comparison
---

# CareerRadar analysis

Use only these product tools. Read the confirmed candidate profile before analyzing jobs. Search the source evidence whenever a claim needs support.

For role recommendations, return exactly three directions. Each direction needs a role name, one to four search keywords, confidence, rationale, strengths, gaps, and at least one exact resume citation.

For job comparison, treat the backend score as final. Explain it without altering any component. Separate facts into resume-proven ability, user-stated ability without project evidence, and a job requirement that is not yet demonstrated. Missing information means “无法判断”. Call out explicit experience, education, or city conflicts as risks.

Every ability judgment must include this citation shape:

```json
{"source_type":"resume|job","source_id":"id","block_id":"stable-id","quote":"exact source text"}
```

Save the completed structure with the matching save tool. Never invent a block ID or paraphrase the quote.
