# -*- coding: utf-8 -*-
"""第二课堂学分规则库：共享加载/检索/计算逻辑。

供 scripts/search_rules.py、scripts/validate_rules.py、scripts/declare.py 及 tests/ 使用。
规则数据在 rules/rules.jsonl（唯一事实源），索引 rules/index.json 为派生产物。
"""
import json
import os
import re
from decimal import Decimal, ROUND_HALF_UP

RULES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rules")
RULES_JSONL = os.path.join(RULES_DIR, "rules.jsonl")
INDEX_JSON = os.path.join(RULES_DIR, "index.json")
METADATA_JSON = os.path.join(RULES_DIR, "policy_metadata.json")
SOURCE_MAP_JSON = os.path.join(RULES_DIR, "source_map.json")

POLICY_2025 = "GZU-2KC-2025-81"
POLICY_2022 = "GZU-JXT-2KC-2022"
POLICY_2021 = "GZU-2KC-2021-80"

CATEGORIES = ["思想成长与实践公益教育类", "创新与创业实训实践类", "体育运动与审美体验类",
              "思想政治课社会实践类", "劳动教育课程类", "政策总则"]
# 类别别名按政策分流：同名别名在两套政策里指向不同类别全称
CATEGORY_ALIASES_2025 = {
    "思政": "思想成长与实践公益教育类", "思想政治": "思想成长与实践公益教育类",
    "思想成长": "思想成长与实践公益教育类", "公益": "思想成长与实践公益教育类",
    "三创": "创新与创业实训实践类", "创新": "创新与创业实训实践类",
    "创业": "创新与创业实训实践类", "创新创业": "创新与创业实训实践类",
    "体育": "体育运动与审美体验类", "体育与审美": "体育运动与审美体验类",
    "审美": "体育运动与审美体验类", "文体": "体育运动与审美体验类",
}
CATEGORY_ALIASES_OLD = {
    "思政": "思想政治课社会实践类", "思想政治": "思想政治课社会实践类",
    "思政实践": "思想政治课社会实践类", "社会实践": "思想政治课社会实践类",
    "三创": "创新与创业实训实践类", "创新": "创新与创业实训实践类",
    "创业": "创新与创业实训实践类", "创新创业": "创新与创业实训实践类",
    "体育": "体育运动与审美体验类", "体育与审美": "体育运动与审美体验类",
    "审美": "体育运动与审美体验类", "文体": "体育运动与审美体验类",
    "劳动": "劳动教育课程类", "劳动教育": "劳动教育课程类",
}
TYPE_ALIASES = {
    "参赛": "participation", "参加": "participation", "参与": "participation",
    "获奖": "award", "奖项": "award", "得奖": "award",
    "结业": "completion", "通过": "completion", "考核": "completion",
    "荣誉": "honor", "表彰": "honor",
    "发表": "publication", "录用": "publication", "转载": "publication",
    "立项": "project_initiation", "结项": "project_completion",
    "任职": "role", "干部": "role",
    "实践": "practice", "服务": "practice", "志愿": "practice",
    "观摩": "audience", "观众": "audience",
}

REQUIRED_FIELDS = [
    "rule_id", "policy_id", "rule_type", "applicable_cohorts", "category", "subcategory",
    "activity_name", "keywords", "eligibility_conditions", "participation_or_award_type",
    "points", "points_qualifier", "calculation_method", "individual_or_team",
    "evidence_requirements", "source_document", "source_page", "source_clause_or_table",
    "source_excerpt", "verification_status",
]


def load_metadata(path=METADATA_JSON):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_rules(path=RULES_JSONL):
    rules = []
    with open(path, encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            r["_line"] = ln
            rules.append(r)
    return rules


def get_policy(metadata, policy_id):
    for p in metadata.get("policies", []):
        if p.get("policy_id") == policy_id:
            return p
    return None


def policy_for_cohort(metadata, cohort):
    """按入学年级返回适用政策。cohort 为入学年份(int/str)。返回 (policy, gate) 。

    gate 非空表示该年级政策不可用（需提示用户提供旧版官方文件）。
    """
    if cohort is None:
        return None, None
    try:
        year = int(str(cohort).strip().replace("级", ""))
    except ValueError:
        return None, f"无法解析入学年级: {cohort!r}（应为年份，如 2025）"
    for p in metadata.get("policies", []):
        if p.get("cohort_min_year") is not None and year >= p["cohort_min_year"]:
            if p.get("rules_available"):
                return p, None
            return p, (f"{year}级适用 {p['doc_number']}，但该政策原文尚未录入规则库"
                       f"（{p.get('notes', '')}）。请提供官方文件后另行建档，"
                       f"不得套用 2025 版规则或推测分值。")
    for p in metadata.get("policies", []):
        if p.get("cohort_max_year") is not None and year <= p["cohort_max_year"]:
            if p.get("rules_available"):
                return p, None
            return p, (f"{year}级适用 {p['doc_number']}，但该政策原文尚未录入规则库"
                       f"（{p.get('notes', '')}）。请提供官方文件后另行建档，"
                       f"不得套用 2025 版规则或推测分值。")
    return None, f"未找到适用于 {year} 级的政策"


def resolve_category(category, policy_id=None):
    if not category:
        return None
    c = category.strip()
    if c in CATEGORIES:
        return c
    table = CATEGORY_ALIASES_OLD if policy_id == POLICY_2022 else CATEGORY_ALIASES_2025
    return table.get(c)


def resolve_type(ptype):
    if not ptype:
        return None
    t = ptype.strip()
    return TYPE_ALIASES.get(t, t)


def _tokenize(query):
    parts = re.split(r"[\s,，、;；/]+", query.strip())
    return [p for p in parts if p]


def _score_rule(rule, tokens, full_query):
    name = rule.get("activity_name") or ""
    kws = rule.get("keywords") or []
    hay = " ".join([name, rule.get("subcategory") or "", rule.get("category") or "",
                    rule.get("eligibility_conditions") or ""] + kws)
    score, hits = 0, []
    for t in tokens:
        if t in name:
            score += 10
            hits.append(t)
        elif any(t in k for k in kws):
            score += 6
            hits.append(t)
        elif t in hay:
            score += 2
            hits.append(t)
    if full_query and full_query in name:
        score += 8
    return score, hits


DIFF_FIELDS = ["competition_level", "award_level_or_rank", "points", "hours_range",
               "individual_or_team", "eligibility_conditions"]


def search_rules(query, cohort=None, category=None, ptype=None, limit=5,
                 rules=None, metadata=None):
    """按活动名称/关键词检索规则。返回低 Token 的结构化结果。"""
    rules = rules if rules is not None else load_rules()
    metadata = metadata if metadata is not None else load_metadata()
    result = {"query": query, "cohort": cohort, "candidates": [], "needs_clarification": []}

    policy, gate = policy_for_cohort(metadata, cohort)
    if gate:
        result["status"] = "policy_not_available"
        result["message"] = gate
        result["policy"] = {"policy_id": policy.get("policy_id") if policy else None}
        return result
    if policy:
        result["policy"] = {"policy_id": policy["policy_id"],
                            "doc_number": policy.get("doc_number"),
                            "applicable_cohorts": policy.get("applicable_cohorts")}

    cat = resolve_category(category, policy["policy_id"] if policy else None)
    if category and not cat:
        result["status"] = "bad_request"
        aliases = "思政/三创/体育/劳动教育" if (policy and policy["policy_id"] == POLICY_2022) else "思政/三创/体育"
        result["message"] = f"未知类别: {category}（可用: {aliases} 或政策类别全称）"
        return result
    typ = resolve_type(ptype)
    if ptype and not typ:
        result["status"] = "bad_request"
        result["message"] = f"未知参与/获奖类型: {ptype}"
        return result

    pool = rules
    if policy:
        pool = [r for r in pool if r.get("policy_id") == policy["policy_id"]]
    if cat:
        pool = [r for r in pool if r.get("category") == cat]
    if typ:
        pool = [r for r in pool if r.get("participation_or_award_type") == typ]

    tokens = _tokenize(query)
    scored = []
    for r in pool:
        s, hits = _score_rule(r, tokens, query.strip())
        if s > 0:
            scored.append((s, hits, r))
    scored.sort(key=lambda x: (-x[0], x[2]["rule_id"]))
    top = scored[:limit]

    if not top:
        doc = policy.get("doc_number") if policy else "广大〔2025〕81号"
        catch_all = "PL-2022-013" if (policy and policy["policy_id"] == POLICY_2022) else "PL-2025-010"
        result["status"] = "not_found"
        result["message"] = (f"未找到匹配规则（待核验）。可能原因：活动不在{doc}附表内、"
                             f"关键词不一致、或需要人工认定（见 {catch_all}）。"
                             "请补充活动全称/类别/获奖情况，或查阅官方文件后补建规则；在核验前不得直接申报。")
        return result

    best = top[0][0]
    close = [t for t in top if t[0] >= best - 2]
    for s, hits, r in top:
        result["candidates"].append(_brief(r, hits))

    differing = []
    for f in DIFF_FIELDS:
        vals = {json.dumps(r.get(f), ensure_ascii=False) for _, _, r in close}
        if len(vals) > 1:
            differing.append(f)
    if len(close) > 1 and differing:
        result["status"] = "multiple_candidates"
        result["needs_clarification"] = differing
        result["message"] = (f"找到 {len(close)} 条相近候选规则，它们在 {('、'.join(differing))} 上存在差异。"
                             "请补充：赛事级别、获奖等级/名次、个人或团队及排名、累计时长等信息后再确认。")
    else:
        result["status"] = "ok"
        result["message"] = "已找到明确匹配的规则。"
    return result


def _brief(rule, hits=None):
    b = {k: rule.get(k) for k in (
        "rule_id", "policy_id", "category", "subcategory", "activity_name",
        "participation_or_award_type", "competition_level", "award_level_or_rank",
        "points", "points_qualifier", "calculation_method", "coefficient",
        "team_rank_coefficients", "individual_or_team", "cap", "duplicate_credit_rules",
        "evidence_requirements", "exceptions", "hours_range",
        "source_document", "source_page", "source_clause_or_table", "source_excerpt",
        "verification_status", "notes")}
    b["matched_keywords"] = hits or []
    return b


def get_rule(rule_id, rules=None):
    rules = rules if rules is not None else load_rules()
    for r in rules:
        if r.get("rule_id") == rule_id:
            return r
    return None


def compute_points(rule, team_rank=None):
    """按规则计算最终学分（含集体项目排名调节系数，四舍五入保留 1 位小数）。"""
    rid = rule.get("rule_id")
    base = rule.get("points")
    out = {"rule_id": rid, "base_points": base, "team_rank": team_rank}
    if base is None:
        out.update({"final_points": None, "coefficient": None,
                    "note": "该规则无固定分值（需人工认定或为政策程序性条款），不能直接用于申报填值。"})
        return out
    coefs = rule.get("team_rank_coefficients")
    if team_rank is None:
        out.update({"final_points": base, "coefficient": None,
                    "note": "个人项目或未提供团队排名，按基础分值计。" if not coefs else
                            "该规则支持集体项目排名系数；如为团队获奖请提供 --team-rank。"})
        return out
    if not coefs:
        out.update({"final_points": None, "coefficient": None,
                    "error": f"规则 {rid} 未规定集体项目排名调节系数；团队计分需人工确认（待核验）。"})
        return out
    try:
        rank = int(team_rank)
    except (TypeError, ValueError):
        out.update({"final_points": None, "coefficient": None, "error": f"无法解析团队排名: {team_rank!r}"})
        return out
    key = str(rank) if str(rank) in coefs else ("5_and_below" if rank >= 5 else None)
    c = coefs.get(key)
    if c is None:
        out.update({"final_points": None, "coefficient": None,
                    "error": f"团队排名 {rank} 超出规则系数范围（1-4 或 5 名以后）。"})
        return out
    raw = Decimal(str(base)) * Decimal(str(c))
    final = float(raw.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
    out.update({"coefficient": c, "final_points": final,
                "calculation": f"{base} × {c} = {raw} → 四舍五入保留1位 = {final}",
                "note": rule.get("calculation_method")})
    return out


def check_evidence(rule, provided=None):
    """核对证明材料是否满足规则要求。provided 为已具备的证明项列表。"""
    required = rule.get("evidence_requirements") or []
    provided = provided or []
    missing = []
    for req in required:
        if not any(req in p or p in req for p in provided if p):
            missing.append(req)
    out = {"rule_id": rule.get("rule_id"), "required": required,
           "provided": provided, "missing": missing,
           "eligible": not missing}
    if not required:
        out["note"] = ("政策未对该条目明确规定证明材料；按平台申报要求提供活动证明/获奖证书等原件扫描件"
                       "（单张≤500KB，最多3张），并由学院认定时核对。")
    elif missing:
        out["note"] = "证据材料不足，不能判定合格；请先补齐证明材料再申报。"
    else:
        out["note"] = "证明材料齐全。"
    return out


def build_index(rules, metadata=None):
    metadata = metadata if metadata is not None else load_metadata()
    keywords, by_cat, names = {}, {}, {}
    for r in rules:
        rid = r["rule_id"]
        for k in r.get("keywords") or []:
            keywords.setdefault(k, []).append(rid)
        by_cat.setdefault(r.get("category") or "未分类", []).append(rid)
        names.setdefault(r.get("activity_name") or "", rid)
    policy_counts = {}
    for r in rules:
        policy_counts[r["policy_id"]] = policy_counts.get(r["policy_id"], 0) + 1
    return {
        "generated_from": "rules.jsonl",
        "rule_count": len(rules),
        "policies": [{"policy_id": p, "rule_count": c} for p, c in sorted(policy_counts.items())],
        "keywords": {k: sorted(v) for k, v in sorted(keywords.items())},
        "activity_names": names,
        "by_category": {k: sorted(v) for k, v in sorted(by_cat.items())},
    }


def build_source_map(rules, metadata=None):
    metadata = metadata if metadata is not None else load_metadata()
    sm = {}
    for p in metadata.get("policies", []):
        sm[p["policy_id"]] = {"source_file": p.get("source_file"), "rules": {}}
    for r in rules:
        pid = r.get("policy_id")
        sm.setdefault(pid, {"source_file": r.get("source_document"), "rules": {}})
        sm[pid]["rules"][r["rule_id"]] = {
            "page": r.get("source_page"),
            "clause": r.get("source_clause_or_table"),
            "excerpt": r.get("source_excerpt"),
        }
    return sm


def category_to_folder(category):
    """政策类别 → 截图归档文件夹（与 erkefen.py 的三类文件夹一致；劳动教育类无对应文件夹）。"""
    m = {"思想成长与实践公益教育类": "思政类",
         "思想政治课社会实践类": "思政类",
         "创新与创业实训实践类": "三创类",
         "体育运动与审美体验类": "体育类"}
    return m.get(category)


def platform_category(category, cohort=2025, metadata=None):
    """政策类别 → 平台申报下拉选项。

    2025 级：带【2025级】前缀（policy_metadata 2025_cohort）。
    2024 级及以前：无前缀四类（policy_metadata pre_2025_cohort，未实测标记待核验）。
    劳动教育课程类无截图文件夹对应，直传政策类别名。
    """
    metadata = metadata if metadata is not None else load_metadata()
    folder = category_to_folder(category)
    try:
        year = int(str(cohort).replace("级", ""))
    except (TypeError, ValueError):
        year = 2025
    if year >= 2025:
        for p in metadata.get("policies", []):
            if p.get("policy_id") == POLICY_2025:
                return p.get("platform_category_map", {}).get("2025_cohort", {}).get(folder)
        return None
    for p in metadata.get("policies", []):
        if p.get("policy_id") == POLICY_2022:
            m = p.get("platform_category_map", {}).get("pre_2025_cohort", {})
            return m.get(folder) or category
    return None
