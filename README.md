# Skill Studio

Skill Studio 是一个 skill，用于把本地 skills 可视化、阅读、理解、检查、编辑和维护成一个可操作的 HTML 工作台。

它可以帮助你看清一个 skill 的用途、触发场景、Agent 可能的执行路径、依赖文件，以及浏览器里做出的修改如何先暂存为 JSON，再交给 Agent 审查 diff 后写回真实 skill 文件。

## 包含内容

- `SKILL.md` — skill 的主说明文件，定义触发描述、工作流和维护规则。
- `scripts/skill_studio.py` — 本地 Skill Studio 渲染器和编辑服务器。

## 使用方式

直接渲染某个 skill 为独立可编辑 HTML 页面：

```bash
python3 ~/.verdent/skills/skill-studio/scripts/skill_studio.py render <skill-name-or-path> --open
```

打开通用本地 Skill Studio 浏览器：

```bash
python3 ~/.verdent/skills/skill-studio/scripts/skill_studio.py serve --open
```

查看最新暂存修改包：

```bash
python3 ~/.verdent/skills/skill-studio/scripts/skill_studio.py latest
python3 ~/.verdent/skills/skill-studio/scripts/skill_studio.py inspect-stage <stage-json-path>
```

## 安全模型

浏览器 UI 不会直接写入真实 skill。它只会把修改暂存为 JSON。Agent 需要检查 staged diff，说明关键风险，然后再用正常的文件编辑工具写回真实 skill 文件。
