#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第二课堂学分规则查询 CLI（低 Token：只返回最相关的少数规则，不读取整份 PDF）。

用法：
  python scripts/search_rules.py search --query "挑战杯 获奖 一等奖" [--cohort 2025]
      [--category 三创] [--type 获奖] [--limit 5] [--format text|json]
  python scripts/search_rules.py get --rule-id SC-2025-019 [--format text|json]
  python scripts/search_rules.py compute --rule-id SC-2025-020 [--team-rank 2]
  python scripts/search_rules.py check --rule-id SC-2025-042 --evidence "收录通知书"
  python scripts/search_rules.py policies [--cohort 2025]
  python scripts/search_rules.py build-index

search 状态：ok（唯一明确匹配）/ multiple_candidates（需补充信息）/
            not_found（未找到，待核验）/ policy_not_available（该年级政策未建档）。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rules_lib as L  # noqa: E402


def emit(obj, fmt):
    if fmt == "text":
        print(_text(obj))
    else:
        print(json.dumps(obj, ensure_ascii=False, indent=2))


def _fmt_rule_line(r):
    pts = r.get("points")
    pts_s = "人工认定" if pts is None else f"{pts}学分"
    lvl = r.get("competition_level") or ""
    award = r.get("award_level_or_rank") or ""
    lvl_s = ("｜" + lvl + award) if (lvl or award) else ""
    return (f"[{r.get('rule_id')}] {r.get('activity_name')}{lvl_s} → {pts_s} "
            f"｜{r.get('category')}｜{r.get('source_clause_or_table')} p{r.get('source_page')}")


def _text(obj):
    if "final_points" in obj:  # compute
        if obj.get("error"):
            return f"{obj.get('rule_id')}: ERROR {obj['error']}"
        if obj.get("final_points") is None:
            return f"{obj.get('rule_id')}: {obj.get('note', '无固定分值')}"
        if obj.get("coefficient") is not None:
            return (f"{obj['rule_id']}: base={obj['base_points']} rank={obj['team_rank']} "
                    f"coef={obj['coefficient']} → final={obj['final_points']}（{obj.get('calculation')}）")
        return f"{obj['rule_id']}: final={obj['final_points']}（{obj.get('note', '')}）"
    if "candidates" not in obj:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    lines = [f"query: {obj.get('query')}  cohort: {obj.get('cohort')}  status: {obj.get('status')}"]
    pol = obj.get("policy")
    if pol:
        lines.append(f"policy: {pol.get('doc_number')}（{pol.get('applicable_cohorts')}）")
    if obj.get("message"):
        lines.append(obj["message"])
    if obj.get("needs_clarification"):
        lines.append("needs_clarification: " + ", ".join(obj["needs_clarification"]))
    for r in obj["candidates"]:
        lines.append("  " + _fmt_rule_line(r))
        if r.get("cap"):
            lines.append(f"    cap: {r['cap'].get('note')}")
        if r.get("team_rank_coefficients"):
            lines.append(f"    team_coefficients: {r['team_rank_coefficients']}")
        if r.get("evidence_requirements"):
            lines.append(f"    evidence: {'；'.join(r['evidence_requirements'])}")
        if r.get("exceptions"):
            lines.append(f"    exceptions: {r['exceptions']}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="第二课堂学分规则查询")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="按活动名称/关键词检索规则")
    s.add_argument("--query", required=True)
    s.add_argument("--cohort", default=None, help="入学年级，如 2025（2024 级及之前政策未建档）")
    s.add_argument("--category", default=None, help="思政/三创/体育 或政策类别全称")
    s.add_argument("--type", default=None, help="参赛/获奖/结业/荣誉/发表/立项/结项/任职/实践/观摩")
    s.add_argument("--limit", type=int, default=5)
    s.add_argument("--format", choices=["text", "json"], default="json")

    s = sub.add_parser("get", help="按规则 ID 取单条规则")
    s.add_argument("--rule-id", required=True)
    s.add_argument("--format", choices=["text", "json"], default="json")

    s = sub.add_parser("compute", help="计算最终学分（含集体项目排名系数）")
    s.add_argument("--rule-id", required=True)
    s.add_argument("--team-rank", default=None, help="团队内排名（1 起）")
    s.add_argument("--format", choices=["text", "json"], default="json")

    s = sub.add_parser("check", help="核对证明材料是否满足规则")
    s.add_argument("--rule-id", required=True)
    s.add_argument("--evidence", default="", help="已具备的证明，逗号分隔")
    s.add_argument("--format", choices=["text", "json"], default="json")

    s = sub.add_parser("policies", help="政策版本与适用年级")
    s.add_argument("--cohort", default=None)
    s.add_argument("--format", choices=["text", "json"], default="json")

    s = sub.add_parser("build-index", help="由 rules.jsonl 重建 index.json 与 source_map.json")

    a = ap.parse_args()
    rules = L.load_rules()
    metadata = L.load_metadata()

    if a.cmd == "search":
        emit(L.search_rules(a.query, cohort=a.cohort, category=a.category,
                            ptype=a.type, limit=a.limit, rules=rules, metadata=metadata), a.format)
    elif a.cmd == "get":
        r = L.get_rule(a.rule_id, rules)
        if not r:
            emit({"status": "not_found", "message": f"规则不存在: {a.rule_id}"}, a.format)
            sys.exit(2)
        emit({"status": "ok", "rule": L._brief(r)}, a.format)
    elif a.cmd == "compute":
        r = L.get_rule(a.rule_id, rules)
        if not r:
            emit({"status": "not_found", "message": f"规则不存在: {a.rule_id}"}, a.format)
            sys.exit(2)
        emit(L.compute_points(r, team_rank=a.team_rank), a.format)
    elif a.cmd == "check":
        r = L.get_rule(a.rule_id, rules)
        if not r:
            emit({"status": "not_found", "message": f"规则不存在: {a.rule_id}"}, a.format)
            sys.exit(2)
        provided = [x.strip() for x in a.evidence.split(",") if x.strip()]
        emit(L.check_evidence(r, provided), a.format)
    elif a.cmd == "policies":
        out = {"policies": [{k: p.get(k) for k in (
            "policy_id", "doc_number", "title", "applicable_cohorts",
            "cohort_min_year", "cohort_max_year", "rules_available", "verification_status", "notes")}
            for p in metadata.get("policies", [])]}
        if a.cohort:
            pol, gate = L.policy_for_cohort(metadata, a.cohort)
            out["cohort"] = a.cohort
            out["applicable_policy"] = pol.get("policy_id") if pol else None
            out["gate"] = gate
        emit(out, a.format)
    elif a.cmd == "build-index":
        idx = L.build_index(rules, metadata)
        with open(L.INDEX_JSON, "w", encoding="utf-8") as f:
            json.dump(idx, f, ensure_ascii=False, indent=2)
        sm = L.build_source_map(rules, metadata)
        with open(L.SOURCE_MAP_JSON, "w", encoding="utf-8") as f:
            json.dump(sm, f, ensure_ascii=False, indent=2)
        print(f"index.json: {idx['rule_count']} rules, {len(idx['keywords'])} keywords")
        print(f"source_map.json: {sum(len(v['rules']) for v in sm.values())} mappings")


if __name__ == "__main__":
    main()
