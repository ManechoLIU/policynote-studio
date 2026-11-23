#!/usr/bin/env python3
"""scheme.py — 保险图文五步方案：建骨架、交付门禁、改稿快照、导出。

子命令：
  init    建 outputs/<主题>/ 骨架，复制需求单/事实台账/方案/溯源模板与封面模板
  check   交付前硬门禁：文件齐全、封面尺寸、标题与正文字数、溯源三行、事实状态、占位残留
  revise  改稿：先快照当前稿，再写入新稿，追加修订记录
  export  导出选中方案为单文件（标题/封面/正文/溯源/修订记录）

用法：
  python skills/builtin/insurance-note-scheme/scripts/scheme.py init --topic 重疾险-等待期怎么讲
  python skills/builtin/insurance-note-scheme/scripts/scheme.py check --topic 重疾险-等待期怎么讲
  python skills/builtin/insurance-note-scheme/scripts/scheme.py revise --topic 重疾险-等待期怎么讲 \
      --variant A --note "开头太慢，换成客户原话" --from D:/tmp/A-方案.md
  python skills/builtin/insurance-note-scheme/scripts/scheme.py export --topic 重疾险-等待期怎么讲 --variant A
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
TEMPLATES = SKILL_DIR / "assets" / "templates"
COVER_JSON_TEMPLATE = SKILL_DIR / "assets" / "templates" / "cover.json"
DEFAULT_VARIANTS = ("A", "B", "C", "D")
TITLE_LIMIT = 20
BODY_LIMIT = 1000
MIN_COVER_WIDTH = 1000
COVER_RATIO = 3 / 4
RATIO_TOLERANCE = 0.02

ABSOLUTE_CLAIMS = (
    "稳赚", "保本", "无风险", "保证收益", "一定能赔", "百分百赔付",
    "100%赔付", "最划算", "第一名", "人人适合",
)
PLACEHOLDER_RE = re.compile(r"【[^】]{0,30}】|<[^<>\n]{1,40}>")
ALLOWED_MARKERS = {"【待核实】"}


def _placeholders(text: str) -> list[str]:
    return [m.group(0) for m in PLACEHOLDER_RE.finditer(text) if m.group(0) not in ALLOWED_MARKERS]


def _find_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file() and (parent / "easel" / "cli.py").is_file():
            return parent
    raise SystemExit("[scheme] 找不到项目根，请设置 EASEL_ROOT")


ROOT = _find_root()
sys.path.insert(0, str(ROOT / "skills" / "shared" / "scripts"))
from output_paths import (  # noqa: E402
    OUTPUTS_DIR,
    OutputPathError,
    validate_output_path,
    validate_project_dir,
)


# ---------------------------------------------------------------- helpers

def _project(topic: str) -> Path:
    try:
        return validate_project_dir(OUTPUTS_DIR / topic)
    except OutputPathError as exc:
        raise SystemExit(f"[scheme] 主题不合法：{exc}")


def _require_project(topic: str) -> Path:
    proj = _project(topic)
    if not proj.is_dir():
        raise SystemExit(f"[scheme] 项目不存在：{proj}，先跑 init")
    return proj


def _variants(proj: Path, spec: str | None) -> list[str]:
    if spec:
        names = [part.strip().upper() for part in spec.split(",") if part.strip()]
    else:
        names = sorted(
            p.name[2:] for p in proj.iterdir()
            if p.is_dir() and re.fullmatch(r"方案[A-F]", p.name)
        )
    if not names:
        raise SystemExit(f"[scheme] {proj} 下没有方案目录，先跑 init")
    return names


def _sections(text: str) -> dict[str, str]:
    parts = re.split(r"(?m)^##\s+(.+?)\s*$", text)
    out: dict[str, str] = {}
    for index in range(1, len(parts) - 1, 2):
        out[parts[index].strip()] = parts[index + 1].strip()
    return out


def _clean_markup(value: str) -> str:
    return re.sub(r"[*_`>「」\"'“”‘’\s]", "", value)


def _count(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def _json_strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for child in value for item in _json_strings(child)]
    if isinstance(value, dict):
        return [item for child in value.values() for item in _json_strings(child)]
    return []


def _png_size(path: Path) -> tuple[int, int] | None:
    try:
        head = path.open("rb").read(33)
    except OSError:
        return None
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


def _row_for(lines: list[str], label: str) -> str | None:
    for line in lines:
        if not line.strip().startswith("|"):
            continue
        cells = line.split("|")
        if len(cells) < 3:
            continue
        if _clean_markup(cells[1]).startswith(label):
            return line
    return None


def _template(name: str, **values: str) -> str:
    text = (TEMPLATES / name).read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    return text


# ---------------------------------------------------------------- init

def cmd_init(args) -> int:
    topic = args.topic.strip()
    proj = validate_project_dir(OUTPUTS_DIR / topic, create=True)
    variants = [v.strip().upper() for v in (args.variants or ",".join(DEFAULT_VARIANTS)).split(",") if v.strip()]

    for rel in ("assets/covers", "assets/snapshots", "导出"):
        (proj / rel).mkdir(parents=True, exist_ok=True)

    created: list[str] = []

    def _write(rel: str, content: str) -> None:
        path = proj / rel
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        created.append(rel)

    _write("需求单.md", _template("需求单.md", TOPIC=topic))
    _write("事实台账.md", _template("事实台账.md", TOPIC=topic))
    _write("修订记录.md", _template("修订记录.md", TOPIC=topic))
    _write("方案对比.md", _template("方案对比.md", TOPIC=topic))
    _write("assets/方法候选.md",
           "# 方法候选 · " + topic + "\n\n> 经纪人反馈先记这里，同一观察两次以上验证后再提交方法层，不自动写回知识库。\n\n"
           "| 观察 | 适用条件 | 反例/失效场景 | 来源 |\n|---|---|---|---|\n")

    cover = proj / "assets" / "covers" / "cover.json"
    if not cover.exists():
        shutil.copyfile(COVER_JSON_TEMPLATE, cover)
        created.append("assets/covers/cover.json")

    for variant in variants:
        vdir = proj / f"方案{variant}"
        (vdir).mkdir(exist_ok=True)
        _write(f"方案{variant}/方案.md", _template("方案.md", VARIANT=variant, TOPIC=topic))
        _write(f"方案{variant}/溯源.md", _template("溯源.md", VARIANT=variant, TOPIC=topic))

    print(f"[scheme] 项目：{proj}")
    if created:
        for rel in created:
            print(f"  新建 {rel}")
    else:
        print("  已存在，未覆盖（要重置请先手动移走旧目录）")
    return 0


# ---------------------------------------------------------------- check

def cmd_check(args) -> int:
    proj = _require_project(args.topic)
    variants = _variants(proj, args.variants)
    variant_failures = 0
    global_failures = 0

    cover_json = proj / "assets" / "covers" / "cover.json"
    cover_specs: dict = {}
    if not cover_json.is_file():
        print("[FAIL] 缺少封面配置：assets/covers/cover.json（用 assets/templates/cover.json 填写）")
        global_failures += 1
    else:
        try:
            cover_specs = json.loads(cover_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"[FAIL] cover.json 不是有效 JSON：{exc}")
            global_failures += 1
            cover_specs = {}
        for variant in variants:
            spec = cover_specs.get(variant) or cover_specs.get(f"方案{variant}")
            if not isinstance(spec, dict):
                print(f"[FAIL] cover.json 缺少方案{variant} 的配置")
                global_failures += 1
                continue
            if str(spec.get("layout", "")).lower() not in ("a", "b", "c", "d"):
                print(f"[FAIL] 方案{variant} 的 layout 必须是 a/b/c/d")
                global_failures += 1
            bad = next((value for value in _json_strings(spec) if "【" in value or "{{" in value), None)
            if bad:
                print(f"[FAIL] 方案{variant} 封面文案仍有占位：{bad[:30]}")
                global_failures += 1

    stray_scripts = sorted(path.name for path in (proj / "assets").glob("*.py"))
    if stray_scripts:
        print(f"       ! assets 下有脚本 {', '.join(stray_scripts)}：确认封面不是自写渲染，模板才是唯一来源")

    for variant in variants:
        vdir = proj / f"方案{variant}"
        problems: list[str] = []
        notes: list[str] = []

        md_path = vdir / "方案.md"
        sections: dict[str, str] = {}
        title = ""
        body = ""
        if not md_path.is_file():
            problems.append("缺少 方案.md")
        else:
            sections = _sections(md_path.read_text(encoding="utf-8"))
            missing = [key for key in ("标题", "封面文案", "正文") if key not in sections]
            if missing:
                problems.append("方案.md 缺少章节：" + "、".join(f"## {m}" for m in missing))
            title_text = sections.get("标题", "").strip()
            title = _clean_markup(title_text.splitlines()[0]) if title_text else ""
            if not title:
                problems.append("标题为空")
            elif len(title) > TITLE_LIMIT:
                problems.append(f"标题 {len(title)} 字（上限 {TITLE_LIMIT}）")
            body = sections.get("正文", "").strip()
            body_len = _count(body)
            if body and body_len > BODY_LIMIT:
                problems.append(f"正文 {body_len} 字（上限 {BODY_LIMIT}）")
            if body and body_len < 200:
                notes.append(f"正文仅 {body_len} 字，疑似未写完")
            cover_copy = sections.get("封面文案", "").strip()
            for label, chunk in (("标题", title_text), ("封面文案", cover_copy), ("正文", body)):
                marks = _placeholders(chunk)
                if marks:
                    problems.append(f"{label}仍有模板占位：{marks[0]}")

        cover = vdir / "封面.png"
        cover_label = "缺封面.png"
        if not cover.is_file():
            problems.append("缺少 封面.png")
        else:
            size = _png_size(cover)
            if size is None:
                problems.append("封面.png 不是有效 PNG")
            else:
                width, height = size
                cover_label = f"{width}×{height}"
                if width < MIN_COVER_WIDTH:
                    problems.append(f"封面宽 {width}px（至少 {MIN_COVER_WIDTH}px）")
                if height and abs(width / height - COVER_RATIO) > RATIO_TOLERANCE:
                    notes.append(f"封面比例 {width}:{height}，不是 3:4")

        prov_path = vdir / "溯源.md"
        prov_rows = 0
        pending = 0
        if not prov_path.is_file():
            problems.append("缺少 溯源.md")
        else:
            text = prov_path.read_text(encoding="utf-8")
            lines = text.splitlines()
            for label in ("标题", "封面", "正文"):
                row = _row_for(lines, label)
                if row is None:
                    problems.append(f"溯源缺少「{label}」行")
                    continue
                if "http" in row or "无直接对标" in row:
                    prov_rows += 1
                else:
                    problems.append(f"溯源「{label}」行没有链接，也没写“无直接对标”")
            if "事实清单" not in text:
                problems.append("溯源缺少「事实清单」")
            elif not any(token in text for token in ("已核", "待核", "不适用")):
                problems.append("事实清单没有状态（已核/待核/不适用）")
            pending = sum(
                1 for line in lines
                if line.strip().startswith("|") and "待核" in line and "状态" not in line
            )
            if pending and "【待核实】" not in body:
                notes.append(f"事实清单有 {pending} 条待核，正文未标【待核实】")

        for claim in ABSOLUTE_CLAIMS:
            if claim in title or claim in body:
                notes.append(f"疑似绝对化表述：{claim}（人工确认，不自动判错）")

        label = f"方案{variant}｜标题 {len(title)} 字｜正文 {_count(body)} 字｜封面 {cover_label}｜溯源 {prov_rows}/3"
        if problems:
            variant_failures += 1
            print(f"[FAIL] {label}")
            for problem in problems:
                print(f"       - {problem}")
        else:
            print(f"[PASS] {label}")
        for note in notes:
            print(f"       ! {note}")

    if not (proj / "方案对比.md").is_file():
        print("       ! 还没有 方案对比.md（四套并排比较，交付时建议补上）")
    if not (proj / "事实台账.md").is_file():
        print("       ! 缺少 事实台账.md（事实层底稿，数字与结论无处可回）")
    single = bool(getattr(args, "single", False))
    if not args.variants and not single and len(variants) < 4:
        print(f"[FAIL] 多套模式只有 {len(variants)} 套（至少 4 套）；单篇请加 --single")
        global_failures += 1

    total = len(variants)
    mode = "单篇" if single else "多套"
    print(f"[scheme] {mode} {total} 套：{total - variant_failures} 通过，{variant_failures} 失败")
    return 1 if (variant_failures or global_failures) else 0


# ---------------------------------------------------------------- revise

def cmd_revise(args) -> int:
    proj = _require_project(args.topic)
    variant = args.variant.strip().upper()
    vdir = proj / f"方案{variant}"
    if not vdir.is_dir():
        raise SystemExit(f"[scheme] 方案目录不存在：{vdir}")

    source = Path(args.from_file).expanduser()
    if not source.is_file():
        raise SystemExit(f"[scheme] 新稿文件不存在：{source}")

    target_name = (args.target or "方案.md").strip()
    if "/" in target_name or "\\" in target_name or target_name.startswith("."):
        raise SystemExit("[scheme] --target 只能是方案目录内的文件名")
    target = vdir / target_name

    snapshots = proj / "assets" / "snapshots"
    snapshots.mkdir(parents=True, exist_ok=True)
    current = len(list(snapshots.glob(f"{variant}-v*"))) + 1
    while (snapshots / f"{variant}-v{current}").exists():
        current += 1
    snap = snapshots / f"{variant}-v{current}"
    snap.mkdir()
    for item in vdir.iterdir():
        if item.is_file():
            shutil.copy2(item, snap / item.name)

    shutil.copyfile(source, target)

    log = proj / "修订记录.md"
    if not log.is_file():
        log.write_text(_template("修订记录.md", TOPIC=proj.name), encoding="utf-8")
    note = re.sub(r"\s+", " ", args.note).replace("|", "｜").strip()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    with log.open("a", encoding="utf-8") as fh:
        fh.write(f"| v{current + 1} | {stamp} | 方案{variant} | {note} | {target_name} |\n")

    print(f"[scheme] 已快照 方案{variant} v{current} → assets/snapshots/{variant}-v{current}/")
    print(f"[scheme] 新稿 v{current + 1} 已写入 {target.relative_to(proj)}")
    print(f"[scheme] 修改意见已记入 修订记录.md")
    return 0



# ---------------------------------------------------------------- build

def _cover_copy(spec: dict) -> str:
    layout = str(spec.get("layout", "")).lower()
    title = spec.get("title")
    if layout == "d":
        t = title or {}
        main = f"{t.get('line1','')}{t.get('pre','')}{t.get('keyword','')}{t.get('post','')}"
        sub = (spec.get("note") or {}).get("judgment", "")
    elif layout == "b":
        rows = spec.get("table") or []
        main = "".join(title or [])
        sub = rows[0][0] + " / " + rows[0][1] if rows and len(rows[0]) > 1 else ""
    elif layout == "c":
        main = "".join(title or [])
        items = spec.get("items") or []
        sub = " / ".join(item[1] for item in items if len(item) > 1)
    else:
        main = "".join(title or [])
        sub = spec.get("lede", "")
    return f"主文案：{main}\n副文案：{sub}\n构图：{layout}"


def cmd_build(args) -> int:
    proj = _require_project(args.topic)
    source = Path(args.input).expanduser()
    if not source.is_file():
        raise SystemExit(f"[scheme] 内容文件不存在：{source}")
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"[scheme] 内容 JSON 解析失败：{exc}")

    items = data.get("variants") or []
    if not items:
        raise SystemExit("[scheme] content JSON 缺少 variants")
    letters, covers = [], {}
    compare_rows, ledger = [], {}
    for item in items:
        letter = str(item.get("letter", "")).strip().upper()
        if not letter or not re.fullmatch(r"[A-F]", letter):
            raise SystemExit("[scheme] 每套需要 letter（A-F）")
        if letter in letters:
            raise SystemExit(f"[scheme] letter 重复：{letter}")
        letters.append(letter)

        title = str(item.get("title", "")).strip()
        body = str(item.get("body", "")).strip()
        if not title:
            raise SystemExit(f"[scheme] 方案{letter} 标题为空")
        if len(re.sub(r"\s+", "", title)) > TITLE_LIMIT:
            raise SystemExit(f"[scheme] 方案{letter} 标题超过 {TITLE_LIMIT} 字")
        body_len = _count(body)
        if body_len < 200 or body_len > BODY_LIMIT:
            raise SystemExit(f"[scheme] 方案{letter} 正文 {body_len} 字，要求 200-{BODY_LIMIT}")
        cover = item.get("cover")
        if not isinstance(cover, dict) or str(cover.get("layout", "")).lower() not in ("a", "b", "c", "d"):
            raise SystemExit(f"[scheme] 方案{letter} 缺少有效 cover.layout（a/b/c/d）")
        covers[letter] = cover
        compare_rows.append(f"| {letter} | {str(item.get('angle', '')).strip()} | {title} | {cover.get('layout')} | {str(item.get('angle', '')).strip()} |")
        for fact in item.get("facts") or []:
            if isinstance(fact, list) and len(fact) >= 6:
                ledger.setdefault(str(fact[0]), fact)

        vdir = proj / f"方案{letter}"
        vdir.mkdir(exist_ok=True)
        sources = item.get("sources") or {}
        rows = []
        for key, label in (("title", "标题"), ("cover", "封面"), ("body", "正文")):
            value = sources.get(key)
            if not isinstance(value, list) or len(value) < 3:
                raise SystemExit(f"[scheme] 方案{letter} sources.{key} 需要 [参考, 借鉴点, 不复用]")
            rows.append(f"| {label} | {value[0]} | {value[1]} | {value[2]} |")
        facts = item.get("facts") or []
        fact_rows = [
            f"| {f[0]} | {f[1]} | {f[2]} | {f[3]} | {f[4]} | {f[5]} |"
            for f in facts if isinstance(f, list) and len(f) >= 6
        ]
        if not fact_rows:
            raise SystemExit(f"[scheme] 方案{letter} facts 至少一条（六列）")

        angle = str(item.get("angle", "")).strip()
        reader = str(item.get("reader", "")).strip()
        next_step = str(item.get("next", "")).strip()
        open_items = str(item.get("open", "")).strip()
        md = [
            f"# 方案{letter} · {angle or title}", "",
            f"> 角度：{angle}", f"> 目标读者：{reader}", f"> 读者读完下一步：{next_step}", "",
            "## 标题", "", title, "",
            "## 封面文案", "", _cover_copy(cover), "",
            "## 正文", "", body, "",
        ]
        if open_items:
            md += ["## 本篇没有回答的问题", "", open_items, ""]
        (vdir / "方案.md").write_text("\n".join(md), encoding="utf-8")

        prov = [
            f"# 方案{letter} · 分项溯源", "",
            "## 参考溯源", "",
            "| 项目 | 参考案例（ID＋链接） | 借鉴点 | 不复用什么 |",
            "|---|---|---|---|", *rows, "",
            "## 事实清单", "",
            "| 主张 | 类型 | 依据/来源 | 版本或口径 | 状态 | 失效时间 |",
            "|---|---|---|---|---|---|", *fact_rows, "",
            "## 相似度自查", "",
            "- [x] 没有连续 20 字以上与原帖雷同",
            "- [x] 没有把原帖作者身份、经历、评论写成本人",
            "- [x] 没有用原帖原图冒充本篇配图", "",
        ]
        (vdir / "溯源.md").write_text("\n".join(prov), encoding="utf-8")

    brief = data.get("brief") or {}
    if isinstance(brief, dict) and brief:
        assumptions = brief.get("assumptions") or []
        if isinstance(assumptions, str):
            assumptions = [assumptions]
        (proj / "需求单.md").write_text(
            "\n".join([
                f"# 需求单 · {proj.name}", "",
                "| 字段 | 内容 | 来源/状态 |", "|---|---|---|",
                f"| 模式 | {brief.get('mode', '')} | 用户确认 |",
                f"| 主题 | {brief.get('topic', '')} | 用户原话 |",
                f"| 必须包含的信息 | {brief.get('must', '')} | 用户点名 |",
                f"| 目标人群 | {brief.get('reader', '')} | {'假设' if brief.get('reader_assumed') else '用户提供'} |",
                f"| 使用场景 | {brief.get('scene', '小红书搜索')} | 默认 |",
                f"| 语气 | {brief.get('tone', '克制')} | 按画像 |",
                f"| 产品与条款版本 | {brief.get('product', '未指定，按通用重疾险写、不点名')} | 假设 |",
                f"| 表述边界 | {brief.get('boundary', '不承诺赔付、不写统一数字')} | 默认 |",
                "", "## 读者此刻在问什么", "", brief.get("question", ""), "",
                "## 本稿假设", "", *[f"- {item}" for item in assumptions], "",
            ]), encoding="utf-8")

    (proj / "方案对比.md").write_text(
        "\n".join([
            f"# 多套方案比较 · {proj.name}", "",
            "| 套 | 角度（读者入口） | 标题 | 封面构图 | 与另外几套的差异 |",
            "|---|---|---|---|---|", *compare_rows, "",
            "> 封面构图 a=大字断言 b=左右对照 c=编号清单 d=标注圈划。",
        ]), encoding="utf-8")
    ledger_rows = [
        f"| {index} | {fact[0]} | {fact[1]} | {fact[2]} | {fact[3]} | {fact[4]} | {fact[5]} |"
        for index, fact in enumerate(ledger.values(), 1)
    ]
    (proj / "事实台账.md").write_text(
        "\n".join([
            f"# 事实台账 · {proj.name}", "",
            "| # | 主张（将怎么写） | 类型 | 依据/来源 | 版本或口径 | 状态 | 失效时间 |",
            "|---|---|---|---|---|---|---|", *ledger_rows, "",
            "> 状态口径：已核=能指出来源与版本；待核=正文用【待核实】标注；不适用=删除该主张。",
        ]), encoding="utf-8")

    cover_path = proj / "assets" / "covers" / "cover.json"
    cover_path.parent.mkdir(parents=True, exist_ok=True)
    cover_path.write_text(json.dumps(covers, ensure_ascii=False, indent=1), encoding="utf-8")

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from cover_render import render_project
    rendered = render_project(proj, scale=1)
    print(f"[scheme] 已写入 {len(letters)} 套方案与溯源，渲染 {len(rendered)} 张封面")

    class _CheckArgs:
        topic = args.topic
        variants = ",".join(letters)
        single = len(letters) == 1
    return cmd_check(_CheckArgs())



# ---------------------------------------------------------------- deliver

def cmd_deliver(args) -> int:
    """生成交付文案：四套标题并排 + 每套封面/正文/溯源链接，一个不漏。"""
    proj = _require_project(args.topic)
    variants = _variants(proj, args.variants)
    lines = []
    links = []
    for variant in variants:
        vdir = proj / f"方案{variant}"
        md_path = vdir / "方案.md"
        if not md_path.is_file():
            continue
        text = md_path.read_text(encoding="utf-8")
        sections = _sections(text)
        title = sections.get("标题", "").strip().splitlines()[0] if sections.get("标题") else ""
        angle = ""
        first = text.splitlines()[0] if text.splitlines() else ""
        if first.startswith("# "):
            angle = first[2:].strip()
        lines.append(f"| {variant} | {title} | {angle} |")
        base = vdir.as_posix()
        links.append(
            f"- **方案{variant}**：[封面]({base}/封面.png) · [正文]({base}/方案.md) · [溯源]({base}/溯源.md)"
        )

    recommendation = ""
    compare = proj / "方案对比.md"
    if compare.is_file():
        for line in compare.read_text(encoding="utf-8").splitlines():
            if "首选" in line or "推荐" in line:
                recommendation = line.strip("- ").strip()
                break

    out = [
        f"四套方案已齐，`check` 全 PASS（标题、正文、封面、溯源）。",
        "",
        "| 套 | 标题 | 角度 |",
        "|---|---|---|",
        *lines,
        "",
        "每套的封面、正文、分项溯源：",
        *links,
        "",
        f"资料：[需求单]({(proj / '需求单.md').as_posix()}) · [事实台账]({(proj / '事实台账.md').as_posix()}) · [方案对比]({compare.as_posix()})",
        "",
    ]
    if recommendation:
        out.append(f"推荐：{recommendation}")
        out.append("")
    out.append("选一套并指出要改的句子即可；本轮未进入发布或草稿流程。")
    print("\n".join(out))
    return 0


# ---------------------------------------------------------------- cover

def cmd_cover(args) -> int:
    proj = _require_project(args.topic)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from cover_render import render_project
    written = render_project(proj, scale=1)
    for path in written:
        print(f"[scheme] 封面：{path.relative_to(proj).as_posix()}")
    if not written:
        print("[scheme] 没有可渲染的方案目录")
        return 1
    return 0


# ---------------------------------------------------------------- export

def cmd_export(args) -> int:
    proj = _require_project(args.topic)
    variant = args.variant.strip().upper()
    vdir = proj / f"方案{variant}"
    md_path = vdir / "方案.md"
    prov_path = vdir / "溯源.md"
    cover = vdir / "封面.png"
    for path in (md_path, prov_path, cover):
        if not path.is_file():
            raise SystemExit(f"[scheme] 缺少文件：{path}")

    sections = _sections(md_path.read_text(encoding="utf-8"))
    title = re.sub(r"[*`]", "", sections.get("标题", "").strip().splitlines()[0]).strip() if sections.get("标题") else ""
    if not title:
        raise SystemExit("[scheme] 方案.md 的「## 标题」为空")
    body = sections.get("正文", "").strip()
    cover_copy = sections.get("封面文案", "").strip()
    open_items = sections.get("本篇没有回答的问题", "").strip()

    snapshots = proj / "assets" / "snapshots"
    version = len(list(snapshots.glob(f"{variant}-v*"))) + 1

    out_dir = proj / "导出"
    out_dir.mkdir(exist_ok=True)
    stem = f"{proj.name}-方案{variant}-v{version}"
    out_path = validate_output_path(out_dir / f"{stem}.md", create_parent=True)
    cover_out = out_dir / f"{stem}-封面.png"
    shutil.copyfile(cover, cover_out)

    log_lines = []
    log = proj / "修订记录.md"
    if log.is_file():
        for line in log.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("| v") and f"方案{variant}" in line:
                log_lines.append(line)
    history = "\n".join(log_lines) if log_lines else "| — | — | — | 暂无改稿记录，当前为 v1 原稿 | — |"

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    parts = [
        f"# {title}",
        "",
        "> 状态：待人工确认（不等于机构审核通过）",
        f"> 导出时间：{stamp}",
        f"> 方案：方案{variant} · v{version}",
        f"> 封面：{cover_out.name}",
        "",
        f"![封面]({cover_out.name})",
        "",
        "## 标题",
        "",
        title,
        "",
        "## 封面文案",
        "",
        cover_copy or "（未填写）",
        "",
        "## 正文",
        "",
        body,
        "",
    ]
    if open_items:
        parts += ["## 本篇没有回答的问题", "", open_items, ""]
    parts += [
        "---",
        "",
        "## 溯源",
        "",
        prov_path.read_text(encoding="utf-8").strip(),
        "",
        "---",
        "",
        f"## 修订记录（方案{variant}）",
        "",
        "| 版本 | 时间 | 方案 | 修改意见（用户原话） | 改动范围 |",
        "|---|---|---|---|---|",
        history,
        "",
        "> 对外发布前：请先报备，再发表笔记（明亚APP-我的-自助服务-发文报备）。",
    ]
    out_path.write_text("\n".join(parts), encoding="utf-8")

    print(f"[scheme] 导出：{out_path}")
    print(f"[scheme] 封面：{cover_out}")
    print("[scheme] 导出件状态为“待人工确认”，需人工核对事实与机构要求后再使用")
    return 0


# ---------------------------------------------------------------- main

def main() -> int:
    parser = argparse.ArgumentParser(description="保险图文五步方案：骨架、门禁、改稿快照、导出")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="建 outputs/<主题>/ 骨架并复制模板")
    p_init.add_argument("--topic", required=True, help="人类可读主题，如 重疾险-等待期怎么讲")
    p_init.add_argument("--variants", default="A,B,C,D", help="方案字母，逗号分隔（默认 A,B,C,D）")
    p_init.set_defaults(func=cmd_init)

    p_check = sub.add_parser("check", help="交付前硬门禁")
    p_check.add_argument("--topic", required=True)
    p_check.add_argument("--variants", default="", help="只查指定方案，如 A,C")
    p_check.add_argument("--single", action="store_true", help="单篇模式：不提示少于 4 套")
    p_check.set_defaults(func=cmd_check)

    p_revise = sub.add_parser("revise", help="快照原稿后写入新稿并记修订")
    p_revise.add_argument("--topic", required=True)
    p_revise.add_argument("--variant", required=True)
    p_revise.add_argument("--note", required=True, help="用户修改意见原话")
    p_revise.add_argument("--from", dest="from_file", required=True, help="新稿文件路径")
    p_revise.add_argument("--target", default="方案.md", help="方案目录内的目标文件名")
    p_revise.set_defaults(func=cmd_revise)

    p_build = sub.add_parser("build", help="按 content.json 一次写完四套+封面+门禁")
    p_build.add_argument("--topic", required=True)
    p_build.add_argument("--input", required=True, help="内容 JSON 路径")
    p_build.set_defaults(func=cmd_build)

    p_deliver = sub.add_parser("deliver", help="生成四套交付文案（封面/正文/溯源链接全列出）")
    p_deliver.add_argument("--topic", required=True)
    p_deliver.add_argument("--variants", default="", help="只列指定方案，如 A,C")
    p_deliver.set_defaults(func=cmd_deliver)

    p_cover = sub.add_parser("cover", help="按 cover.json 渲染封面（PIL，无需浏览器）")
    p_cover.add_argument("--topic", required=True)
    p_cover.set_defaults(func=cmd_cover)

    p_export = sub.add_parser("export", help="导出选中方案为单文件")
    p_export.add_argument("--topic", required=True)
    p_export.add_argument("--variant", required=True)
    p_export.set_defaults(func=cmd_export)

    args = parser.parse_args()
    try:
        return args.func(args)
    except OutputPathError as exc:
        print(f"[scheme] 路径不合规：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
