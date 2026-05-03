#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import hashlib
import html
import http.server
import json
import os
import pathlib
import re
import socketserver
import sys
import urllib.parse
import webbrowser

TEXT_SUFFIXES = {
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".py",
    ".sh",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".html",
    ".css",
    ".toml",
}
SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".venv", "dist", "build"}
MAX_TEXT_BYTES = 500_000


def expand(path: str | pathlib.Path) -> pathlib.Path:
    return pathlib.Path(path).expanduser().resolve()


def default_state_dir() -> pathlib.Path:
    cwd_state = pathlib.Path.cwd() / ".verdent" / "skill-studio"
    if pathlib.Path.cwd().exists():
        return cwd_state
    return pathlib.Path.home() / ".verdent" / "skill-studio"


def default_roots(extra: list[str] | None = None) -> list[pathlib.Path]:
    roots = [
        pathlib.Path.home() / ".verdent" / "skills",
        pathlib.Path.home() / ".verdent" / "workspace" / ".verdent" / "skills",
        pathlib.Path.cwd() / ".verdent" / "skills",
    ]
    if extra:
        roots.extend(pathlib.Path(item) for item in extra)
    seen: set[pathlib.Path] = set()
    result: list[pathlib.Path] = []
    for root in roots:
        resolved = expand(root)
        if resolved.exists() and resolved not in seen:
            result.append(resolved)
            seen.add(resolved)
    return result


def read_text(path: pathlib.Path) -> str:
    data = path.read_bytes()
    if len(data) > MAX_TEXT_BYTES:
        return data[:MAX_TEXT_BYTES].decode("utf-8", errors="replace") + "\n\n[TRUNCATED]"
    return data.decode("utf-8", errors="replace")


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_relative_to(path: pathlib.Path, parent: pathlib.Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not match:
        return {}
    data: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip().strip("'\"")
        data[key.strip()] = value
    return data


def list_skills(roots: list[pathlib.Path]) -> list[dict[str, str]]:
    skills: list[dict[str, str]] = []
    for root in roots:
        if not root.exists():
            continue
        for child in sorted(root.iterdir(), key=lambda item: item.name.lower()):
            if not child.is_dir() or not (child / "SKILL.md").exists():
                continue
            text = read_text(child / "SKILL.md")
            frontmatter = parse_frontmatter(text)
            skills.append(
                {
                    "name": frontmatter.get("name") or child.name,
                    "description": frontmatter.get("description", ""),
                    "path": str(child),
                    "root": str(root),
                }
            )
    return skills


def resolve_skill(raw: str, roots: list[pathlib.Path]) -> pathlib.Path:
    candidate = expand(raw)
    if candidate.exists() and (candidate / "SKILL.md").exists():
        return candidate
    for skill in list_skills(roots):
        if raw in {skill["name"], pathlib.Path(skill["path"]).name, skill["path"]}:
            return expand(skill["path"])
    raise ValueError(f"Skill not found: {raw}")


def file_tree(skill_path: pathlib.Path) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for path in sorted(skill_path.rglob("*")):
        rel = path.relative_to(skill_path)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if path.is_dir():
            continue
        suffix = path.suffix.lower()
        is_text = suffix in TEXT_SUFFIXES
        size = path.stat().st_size
        item: dict[str, object] = {
            "path": str(rel),
            "size": size,
            "text": is_text and size <= MAX_TEXT_BYTES,
            "suffix": suffix,
        }
        if item["text"]:
            item["content"] = read_text(path)
            item["sha256"] = sha256(str(item["content"]))
        items.append(item)
    return items


def extract_headings(text: str) -> list[dict[str, object]]:
    headings: list[dict[str, object]] = []
    for line in text.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            headings.append({"level": len(match.group(1)), "title": match.group(2)})
    return headings


def extract_trigger_lines(text: str, description: str) -> list[str]:
    triggers: list[str] = []
    if description:
        triggers.append(description)
    for line in text.splitlines():
        lowered = line.lower()
        if any(token in lowered for token in ["trigger", "keyword", "when user", "触发", "关键词", "use when"]):
            cleaned = line.strip(" -*")
            if cleaned and cleaned not in triggers:
                triggers.append(cleaned)
    return triggers[:12]


def extract_workflow(text: str) -> list[str]:
    steps: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"^(\d+\.|[-*])\s+", stripped):
            candidate = re.sub(r"^(\d+\.|[-*])\s+", "", stripped)
            if 12 <= len(candidate) <= 180 and any(
                word in candidate.lower()
                for word in ["run", "read", "create", "write", "ask", "search", "execute", "validate", "生成", "读取", "创建", "保存", "执行", "检查"]
            ):
                steps.append(candidate)
    if not steps:
        steps = [f"{'#' * item['level']} {item['title']}" for item in extract_headings(text)[:10]]
    return steps[:16]


def extract_risks(files: list[dict[str, object]]) -> list[dict[str, str]]:
    patterns = [
        ("Shell command", r"\b(bash|zsh|sh|python3|node|npm|git|curl|open)\b"),
        ("File write/delete", r"\b(write|overwrite|delete|remove|rm -rf|apply_patch|mv |cp )\b|删除|覆盖|写入|保存"),
        ("Network/API", r"\b(api|http|https|webhook|token|key|auth|login|curl)\b|授权|登录|密钥"),
        ("Destructive language", r"\b(force|reset --hard|drop table|destroy|irreversible)\b|不可逆|强制"),
        ("Large context", r"\b(read all|entire file|load everything|全部读取|完整读取)\b"),
    ]
    risks: list[dict[str, str]] = []
    for file_item in files:
        if not file_item.get("text"):
            continue
        content = str(file_item.get("content", ""))
        for label, pattern in patterns:
            for match in re.finditer(pattern, content, flags=re.I):
                line_no = content[: match.start()].count("\n") + 1
                line = content.splitlines()[line_no - 1].strip()
                risks.append({"type": label, "file": str(file_item["path"]), "line": str(line_no), "text": line[:220]})
                break
    return risks[:40]


def analyse_skill(skill_path: pathlib.Path) -> dict[str, object]:
    skill_md = read_text(skill_path / "SKILL.md")
    frontmatter = parse_frontmatter(skill_md)
    files = file_tree(skill_path)
    resources = {
        "scripts": sum(1 for item in files if str(item["path"]).startswith("scripts/")),
        "references": sum(1 for item in files if str(item["path"]).startswith("references/")),
        "assets": sum(1 for item in files if str(item["path"]).startswith("assets/")),
    }
    return {
        "name": frontmatter.get("name") or skill_path.name,
        "description": frontmatter.get("description", ""),
        "path": str(skill_path),
        "frontmatter": frontmatter,
        "headings": extract_headings(skill_md),
        "triggers": extract_trigger_lines(skill_md, frontmatter.get("description", "")),
        "workflow": extract_workflow(skill_md),
        "risks": extract_risks(files),
        "resources": resources,
        "files": files,
    }


def json_for_html(data: object) -> str:
    return html.escape(json.dumps(data, ensure_ascii=False), quote=False)


def inline_markdown(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", escaped)
    return escaped


def markdown_to_html(text: str) -> str:
    if text.startswith("---"):
        text = re.sub(r"^---\s*\n.*?\n---\s*\n", "", text, count=1, flags=re.S)
    lines = text.splitlines()
    blocks: list[str] = []
    paragraph: list[str] = []
    list_items: list[str] = []
    in_code = False
    code_lines: list[str] = []
    code_lang = ""

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            blocks.append(f"<p>{inline_markdown(' '.join(paragraph))}</p>")
            paragraph = []

    def flush_list() -> None:
        nonlocal list_items
        if list_items:
            blocks.append("<ul>" + "".join(f"<li>{inline_markdown(item)}</li>" for item in list_items) + "</ul>")
            list_items = []

    for line in lines:
        stripped = line.strip()
        fence = re.match(r"^```(.*)$", stripped)
        if fence:
            if in_code:
                blocks.append(
                    f'<pre class="code-block"><div class="code-lang">{html.escape(code_lang or "代码")}</div><code>{html.escape(chr(10).join(code_lines))}</code></pre>'
                )
                in_code = False
                code_lines = []
                code_lang = ""
            else:
                flush_paragraph()
                flush_list()
                in_code = True
                code_lang = fence.group(1).strip()
            continue
        if in_code:
            code_lines.append(line)
            continue
        if not stripped:
            flush_paragraph()
            flush_list()
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if heading:
            flush_paragraph()
            flush_list()
            level = min(len(heading.group(1)), 4)
            blocks.append(f"<h{level}>{inline_markdown(heading.group(2))}</h{level}>")
            continue
        if stripped.startswith(">"):
            flush_paragraph()
            flush_list()
            blocks.append(f"<blockquote>{inline_markdown(stripped.lstrip('> ').strip())}</blockquote>")
            continue
        list_match = re.match(r"^[-*]\s+(.+)$", stripped)
        if list_match:
            flush_paragraph()
            list_items.append(list_match.group(1))
            continue
        ordered_match = re.match(r"^\d+\.\s+(.+)$", stripped)
        if ordered_match:
            flush_paragraph()
            list_items.append(ordered_match.group(1))
            continue
        paragraph.append(stripped)
    flush_paragraph()
    flush_list()
    if in_code:
        blocks.append(f'<pre class="code-block"><code>{html.escape(chr(10).join(code_lines))}</code></pre>')
    return "\n".join(blocks)


def render_file_content(file_item: dict[str, object]) -> str:
    content = str(file_item.get("content", ""))
    path = str(file_item["path"])
    if path.lower().endswith(".md"):
        return f'<article class="md-body">{markdown_to_html(content)}</article>'
    return f'<pre>{html.escape(content)}</pre>'


def file_purpose(path: str) -> str:
    name = pathlib.PurePosixPath(path).name
    suffix = pathlib.PurePosixPath(path).suffix.lower()
    if path == "SKILL.md":
        return "Skill 的主说明文件，决定它什么时候被触发、Agent 应该怎么执行、有哪些规则和注意事项。"
    if path == "scripts/skill_studio.py":
        return "Skill Studio 的核心程序：扫描 skill、生成可视化 HTML、渲染 Markdown、导出修改 JSON、检查 staged diff。"
    if path.startswith("scripts/"):
        return f"可执行脚本，用来把重复或容易出错的操作固定下来。当前文件名是 {name}。"
    if path.startswith("references/"):
        return f"参考资料文件，给 Agent 在需要时读取，用来补充领域知识、规范、示例或长文档。当前文件名是 {name}。"
    if path.startswith("assets/"):
        return f"素材或模板文件，通常不直接塞进上下文，而是在产出内容时被复制、引用或改造。当前文件名是 {name}。"
    if suffix in {".json", ".yaml", ".yml", ".toml"}:
        return "配置或结构化数据文件，通常用来保存参数、元信息或机器可读内容。"
    if suffix in {".html", ".css", ".js", ".ts", ".tsx", ".jsx"}:
        return "前端相关文件，用来展示界面、样式或交互逻辑。"
    if suffix in {".py", ".sh"}:
        return "自动化脚本文件，用来执行确定性的本地操作。"
    return "Skill 附带的文本文件，具体作用需要结合内容阅读。"


def chinese_summary(description: str) -> str:
    lowered = description.lower()
    parts: list[str] = []
    if "visualize" in lowered or "可视化" in description:
        parts.append("把 Skill 转成结构化可视化页面")
    if "read" in lowered or "understand" in lowered or "阅读" in description or "理解" in description:
        parts.append("帮助维护者阅读、理解 Skill 的执行逻辑")
    if "edit" in lowered or "修改" in description or "编辑" in description:
        parts.append("支持在线编辑并导出修改")
    if "save" in lowered or "保存" in description:
        parts.append("由 Agent 审查 diff 后保存")
    if not parts:
        return description[:180]
    return "；".join(parts) + "。"


def trigger_keywords(description: str, trigger_lines: list[object]) -> list[str]:
    candidates = [
        "skill阅读器",
        "skill可视化",
        "Skill Studio",
        "Skill Reader",
        "阅读skill",
        "理解skill",
        "编辑skill",
        "修改skill",
        "维护skill",
        "保存skill",
        "查看SKILL.md",
        "skill workflow",
        "trigger analysis",
        "模拟 Agent 执行",
    ]
    source = description + "\n" + "\n".join(str(item) for item in trigger_lines)
    result: list[str] = []
    for keyword in candidates:
        if keyword.lower() in source.lower() and keyword not in result:
            result.append(keyword)
    return result or [str(item)[:40] for item in trigger_lines[:8]]


def chinese_workflow(analysis: dict[str, object]) -> list[str]:
    paths = {str(item["path"]) for item in analysis["files"]}
    steps = [
        "读取 SKILL.md，理解这个 skill 的用途、触发场景和执行边界。",
        "提取触发关键词，把长描述压缩成可读的中文触发标签。",
        "根据标题、列表和资源文件，梳理 Agent 可能采用的执行路径。",
        "检查 scripts、references、assets 等资源，说明每个文件的职责。",
        "识别命令执行、文件写入、网络请求、鉴权、破坏性操作等风险点。",
        "生成独立 HTML，把完整结构和所有可读文件的内容展示出来。",
        "把 Markdown 文件渲染成阅读版，同时在编辑器中保留可修改的原始文本。",
        "用户导出修改 JSON 后，由 Agent 审查 diff，再写回真实 skill 文件。",
    ]
    if "scripts/skill_studio.py" not in paths:
        steps[5] = "生成独立 HTML，把这个 skill 的结构、原文、资源和风险集中展示。"
    return steps


def render_direct_html(analysis: dict[str, object]) -> str:
    files = [item for item in analysis["files"] if item.get("text")]
    all_files = analysis["files"]
    description = str(analysis["description"])
    triggers = trigger_keywords(description, list(analysis["triggers"] or []))
    summary = chinese_summary(description)
    workflow = chinese_workflow(analysis)
    resources = analysis["resources"]
    trigger_html = "".join(f'<span class="pill">{html.escape(str(item))}</span>' for item in triggers)
    workflow_html = "".join(f'<div class="step"><div><b>步骤</b><span>{html.escape(str(item))}</span></div></div>' for item in workflow)
    files_html = "".join(
        f'<div class="file"><div><code>{html.escape(str(item["path"]))}</code><p>{html.escape(file_purpose(str(item["path"])))}</p><small>{int(item["size"])} 字节 · {html.escape(str(item["suffix"] or "文本"))}</small></div><span class="badge">可编辑</span></div>'
        for item in files
    )
    tree_html = "".join(
        f'<div class="tree-row"><code>{html.escape(str(item["path"]))}</code><span>{int(item["size"])} 字节 · {"文本" if item.get("text") else "二进制/大文件"}</span></div>'
        for item in all_files
    )
    source_html = "".join(
        f'<details open><summary>{html.escape(str(item["path"]))}</summary>{render_file_content(item)}</details>'
        for item in files
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(str(analysis["name"]))} · Skill 可视化阅读器</title>
<style>
:root{{--bg:#0f1115;--ink:#f7f0e6;--muted:#aeb4c0;--line:rgba(255,255,255,.12);--gold:#ffd166;--blue:#76e4f7;--green:#9be7a7;--red:#ff8b78;--shadow:0 18px 52px rgba(0,0,0,.32)}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:radial-gradient(circle at 8% 8%,rgba(118,228,247,.12),transparent 24rem),radial-gradient(circle at 88% 0,rgba(255,209,102,.14),transparent 26rem),linear-gradient(180deg,#0f1115,#11141a 55%,#0d0f13);color:var(--ink);font:14px/1.6 -apple-system,BlinkMacSystemFont,"SF Pro Text","PingFang SC","Microsoft YaHei",sans-serif}}
.shell{{display:grid;grid-template-columns:240px minmax(0,1fr);min-height:100vh}}aside{{position:sticky;top:0;height:100vh;padding:18px 14px;border-right:1px solid var(--line);background:rgba(15,17,21,.78);backdrop-filter:blur(18px)}}main{{padding:28px clamp(18px,4vw,48px) 56px;max-width:1320px}}a{{color:inherit;text-decoration:none}}nav{{display:grid;gap:5px;margin-top:18px}}nav a{{padding:8px 10px;border-radius:10px;color:var(--muted);border:1px solid transparent;font-size:13px}}nav a:hover{{color:var(--ink);border-color:var(--line);background:rgba(255,255,255,.05)}}.brand{{display:flex;align-items:center;gap:10px;padding:12px;border:1px solid var(--line);border-radius:18px;background:linear-gradient(135deg,rgba(255,255,255,.08),rgba(255,255,255,.03));box-shadow:var(--shadow)}}.mark{{width:38px;height:38px;flex:0 0 38px;display:grid;place-items:center;border-radius:12px;background:linear-gradient(135deg,var(--gold),var(--blue));color:#101216;font-weight:950}}h1{{margin:0;font-size:19px;line-height:1.12;letter-spacing:-.04em}}.brand p,.meta{{color:var(--muted);font-size:12px;word-break:break-all}}.meta{{position:absolute;left:14px;right:14px;bottom:18px}}.hero{{display:grid;grid-template-columns:minmax(0,1fr) 260px;gap:16px;margin-bottom:16px}}.card,.hero-card{{border:1px solid var(--line);border-radius:22px;background:linear-gradient(180deg,rgba(255,255,255,.07),rgba(255,255,255,.035));box-shadow:var(--shadow)}}.hero-card{{padding:30px;min-height:280px;display:flex;flex-direction:column;justify-content:space-between;overflow:hidden;position:relative}}.eyebrow{{width:max-content;border-radius:999px;padding:5px 9px;background:var(--gold);color:#101216;font-weight:900;font-size:11px;letter-spacing:.04em}}h2{{margin:14px 0 0;font-size:clamp(34px,5vw,58px);line-height:.92;letter-spacing:-.07em}}.summary{{max-width:860px;color:#e4e8ef;font-size:17px;line-height:1.55;letter-spacing:-.02em}}.metric{{padding:16px;border:1px solid var(--line);border-radius:18px;background:rgba(255,255,255,.05);margin-bottom:10px}}.metric b{{display:block;font-size:30px;letter-spacing:-.05em;line-height:1}}.metric span{{color:var(--muted);font-size:12px}}section{{margin-top:16px}}.card{{padding:22px}}.section-title{{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px}}h3{{margin:0;font-size:22px;line-height:1.08;letter-spacing:-.04em}}.tag{{display:inline-flex;padding:4px 8px;border:1px solid var(--line);border-radius:999px;color:var(--muted);font-size:11px;background:rgba(255,255,255,.04);white-space:nowrap}}.grid-2{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}.grid-3{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}}.mini,.file{{padding:14px;border:1px solid var(--line);border-radius:16px;background:rgba(255,255,255,.04)}}.mini h4{{margin:0 0 6px;font-size:15px;letter-spacing:-.02em}}p{{margin:0;color:var(--muted)}}small{{display:block;margin-top:7px;color:#818998;font-size:11px}}.pill-row{{display:flex;flex-wrap:wrap;gap:7px}}.pill{{display:inline-flex;padding:5px 8px;border-radius:999px;color:#101216;background:var(--blue);font-size:12px;font-weight:800}}.pill:nth-child(3n+2){{background:var(--gold)}}.pill:nth-child(3n+3){{background:var(--green)}}.flow{{display:grid;gap:9px;counter-reset:step}}.step{{counter-increment:step;display:grid;grid-template-columns:34px minmax(0,1fr);gap:10px;align-items:start;padding:12px;border:1px solid var(--line);border-radius:15px;background:rgba(255,255,255,.04)}}.step:before{{content:counter(step);width:30px;height:30px;display:grid;place-items:center;border-radius:10px;color:#101216;background:linear-gradient(135deg,var(--gold),var(--blue));font-weight:950}}.step b{{display:block;margin-bottom:3px;font-size:14px}}.step span{{color:var(--muted)}}code,pre,textarea,select,button{{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}}code{{color:var(--blue)}}.file{{display:grid;grid-template-columns:1fr auto;gap:10px;align-items:start}}.file p{{color:#cbd2dd}}.badge{{border-radius:999px;padding:4px 7px;color:#101216;background:var(--green);font-size:11px;font-weight:900}}pre{{margin:0;padding:14px;overflow:auto;border:1px solid var(--line);border-radius:14px;color:#dfe7f2;background:#0b0d12;font-size:12px;line-height:1.55;white-space:pre-wrap}}.editor{{display:grid;grid-template-columns:280px minmax(0,1fr);gap:12px}}select,textarea,button{{width:100%;border:1px solid var(--line);border-radius:12px}}select{{padding:9px;background:#11151d;color:var(--ink)}}textarea{{min-height:520px;padding:14px;background:#090b10;color:#eef5ff;font-size:12px;line-height:1.55;resize:vertical}}button{{padding:11px 12px;background:linear-gradient(135deg,var(--gold),var(--blue));color:#111;font-weight:950;cursor:pointer}}button.secondary{{background:transparent;color:var(--ink)}}.status{{margin-top:10px;padding:12px;border:1px solid rgba(155,231,167,.32);border-radius:14px;background:rgba(155,231,167,.08);white-space:pre-wrap;color:#dcffe4;display:none}}.changed{{color:var(--gold);font-weight:900}}.tree{{border:1px solid var(--line);border-radius:16px;overflow:hidden;background:rgba(255,255,255,.035)}}.tree-row{{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:14px;padding:9px 12px;border-bottom:1px solid var(--line)}}.tree-row:last-child{{border-bottom:0}}.tree-row span{{color:var(--muted);font-size:12px}}.heading-tree{{display:grid;gap:7px}}.heading-row{{display:grid;grid-template-columns:42px minmax(0,1fr);align-items:center;gap:10px;padding:8px 10px;border:1px solid var(--line);border-radius:12px;background:rgba(255,255,255,.035)}}.heading-row span{{color:#101216;background:var(--gold);border-radius:8px;padding:2px 6px;font-size:11px;font-weight:900;text-align:center}}.heading-row p{{color:#dfe5ee}}.depth-2{{margin-left:14px}}.depth-3{{margin-left:28px}}.depth-4,.depth-5,.depth-6{{margin-left:42px}}details{{border:1px solid var(--line);border-radius:16px;background:rgba(255,255,255,.035);margin-bottom:12px;overflow:hidden}}summary{{cursor:pointer;padding:11px 14px;color:var(--blue);font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;border-bottom:1px solid var(--line)}}details pre{{border:0;border-radius:0;max-height:620px}}.md-body{{padding:18px 22px;color:#e9edf3;background:rgba(255,255,255,.025)}}.md-body h1,.md-body h2,.md-body h3,.md-body h4{{margin:22px 0 8px;letter-spacing:-.035em;line-height:1.15}}.md-body h1{{font-size:30px}}.md-body h2{{font-size:24px}}.md-body h3{{font-size:19px}}.md-body h4{{font-size:16px}}.md-body p{{margin:8px 0;color:#d7dde7}}.md-body ul{{margin:8px 0 14px;padding-left:22px;color:#d7dde7}}.md-body li{{margin:5px 0}}.md-body blockquote{{margin:12px 0;padding:10px 13px;border-left:3px solid var(--gold);background:rgba(255,209,102,.08);border-radius:10px;color:#eef2f7}}.md-body .code-block{{margin:12px 0}}.code-lang{{margin:-14px -14px 10px;padding:7px 12px;border-bottom:1px solid var(--line);color:var(--muted);background:rgba(255,255,255,.04);font-size:11px}}@media(max-width:1050px){{.shell,.hero,.grid-2,.grid-3,.editor{{grid-template-columns:1fr}}aside{{position:static;height:auto}}.meta{{position:static;margin-top:16px}}}}
</style>
</head>
<body>
<div class="shell">
<aside>
<div class="brand"><div class="mark">阅</div><h1>{html.escape(str(analysis["name"]))}</h1></div>
<nav>
<a href="#purpose">01 / 它是什么</a>
<a href="#trigger">02 / 什么时候触发</a>
<a href="#flow">03 / Agent 怎么执行</a>
<a href="#files">04 / 可编辑文件</a>
<a href="#structure">05 / 完整结构</a>
<a href="#source">06 / 原文全文</a>
<a href="#edit">07 / 在线编辑</a>
</nav>
<div class="meta">Source:<br>{html.escape(str(analysis["path"]))}</div>
</aside>
<main>
<div class="hero">
<div class="hero-card"><div><div class="eyebrow">Skill 可视化</div><h2>{html.escape(str(analysis["name"]))}</h2><p class="summary">{html.escape(summary)}</p></div><pre>核心原则：HTML 页面可以编辑和导出修改，但真实 skill 文件仍由 Agent 审查 diff 后保存。</pre></div>
<div>
<div class="metric"><b>{len(files)}</b><span>可编辑文本文件</span></div>
<div class="metric"><b>{len(workflow)}</b><span>识别出的执行步骤</span></div>
<div class="metric"><b>{len(all_files)}</b><span>全部文件</span></div>
</div>
</div>
<section id="purpose" class="card"><div class="section-title"><h3>01. 它是什么</h3><span class="tag">用途</span></div><div class="grid-2"><div class="mini"><h4>人类可读</h4><p>把 Skill 的说明、触发词、工作流、资源和风险整理成一个页面。</p></div><div class="mini"><h4>Agent 可维护</h4><p>编辑后导出 staged JSON，交给 Agent 审查并保存。</p></div></div></section>
<section id="trigger" class="card"><div class="section-title"><h3>02. 什么时候触发</h3><span class="tag">触发面</span></div><div class="pill-row">{trigger_html}</div></section>
<section id="flow" class="card"><div class="section-title"><h3>03. Agent 运行它时会发生什么</h3><span class="tag">执行路径</span></div><div class="flow">{workflow_html}</div></section>
<section id="files" class="card"><div class="section-title"><h3>04. 可编辑文件</h3><span class="tag">资源</span></div><div class="grid-2">{files_html}</div><p style="margin-top:16px">资源统计：scripts={resources["scripts"]}，references={resources["references"]}，assets={resources["assets"]}</p></section>
<section id="structure" class="card"><div class="section-title"><h3>05. 完整 Skill 结构</h3><span class="tag">文件树</span></div><div class="tree">{tree_html}</div></section>
<section id="source" class="card"><div class="section-title"><h3>06. Skill 原文全文</h3><span class="tag">阅读版</span></div>{source_html}</section>
<section id="edit" class="card"><div class="section-title"><h3>07. 在线编辑并导出修改</h3><span class="tag">编辑</span></div><div class="editor"><div><select id="fileSelect"></select><p id="fileMeta" style="margin:12px 0"></p><button onclick="downloadStage()">导出修改给 Agent</button><button class="secondary" style="margin-top:10px" onclick="resetFile()">重置当前文件</button><div id="status" class="status"></div></div><textarea id="editor" spellcheck="false"></textarea></div></section>
</main>
</div>
<script type="application/json" id="skill-data">{json_for_html(analysis)}</script>
<script>
const data=JSON.parse(document.getElementById('skill-data').textContent);
const textFiles=data.files.filter(f=>f.text);
const originals=Object.fromEntries(textFiles.map(f=>[f.path,f.content]));
const modified={{...originals}};
let active=textFiles[0]?.path||'';
const $=id=>document.getElementById(id);
function loadOptions(){{$('fileSelect').innerHTML=textFiles.map(f=>`<option value="${{f.path}}">${{f.path}}</option>`).join('');$('fileSelect').onchange=()=>selectFile($('fileSelect').value);if(active)selectFile(active)}}
function selectFile(path){{active=path;$('fileSelect').value=path;$('editor').value=modified[path]||'';updateMeta();$('status').style.display='none'}}
function updateMeta(){{const changed=(modified[active]||'')!==(originals[active]||'');$('fileMeta').innerHTML=`${{active}} ${{changed?'<span class="changed">· modified</span>':''}}`}}
$('editor').addEventListener('input',()=>{{modified[active]=$('editor').value;updateMeta()}});
function resetFile(){{if(!active)return;modified[active]=originals[active];selectFile(active)}}
async function downloadStage(){{
  const changed=Object.entries(modified).filter(([path,content])=>content!==originals[path]);
  if(!changed.length){{alert('没有修改可以导出');return}}
  const bundle={{version:1,created_at:new Date().toISOString(),skill_name:data.name,skill_path:data.path,files:changed.map(([path,content])=>({{path,original_content:originals[path],modified_content:content}}))}};
  const blob=new Blob([JSON.stringify(bundle,null,2)],{{type:'application/json'}});
  const url=URL.createObjectURL(blob);
  const a=document.createElement('a');
  const stamp=new Date().toISOString().replace(/[:.]/g,'-');
  a.href=url;a.download=`skill-studio-stage-${{data.name}}-${{stamp}}.json`;a.click();
  URL.revokeObjectURL(url);
  const msg='已导出 staged JSON。\\n\\n请回到 Agent，说：\\n我已修改好我的 skill，帮我保存 skill。\\n\\n如果 Agent 找不到文件，请告诉它刚下载的 JSON 文件路径，通常在 Downloads 目录。';
  $('status').textContent=msg;$('status').style.display='block';
  try{{await navigator.clipboard.writeText('我已修改好我的 skill，帮我保存 skill。')}}catch(e){{}}
}}
loadOptions();
</script>
</body>
</html>"""


def render_skill_page(skill_path: pathlib.Path, output: pathlib.Path) -> pathlib.Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_direct_html(analyse_skill(skill_path)), encoding="utf-8")
    return output


def safe_relpath(rel: str) -> pathlib.Path:
    path = pathlib.PurePosixPath(rel)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsafe relative path: {rel}")
    return pathlib.Path(*path.parts)


def stage_changes(state_dir: pathlib.Path, payload: dict[str, object], roots: list[pathlib.Path]) -> pathlib.Path:
    skill_path = resolve_skill(str(payload.get("skill_path") or payload.get("path") or ""), roots)
    changed_files = payload.get("files")
    if not isinstance(changed_files, dict):
        raise ValueError("Expected files object")
    staged: list[dict[str, str]] = []
    for rel, modified in changed_files.items():
        if not isinstance(modified, str):
            continue
        rel_path = safe_relpath(str(rel))
        abs_path = (skill_path / rel_path).resolve()
        if not is_relative_to(abs_path, skill_path):
            raise ValueError(f"Unsafe path outside skill: {rel}")
        original = read_text(abs_path) if abs_path.exists() else ""
        if original == modified:
            continue
        staged.append(
            {
                "path": str(rel_path).replace(os.sep, "/"),
                "original_sha256": sha256(original),
                "modified_sha256": sha256(modified),
                "original_content": original,
                "modified_content": modified,
            }
        )
    if not staged:
        raise ValueError("No changed files to stage")
    stage_dir = state_dir / "staged"
    stage_dir.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    name = re.sub(r"[^a-zA-Z0-9_.-]+", "-", skill_path.name).strip("-")
    out = stage_dir / f"{timestamp}-{name}.json"
    bundle = {
        "version": 1,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "skill_name": skill_path.name,
        "skill_path": str(skill_path),
        "files": staged,
    }
    out.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def newest_stage(state_dir: pathlib.Path) -> pathlib.Path:
    stage_dir = state_dir / "staged"
    files = sorted(stage_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True) if stage_dir.exists() else []
    if not files:
        raise FileNotFoundError(f"No staged bundles found in {stage_dir}")
    return files[0]


def inspect_stage(path: pathlib.Path) -> str:
    bundle = json.loads(read_text(path))
    lines = [
        f"Stage: {path}",
        f"Skill: {bundle.get('skill_name')} ({bundle.get('skill_path')})",
        f"Created: {bundle.get('created_at')}",
        f"Files: {len(bundle.get('files', []))}",
        "",
    ]
    for file_item in bundle.get("files", []):
        rel = file_item["path"]
        original = file_item.get("original_content", "").splitlines(keepends=True)
        modified = file_item.get("modified_content", "").splitlines(keepends=True)
        lines.append(f"--- {rel}")
        lines.extend(
            difflib.unified_diff(
                original,
                modified,
                fromfile=f"a/{rel}",
                tofile=f"b/{rel}",
                n=3,
            )
        )
        lines.append("")
    return "".join(line if line.endswith("\n") else line + "\n" for line in lines)


def app_html() -> str:
    return r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Skill Studio</title>
<style>
:root{--bg:#f6f3ed;--paper:#fffdf8;--ink:#17130d;--muted:#746b5f;--line:#d8d0c2;--accent:#111;--warn:#9b341f;--ok:#28724f}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
header{height:56px;display:flex;align-items:center;justify-content:space-between;padding:0 18px;border-bottom:1px solid var(--line);background:var(--paper);position:sticky;top:0;z-index:5}
h1{font-size:18px;margin:0;letter-spacing:-.03em}.sub{color:var(--muted);font-size:12px}
.layout{display:grid;grid-template-columns:280px minmax(420px,1fr) 46%;min-height:calc(100vh - 56px)}
aside,.main,.editor{padding:16px;border-right:1px solid var(--line);overflow:auto}.editor{border-right:0;background:#fbfaf6}
input,select,textarea,button{font:inherit}input,select{width:100%;padding:9px;border:1px solid var(--line);background:#fff;border-radius:8px}
button{border:1px solid var(--ink);background:var(--ink);color:white;padding:9px 12px;border-radius:8px;cursor:pointer}button.secondary{background:transparent;color:var(--ink);border-color:var(--line)}
.skill{padding:10px;border:1px solid var(--line);background:var(--paper);border-radius:10px;margin:8px 0;cursor:pointer}.skill.active{border-color:var(--ink);box-shadow:0 0 0 2px #17130d10}.skill b{display:block}.skill p{margin:4px 0 0;color:var(--muted);font-size:12px}
.card{background:var(--paper);border:1px solid var(--line);border-radius:14px;padding:14px;margin:0 0 14px}.card h2{font-size:14px;margin:0 0 10px;text-transform:uppercase;letter-spacing:.08em;color:#3b352d}
.desc{font-size:16px;line-height:1.5}.pill{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:4px 8px;margin:3px;background:#fff;color:#3b352d;font-size:12px}
.flow{display:flex;flex-direction:column;gap:8px}.step{border:1px solid var(--line);background:#fff;border-radius:10px;padding:10px;position:relative}.step:before{content:"";position:absolute;left:22px;top:-9px;width:1px;height:8px;background:var(--line)}.step:first-child:before{display:none}
.risk{border-left:3px solid var(--warn);padding:8px 10px;background:#fff;margin:6px 0;border-radius:6px}.risk small{color:var(--muted)}
.file-list{max-height:220px;overflow:auto;border:1px solid var(--line);background:#fff;border-radius:10px}.file-row{padding:8px 10px;border-bottom:1px solid #eee;cursor:pointer}.file-row:hover{background:#f6f3ed}.file-row.active{background:#ece5d7}.file-row small{float:right;color:var(--muted)}
textarea{width:100%;height:calc(100vh - 260px);min-height:420px;border:1px solid var(--line);border-radius:12px;padding:12px;background:#fffdfb;color:#17130d;font:13px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;tab-size:2}
.toolbar{display:flex;gap:8px;margin:10px 0;align-items:center}.toolbar>*{flex:1}.savebox{white-space:pre-wrap;background:#102016;color:#d8ffe6;border-radius:12px;padding:12px;margin-top:10px;display:none}
pre{white-space:pre-wrap;overflow:auto;background:#fff;border:1px solid var(--line);border-radius:10px;padding:10px}.muted{color:var(--muted)}.changed{color:var(--warn);font-weight:700}
</style>
</head>
<body>
<header><div><h1>Skill Studio</h1><div class="sub">Read · understand · edit · stage skill changes for Agent review</div></div><button class="secondary" onclick="reloadSkills()">Refresh</button></header>
<div class="layout">
<aside>
  <input id="search" placeholder="Search skills…" oninput="renderSkillList()">
  <div id="skills"></div>
</aside>
<main class="main">
  <div id="overview" class="muted">Choose a skill to inspect.</div>
</main>
<section class="editor">
  <div class="card">
    <h2>Editor</h2>
    <div class="toolbar"><select id="fileSelect" onchange="selectFile(this.value)"></select><button onclick="stageChanges()">Stage changes for Agent</button></div>
    <div class="sub" id="fileMeta">No file selected.</div>
    <textarea id="editor" spellcheck="false" oninput="markChanged()"></textarea>
    <div id="savebox" class="savebox"></div>
  </div>
</section>
</div>
<script>
let skills=[], current=null, activePath="", originals={}, modified={};
const $=id=>document.getElementById(id);
function escapeHtml(s){return String(s||"").replace(/[&<>"']/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[m]))}
async function reloadSkills(){skills=await (await fetch("/api/skills")).json();renderSkillList()}
function renderSkillList(){const q=$("search").value.toLowerCase();$("skills").innerHTML=skills.filter(s=>(s.name+s.description+s.path).toLowerCase().includes(q)).map(s=>`<div class="skill ${current&&current.path===s.path?"active":""}" onclick="loadSkill('${encodeURIComponent(s.path)}')"><b>${escapeHtml(s.name)}</b><p>${escapeHtml(s.description).slice(0,150)}</p></div>`).join("")}
async function loadSkill(path){current=await (await fetch("/api/skill?path="+path)).json();originals={};modified={};for(const f of current.files){if(f.text){originals[f.path]=f.content;modified[f.path]=f.content}}renderSkillList();renderOverview();renderFiles();const first=current.files.find(f=>f.path==="SKILL.md")||current.files.find(f=>f.text);if(first)selectFile(first.path)}
function renderOverview(){const risks=current.risks.length?current.risks.map(r=>`<div class="risk"><b>${escapeHtml(r.type)}</b> <small>${escapeHtml(r.file)}:${r.line}</small><br>${escapeHtml(r.text)}</div>`).join(""):"<p class='muted'>No obvious risk patterns detected.</p>";$("overview").innerHTML=`<div class="card"><h2>Purpose</h2><div class="desc">${escapeHtml(current.description||"No description.")}</div><p class="muted">${escapeHtml(current.path)}</p></div><div class="card"><h2>Trigger Surface</h2>${current.triggers.map(t=>`<span class="pill">${escapeHtml(t).slice(0,180)}</span>`).join("")||"<p class='muted'>No trigger lines found.</p>"}</div><div class="card"><h2>Execution Map</h2><div class="flow">${current.workflow.map((s,i)=>`<div class="step"><b>${i+1}.</b> ${escapeHtml(s)}</div>`).join("")}</div></div><div class="card"><h2>Resources</h2><span class="pill">scripts: ${current.resources.scripts}</span><span class="pill">references: ${current.resources.references}</span><span class="pill">assets: ${current.resources.assets}</span></div><div class="card"><h2>Risk Highlights</h2>${risks}</div><div class="card"><h2>Headings</h2><pre>${escapeHtml(current.headings.map(h=>"  ".repeat(h.level-1)+h.title).join("\n"))}</pre></div>`}
function renderFiles(){const textFiles=current.files.filter(f=>f.text);$("fileSelect").innerHTML=textFiles.map(f=>`<option value="${escapeHtml(f.path)}">${escapeHtml(f.path)}</option>`).join("")}
function selectFile(path){activePath=path;$("fileSelect").value=path;$("editor").value=modified[path]||"";const changed=(modified[path]||"")!==(originals[path]||"");$("fileMeta").innerHTML=`${escapeHtml(path)} ${changed?"<span class='changed'>· modified</span>":""}`;$("savebox").style.display="none"}
function markChanged(){if(!activePath)return;modified[activePath]=$("editor").value;selectFile(activePath)}
async function stageChanges(){if(!current)return;const changed={};for(const [path,content] of Object.entries(modified)){if(content!==originals[path])changed[path]=content}if(!Object.keys(changed).length){alert("No changes to stage.");return}const res=await fetch("/api/stage",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({skill_path:current.path,files:changed})});const data=await res.json();if(!res.ok){alert(data.error||"Failed to stage changes");return}const msg=`Changes staged.\n\nStage file:\n${data.stage_path}\n\nReturn to Agent and say:\n我已修改好我的 skill，帮我保存 skill。\n\nAgent can inspect with:\npython3 ~/.verdent/skills/skill-studio/scripts/skill_studio.py inspect-stage "${data.stage_path}"`;$("savebox").textContent=msg;$("savebox").style.display="block";navigator.clipboard&&navigator.clipboard.writeText("我已修改好我的 skill，帮我保存 skill。")}
reloadSkills();
</script>
</body>
</html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    roots: list[pathlib.Path] = []
    state_dir: pathlib.Path = default_state_dir()

    def send_json(self, data: object, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if parsed.path == "/":
                body = app_html().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif parsed.path == "/api/skills":
                self.send_json(list_skills(self.roots))
            elif parsed.path == "/api/skill":
                raw = query.get("path", [""])[0]
                self.send_json(analyse_skill(resolve_skill(raw, self.roots)))
            elif parsed.path == "/api/latest":
                self.send_json({"stage_path": str(newest_stage(self.state_dir))})
            else:
                self.send_json({"error": "Not found"}, 404)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 400)

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if self.path != "/api/stage":
                self.send_json({"error": "Not found"}, 404)
                return
            stage_path = stage_changes(self.state_dir, payload, self.roots)
            self.send_json({"stage_path": str(stage_path)})
        except Exception as exc:
            self.send_json({"error": str(exc)}, 400)

    def log_message(self, format: str, *args: object) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))


def serve(args: argparse.Namespace) -> None:
    roots = default_roots(args.skill_root)
    if not roots:
        raise SystemExit("No skill roots found. Pass --skill-root <path>.")
    state_dir = expand(args.state_dir) if args.state_dir else default_state_dir().resolve()
    Handler.roots = roots
    Handler.state_dir = state_dir
    with socketserver.TCPServer(("127.0.0.1", args.port), Handler) as httpd:
        url = f"http://127.0.0.1:{httpd.server_address[1]}/"
        print(f"Skill Studio: {url}")
        print("Skill roots:")
        for root in roots:
            print(f"  - {root}")
        print(f"Staged changes: {state_dir / 'staged'}")
        if args.open:
            webbrowser.open(url)
        httpd.serve_forever()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local HTML Skill Studio for reading and staging Verdent skill edits.")
    sub = parser.add_subparsers(dest="command", required=True)
    serve_parser = sub.add_parser("serve", help="start the local Skill Studio web app")
    serve_parser.add_argument("--port", type=int, default=8765)
    serve_parser.add_argument("--open", action="store_true")
    serve_parser.add_argument("--skill-root", action="append", default=[])
    serve_parser.add_argument("--state-dir")
    latest_parser = sub.add_parser("latest", help="print newest staged bundle")
    latest_parser.add_argument("--state-dir")
    inspect_parser = sub.add_parser("inspect-stage", help="print staged bundle summary and unified diff")
    inspect_parser.add_argument("path")
    render_parser = sub.add_parser("render", help="render one skill directly as a polished editable HTML file")
    render_parser.add_argument("skill", help="skill name or path")
    render_parser.add_argument("--output", "-o")
    render_parser.add_argument("--open", action="store_true")
    render_parser.add_argument("--skill-root", action="append", default=[])
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "serve":
        serve(args)
    elif args.command == "latest":
        state_dir = expand(args.state_dir) if args.state_dir else default_state_dir().resolve()
        print(newest_stage(state_dir))
    elif args.command == "inspect-stage":
        print(inspect_stage(expand(args.path)))
    elif args.command == "render":
        roots = default_roots(args.skill_root)
        skill_path = resolve_skill(args.skill, roots)
        output = expand(args.output) if args.output else pathlib.Path.cwd() / f"{skill_path.name}-visualized.html"
        rendered = render_skill_page(skill_path, output)
        print(rendered)
        if args.open:
            webbrowser.open(f"file://{rendered}")


if __name__ == "__main__":
    main()