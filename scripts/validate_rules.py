#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""规则库校验：字段完整性、重复 ID、索引一致性、原文覆盖检查。

用法：
  python scripts/validate_rules.py [--rules rules/rules.jsonl] [--rebuild-index] [--json]

退出码：0 = 无错误（允许有警告）；1 = 存在错误。
"""
import argparse
import json
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rules_lib as L  # noqa: E402

# 广大〔2025〕81号 必需要件覆盖清单：key = 条款/表格标识，where = rules/metadata/none
COVERAGE_CHECKLIST = [
    ("第四条", "rules", "毕业6学分与残障减免"),
    ("第五条", "rules", "三类各至少2学分"),
    ("第七条", "rules", "集中认定时间与程序"),
    ("第八条", "metadata", "校团委统筹、教务处最终认定"),
    ("第九条", "rules", "弄虚作假处理"),
    ("第十条", "metadata", "适用年级与旧版衔接"),
    ("第十一条", "none", "解释修订主体（程序性条款，无需建档）"),
    ("附表一、（一）", "rules", "党团课培训"),
    ("附表一、（二）", "rules", "思想政治教育活动（线下/线上）"),
    ("附表一、（三）", "rules", "青马工程"),
    ("附表一、（四）", "rules", "突出表现表彰"),
    ("附表一、（五）", "rules", "精神文明活动表彰"),
    ("附表一、（六）", "rules", "学生干部与社团荣誉"),
    ("附表一、（七）", "rules", "寒暑假社会实践"),
    ("附表一、（八）", "rules", "非寒暑假社会实践（分时长档）"),
    ("附表一、（九）", "rules", "志愿服务（分时长档）"),
    ("附表一、（十）", "rules", "西部计划/三支一扶"),
    ("附表一、（十一）", "rules", "无偿献血"),
    ("附表一、（十二）", "rules", "技能培训与资格考试"),
    ("附表一、（十三）", "rules", "社会实践/志愿服务荣誉"),
    ("附表二、（一）", "rules", "学术科技活动立项与结项"),
    ("附表二、（二）表1", "rules", "学术科技竞赛参赛计分"),
    ("附表二、（二）表2", "rules", "学术科技竞赛获奖计分"),
    ("附表二、（三）表3", "rules", "学术科技成果计分"),
    ("附表二、（三）2", "rules", "艺术类作品/体育实践成果认定"),
    ("附表二、（四）", "rules", "学术报告讲座等"),
    ("附表二、（五）1", "rules", "在校大学生创业"),
    ("附表二、（五）2", "rules", "科研项目/社会调查/创新实验"),
    ("附表二、（五）3", "rules", "生涯论坛/职业课程/招聘会"),
    ("附表二、（五）4", "rules", "其他特殊创新创业活动"),
    ("附表三、（一）", "rules", "经典百书阅读与诵读"),
    ("附表三、（二）表4", "rules", "文体比赛获奖计分"),
    ("附表三、（三）", "rules", "参加文体比赛/观摩"),
    ("附表三、（四）1", "rules", "文学作品发表"),
    ("附表三、（四）2", "rules", "视频音频图像录用转载"),
    ("附表三、（四）3", "rules", "新闻报道"),
    ("附表四", "rules", "其他确需计算的学分"),
]

PAGE_RE = re.compile(r"^\d+(-\d+)?$")


def norm(s):
    return re.sub(r"\s+", "", s or "")


def main():
    ap = argparse.ArgumentParser(description="规则库校验")
    ap.add_argument("--rules", default=L.RULES_JSONL)
    ap.add_argument("--rebuild-index", action="store_true", help="先重建 index.json/source_map.json 再校验")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    errors, warnings = [], []
    try:
        rules = L.load_rules(args.rules)
    except Exception as e:
        print(f"FATAL: 无法读取规则文件 {args.rules}: {e}")
        sys.exit(1)
    metadata = L.load_metadata()

    if args.rebuild_index:
        with open(L.INDEX_JSON, "w", encoding="utf-8") as f:
            json.dump(L.build_index(rules, metadata), f, ensure_ascii=False, indent=2)
        with open(L.SOURCE_MAP_JSON, "w", encoding="utf-8") as f:
            json.dump(L.build_source_map(rules, metadata), f, ensure_ascii=False, indent=2)

    known_policies = {p.get("policy_id") for p in metadata.get("policies", [])}

    # 1. 字段完整性与取值
    seen_ids = {}
    for r in rules:
        rid = r.get("rule_id") or f"<line {r.get('_line')}>"
        for f in L.REQUIRED_FIELDS:
            if f not in r:
                errors.append(f"{rid}: 缺少字段 {f}")
        if r.get("rule_id") in seen_ids:
            errors.append(f"{rid}: rule_id 重复（首次出现在第 {seen_ids[r['rule_id']]} 行）")
        seen_ids[r.get("rule_id")] = r.get("_line")
        if r.get("policy_id") not in known_policies:
            errors.append(f"{rid}: policy_id {r.get('policy_id')} 不在 policy_metadata.json 中")
        if r.get("category") not in L.CATEGORIES:
            errors.append(f"{rid}: 非法 category {r.get('category')!r}")
        if r.get("rule_type") == "activity_rule":
            pts = r.get("points")
            if not isinstance(pts, (int, float)) or pts < 0:
                errors.append(f"{rid}: activity_rule 的 points 必须为非负数字（现为 {pts!r}）")
            if not r.get("keywords"):
                errors.append(f"{rid}: activity_rule 的 keywords 不能为空")
        if r.get("verification_status") not in ("已核验", "待核验"):
            errors.append(f"{rid}: 非法 verification_status {r.get('verification_status')!r}")
        if not PAGE_RE.match(str(r.get("source_page") or "")):
            errors.append(f"{rid}: source_page 格式非法: {r.get('source_page')!r}（应为页码或 5-6 形式）")
        if not (r.get("source_excerpt") or "").strip():
            errors.append(f"{rid}: source_excerpt 不能为空（规则必须可追溯到原文）")
        if not isinstance(r.get("evidence_requirements"), list):
            errors.append(f"{rid}: evidence_requirements 必须为列表")
        coefs = r.get("team_rank_coefficients")
        if coefs is not None:
            need = {"1", "2", "3", "4", "5_and_below"}
            if set(coefs) != need:
                errors.append(f"{rid}: team_rank_coefficients 键必须为 1/2/3/4/5_and_below（现为 {sorted(coefs)}）")

    # 2. 重复内容记录（同活动+同级别+同等级+同分值）
    sig = Counter((r.get("category"), r.get("activity_name"), r.get("competition_level"),
                   r.get("award_level_or_rank"), r.get("points")) for r in rules)
    for k, n in sig.items():
        if n > 1:
            warnings.append(f"疑似重复记录 ×{n}: {k}")

    # 3. 索引一致性
    if os.path.exists(L.INDEX_JSON):
        idx = json.load(open(L.INDEX_JSON, encoding="utf-8"))
        idx_ids = set()
        for ids in idx.get("keywords", {}).values():
            idx_ids.update(ids)
        for ids in idx.get("by_category", {}).values():
            idx_ids.update(ids)
        rule_ids = {r["rule_id"] for r in rules}
        if idx.get("rule_count") != len(rules):
            errors.append(f"index.json rule_count={idx.get('rule_count')} 与 rules.jsonl({len(rules)}) 不一致，请运行 --rebuild-index")
        if idx_ids != rule_ids:
            errors.append("index.json 覆盖的 rule_id 与 rules.jsonl 不一致，请运行 --rebuild-index")
    else:
        warnings.append("index.json 不存在，请运行 --rebuild-index 生成")

    # 4. 原文覆盖检查
    meta_text = norm(json.dumps(metadata, ensure_ascii=False))
    clause_text = norm(" ".join(r.get("source_clause_or_table") or "" for r in rules))
    coverage = []
    for key, where, desc in COVERAGE_CHECKLIST:
        k = norm(key)
        if where == "rules":
            hits = [r["rule_id"] for r in rules if k in norm(r.get("source_clause_or_table"))]
            coverage.append({"item": key, "desc": desc, "where": where,
                             "covered": bool(hits), "rules": hits[:6]})
            if not hits:
                errors.append(f"覆盖检查失败: 原文 {key}（{desc}）没有对应规则")
        elif where == "metadata":
            ok = k in meta_text
            coverage.append({"item": key, "desc": desc, "where": where, "covered": ok})
            if not ok:
                errors.append(f"覆盖检查失败: 原文 {key}（{desc}）未在 policy_metadata.json 中体现")
        else:
            coverage.append({"item": key, "desc": desc, "where": where, "covered": True,
                             "note": "程序性条款，无需建档"})

    by_cat = Counter(r.get("category") for r in rules)
    by_status = Counter(r.get("verification_status") for r in rules)
    by_policy = Counter(r.get("policy_id") for r in rules)

    report = {
        "rules_file": args.rules,
        "rule_count": len(rules),
        "by_category": dict(by_cat),
        "by_verification_status": dict(by_status),
        "by_policy": dict(by_policy),
        "coverage": coverage,
        "coverage_covered": sum(1 for c in coverage if c["covered"]),
        "coverage_total": len(coverage),
        "errors": errors,
        "warnings": warnings,
        "ok": not errors,
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"规则总数: {len(rules)}")
        print(f"按类别: {dict(by_cat)}")
        print(f"按核验状态: {dict(by_status)}")
        print(f"按政策: {dict(by_policy)}")
        print(f"原文覆盖: {report['coverage_covered']}/{report['coverage_total']}")
        for c in coverage:
            mark = "✓" if c["covered"] else "✗"
            ids = ",".join(c.get("rules", [])[:4]) if c.get("rules") else ""
            print(f"  {mark} {c['item']} {c['desc']}" + (f" → {ids}" if ids else ""))
        for w in warnings:
            print(f"WARN: {w}")
        for e in errors:
            print(f"ERROR: {e}")
        print("结果:", "通过" if not errors else f"失败（{len(errors)} 个错误）")
    sys.exit(0 if not errors else 1)


if __name__ == "__main__":
    main()
