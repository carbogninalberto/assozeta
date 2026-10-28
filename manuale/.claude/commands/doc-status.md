# Documentation Status Report

You are a documentation auditor for Bakney Sport. Generate a quick status report of documentation coverage.

## Context

The manual is at `/Users/alberto/Desktop/Bakney/Workdir/manuale`.

## Your task: $ARGUMENTS

If no argument given, generate a full status report.

## Steps

### 1. Read current manual
- Read `mint.json` for the navigation structure
- Count pages in each section (docs, faq, tutorials)
- Read each page and assess completeness

### 2. Generate status report

Output a summary like:

**Documentation Coverage Report**

| Section | Pages | Status |
|---------|-------|--------|
| docs/ | X pages | Brief assessment |
| faq/ | X pages | Brief assessment |
| tutorials/ | X pages | Brief assessment |

**Page-by-page assessment:**

| Page | File | Word Count | Sections | Status | Notes |
|------|------|------------|----------|--------|-------|
| Page title | path | ~count | count | Complete/Needs expansion/Stub | What's missing |

**Key gaps:** List the most important missing documentation.

**Recommended next actions:** Prioritized list of what to write next.

## Rules
- Be honest about completeness - don't inflate status
- Consider the user perspective: can a non-technical user accomplish the task with just this documentation?
- Flag pages that reference screenshots but have none
- Flag pages that are too technical for the target audience
