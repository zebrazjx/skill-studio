---
name: skill-studio
description: "This skill should be used when users want to visualize, read, understand, inspect, edit, maintain, or save changes to Verdent skills. It creates a local HTML Skill Studio for skill阅读器, skill可视化, Skill Studio, Skill Reader, 阅读skill, 理解skill, 编辑skill, 修改skill, 维护skill, 保存skill, 查看SKILL.md, skill workflow, trigger analysis, or simulating how an Agent will execute a skill."
---

# Skill Studio

## Overview

Create a local browser-based Skill Studio that turns Verdent skills into readable, inspectable, editable software assets. Use it to help users understand what a skill does, how an Agent is likely to execute it, which files it depends on, and how proposed edits should be staged for Agent-reviewed saving.

Do not write user edits directly from the browser into the real skill. Stage changes as JSON, then use Agent tools to review the diff and apply the final file edits.

## Core Workflows

### Render One Skill Directly

When the user asks to visualize a specific skill, do not make them choose from a generic picker. Generate a polished standalone HTML page for that exact skill:

```bash
python3 ~/.verdent/skills/skill-studio/scripts/skill_studio.py render <skill-name-or-path> --open
```

The generated HTML must show the selected skill directly, including purpose, trigger surface, execution map, editable files, full skill file tree, full original source for every text file, risk highlights, headings, and an editing area.

The standalone HTML is editable. Its **导出修改给 Agent** button downloads a staged JSON bundle containing changed file contents. Tell the user to return to Agent with:

```text
我已修改好我的 skill，帮我保存 skill。
```

If the Agent cannot locate the downloaded staged JSON, ask the user for the downloaded file path, usually under `~/Downloads`.

Render pages as Chinese-first reading experiences:

- Use Chinese labels and navigation by default.
- Keep components compact; avoid oversized hero cards, huge metrics, and English UI chrome.
- Keep the sidebar identity card minimal: only the avatar/mark and skill name, arranged on one horizontal line.
- Do not render long English descriptions or full trigger sentences as giant pills; extract short trigger keywords and show long descriptions as readable summaries.
- Render execution maps as Chinese explanatory steps, not raw extracted English lines from `SKILL.md`.
- Explain what each file does in plain Chinese next to the file path.
- Omit risk highlights from the default visual page unless the user explicitly asks for security/risk review.
- Do not include a separate reading-outline/document-structure section in the default page; the rendered Markdown source is enough.
- Render Markdown files as formatted reading content, not raw Markdown text.
- Still include full raw content in the editor so the user can modify exact source.
- Show the full skill structure and the complete rendered source for every readable text file.

### Open the Generic Skill Studio

Run the bundled server from the skill directory:

```bash
python3 ~/.verdent/skills/skill-studio/scripts/skill_studio.py serve
```

If a project-local skill directory is required, pass it explicitly:

```bash
python3 ~/.verdent/skills/skill-studio/scripts/skill_studio.py serve --skill-root ./.verdent/skills
```

Use the generic Studio only when the user wants to browse multiple skills. The web UI lists discovered skills, renders `SKILL.md`, builds an execution map from headings and workflow language, highlights risks, shows resource files, and provides an editor for text files.

### Help the User Read a Skill

Use the Studio to explain:

1. Purpose: what the skill is for.
2. Trigger surface: which user phrases should invoke it.
3. Execution map: what the Agent will probably read, decide, and run.
4. Resources: scripts, references, assets, and generated artifacts.
5. Risk areas: shell commands, file writes, external services, authentication, destructive language, or large context loads.
6. Maintenance opportunities: unclear triggers, missing examples, duplicated instructions, brittle commands, or oversized sections.

### Stage Browser Edits

In the HTML UI, let the user edit `SKILL.md` or other text files and click **Stage changes for Agent**. The browser posts changes to the local server, which writes a staged JSON bundle under:

```text
.verdent/skill-studio/staged/
```

The UI then displays this instruction for the user:

```text
我已修改好我的 skill，帮我保存 skill。
```

### Save Staged Changes

When the user returns with “我已修改好我的 skill，帮我保存 skill” or similar:

1. Locate the newest staged bundle:

```bash
python3 ~/.verdent/skills/skill-studio/scripts/skill_studio.py latest
```

2. Inspect the staged diff:

```bash
python3 ~/.verdent/skills/skill-studio/scripts/skill_studio.py inspect-stage <stage-json-path>
```

3. Read the staged JSON if exact content is needed.
4. Explain the changed files and important diff risks to the user.
5. Apply the changes to the real skill using normal Agent file-edit tools.
6. Validate the skill with the skill-creator validator or packaging script when available.

Never blindly write staged content into the skill. Treat the staged JSON as a proposed patch from the human editor.

## Editing Standards

Preserve skill quality while applying edits:

- Keep `SKILL.md` concise and procedural.
- Keep frontmatter valid YAML with `name` and `description`.
- Keep the description trigger-rich but under 1024 characters.
- Prefer reusable scripts for deterministic repeated work.
- Move long examples, schemas, or methodology into `references/`.
- Avoid adding README, CHANGELOG, or setup docs unless they are core to execution.
- Preserve existing resource paths unless deliberately refactoring.
- Surface dangerous commands or irreversible actions before saving.

## Recommended User Prompts

Use this skill for prompts like:

- “帮我可视化这个 skill”
- “做一个 skill 阅读器”
- “打开 Skill Studio”
- “我想理解这个 skill 运行时 Agent 会做什么”
- “帮我编辑/修改/维护这个 skill”
- “我已修改好我的 skill，帮我保存 skill”
- “把我的 skill 变成 HTML 页面让我阅读”
- “把整个 skill 的结构和原文全部展示给我”

## Scripts

Use `scripts/skill_studio.py`:

- `serve`: start the local HTML Skill Studio.
- `render <skill>`: generate one specific skill as a polished editable standalone HTML file.
- `latest`: print the newest staged change bundle.
- `inspect-stage <path>`: summarize staged files and print unified diffs.

The server is local-only by default and binds to `127.0.0.1`.
