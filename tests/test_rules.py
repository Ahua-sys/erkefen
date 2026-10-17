# -*- coding: utf-8 -*-
"""规则库测试：检索、认定、计算、证据、政策版本、校验。

运行：python -m unittest discover -s tests -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import rules_lib as L  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable


class RuleSearchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = L.load_rules()
        cls.meta = L.load_metadata()

    def search(self, q, **kw):
        return L.search_rules(q, rules=self.rules, metadata=self.meta, **kw)

    # 1. 能按活动名称查到正确规则
    def test_search_by_activity_name(self):
        r = self.search("党团课", cohort=2025)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["candidates"][0]["rule_id"], "ZZ-2025-001")
        self.assertEqual(r["candidates"][0]["points"], 0.2)
        r = self.search("无偿献血", cohort=2025)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["candidates"][0]["rule_id"], "ZZ-2025-028")

    # 2. 同名/近似活动不会无依据混淆
    def test_similar_activities_not_confused(self):
        # 观摩（观众）≠ 文体比赛获奖
        r = self.search("观摩 观众", cohort=2025)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["candidates"][0]["rule_id"], "TY-2025-032")
        self.assertEqual(r["candidates"][0]["participation_or_award_type"], "audience")
        # 思想政治教育活动：线下每次 0.1 ≠ 线上每两次 0.1
        r = self.search("线上思想政治教育学习", cohort=2025)
        self.assertEqual(r["candidates"][0]["rule_id"], "ZZ-2025-003")
        # 经典百书归体育运动与审美体验类，不混入三创
        r = self.search("经典百书", cohort=2025)
        for c in r["candidates"]:
            self.assertEqual(c["category"], "体育运动与审美体验类")

    # 3. 参加比赛和获得奖项的规则能够区分
    def test_participation_vs_award(self):
        part = self.search("挑战杯 参赛", cohort=2025, ptype="参赛")
        award = self.search("挑战杯 获奖 一等奖", cohort=2025, ptype="获奖")
        part_ids = {c["rule_id"] for c in part["candidates"]}
        award_ids = {c["rule_id"] for c in award["candidates"]}
        self.assertTrue(part_ids & {f"SC-2025-{i:03d}" for i in range(10, 15)})
        self.assertTrue(award_ids & {f"SC-2025-{i:03d}" for i in range(15, 42)})
        self.assertFalse(part_ids & award_ids)
        # 同一级别：参赛 0.4 ≠ 获一等奖 4
        p = L.get_rule("SC-2025-011", self.rules)   # 国家级参赛
        a = L.get_rule("SC-2025-019", self.rules)   # 国家级一等奖
        self.assertEqual((p["points"], a["points"]), (0.4, 4))
        self.assertEqual(p["participation_or_award_type"], "participation")
        self.assertEqual(a["participation_or_award_type"], "award")

    # 4. 不同获奖等级、名次、团队排名或个人身份能正确匹配
    def test_award_levels_and_team_rank(self):
        self.assertEqual(L.get_rule("SC-2025-020", self.rules)["points"], 3)      # 国家级二等奖
        self.assertEqual(L.get_rule("SC-2025-038", self.rules)["points"], 0.8)    # 院级一等奖
        self.assertEqual(L.get_rule("SC-2025-016", self.rules)["award_level_or_rank"], "等级奖")  # 国际级
        # 团队排名系数
        r = L.get_rule("SC-2025-020", self.rules)
        self.assertEqual(L.compute_points(r, team_rank=1)["final_points"], 2.4)
        self.assertEqual(L.compute_points(r, team_rank=2)["final_points"], 2.1)
        self.assertEqual(L.compute_points(r, team_rank=4)["final_points"], 1.5)
        self.assertEqual(L.compute_points(r, team_rank=5)["final_points"], 1.2)
        self.assertEqual(L.compute_points(r, team_rank=9)["final_points"], 1.2)
        # 四舍五入（非银行家舍入）：1.5 × 0.7 = 1.05 → 1.1
        r = L.get_rule("SC-2025-034", self.rules)   # 校级一等奖 1.5
        self.assertEqual(L.compute_points(r, team_rank=2)["final_points"], 1.1)
        # 个人身份：论文只规定第一作者
        r = L.get_rule("SC-2025-042", self.rules)
        self.assertEqual(r["award_level_or_rank"], "第一作者")
        self.assertIn("其他排名未规定", r["notes"])
        # 表4（文体比赛）没有团队系数 → 团队计分必须报错而不是瞎算
        r = L.get_rule("TY-2025-007", self.rules)
        self.assertIsNone(r["team_rank_coefficients"])
        self.assertIn("error", L.compute_points(r, team_rank=2))

    # 5. 计算系数和分值上限不会丢失
    def test_caps_and_coefficients_preserved(self):
        self.assertEqual(L.get_rule("SC-2025-011", self.rules)["cap"]["max_credits"], 0.5)   # 参赛累计
        self.assertEqual(L.get_rule("SC-2025-020", self.rules)["cap"]["max_credits"], 6)     # 同一项目获奖累计
        self.assertEqual(L.get_rule("SC-2025-059", self.rules)["cap"]["max_credits"], 1.2)   # 学术讲座
        self.assertEqual(L.get_rule("TY-2025-032", self.rules)["cap"]["max_credits"], 0.5)   # 观摩
        self.assertEqual(L.get_rule("TY-2025-001", self.rules)["cap"]["max_credits"], 1)     # 经典百书
        self.assertTrue(L.get_rule("SC-2025-020", self.rules)["duplicate_credit_rules"])
        self.assertTrue(L.get_rule("TY-2025-041", self.rules)["team_rank_coefficients"])    # 新闻报道集体按排名
        # 破纪录是获奖之外的另计
        r = L.get_rule("TY-2025-027", self.rules)
        self.assertEqual((r["points"], r["competition_level"]), (2, "国家级"))
        self.assertIn("除比赛获奖学分外", r["calculation_method"])

    # 6. 证据材料不足时会提示，而不是直接判定合格
    def test_evidence_insufficient_prompts(self):
        r = L.get_rule("SC-2025-042", self.rules)   # 国际权威刊物论文
        chk = L.check_evidence(r, [])
        self.assertFalse(chk["eligible"])
        self.assertTrue(chk["missing"])
        self.assertIn("不能判定合格", chk["note"])
        chk = L.check_evidence(r, ["收录通知书"])
        self.assertTrue(chk["eligible"])
        # 寒暑假社会实践要三样材料
        r = L.get_rule("ZZ-2025-018", self.rules)
        chk = L.check_evidence(r, ["个人实践小结"])
        self.assertFalse(chk["eligible"])
        self.assertEqual(len(chk["missing"]), 2)
        # 政策未规定证据的条目：给默认提示而非判定合格
        r = L.get_rule("ZZ-2025-001", self.rules)
        chk = L.check_evidence(r, [])
        self.assertTrue(chk["eligible"])
        self.assertIn("政策未对该条目明确规定证明材料", chk["note"])

    # 7. 查不到规则时明确返回未找到/待核验
    def test_not_found(self):
        r = self.search("量子波动速读比赛", cohort=2025)
        self.assertEqual(r["status"], "not_found")
        self.assertIn("待核验", r["message"])

    # 8. 不同入学年级匹配正确政策版本
    def test_cohort_policy_matching(self):
        r = self.search("党团课", cohort=2025)
        self.assertEqual(r["policy"]["policy_id"], L.POLICY_2025)
        r = self.search("党团课", cohort=2027)
        self.assertEqual(r["policy"]["policy_id"], L.POLICY_2025)
        # 2024 级及以前（经统学院）→ 学院旧细则，不再是 policy_not_available
        for c in (2024, 2023, 2021):
            r = self.search("党团课", cohort=c)
            self.assertEqual(r["policy"]["policy_id"], L.POLICY_2022, f"{c}级应命中学院旧细则")
            self.assertEqual(r["status"], "ok")
            self.assertEqual(r["candidates"][0]["rule_id"], "ZZ-2022-001")
            self.assertEqual(r["candidates"][0]["points"], 0.3)
        pol, gate = L.policy_for_cohort(self.meta, "2025级")
        self.assertEqual(pol["policy_id"], L.POLICY_2025)
        self.assertIsNone(gate)
        pol, gate = L.policy_for_cohort(self.meta, "2024级")
        self.assertEqual(pol["policy_id"], L.POLICY_2022)
        self.assertIsNone(gate)

    # 多候选时要求补充信息，不擅自认定
    def test_multiple_candidates_requires_clarification(self):
        r = self.search("挑战杯 获奖 一等奖", cohort=2025)
        self.assertEqual(r["status"], "multiple_candidates")
        self.assertIn("competition_level", r["needs_clarification"])
        self.assertGreaterEqual(len(r["candidates"]), 2)


class RuleValidationTest(unittest.TestCase):
    def test_real_rules_pass(self):
        p = subprocess.run([PY, os.path.join(REPO, "scripts", "validate_rules.py"), "--json"],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(p.returncode, 0, p.stdout[-2000:])
        rep = json.loads(p.stdout)
        self.assertTrue(rep["ok"])
        # 两套政策：2025版 152 + 学院旧细则 200
        self.assertEqual(rep["rule_count"], 352)
        self.assertEqual(rep["by_policy"].get("GZU-2KC-2025-81"), 152)
        self.assertEqual(rep["by_policy"].get("GZU-JXT-2KC-2022"), 200)
        self.assertEqual(rep["coverage_covered"], rep["coverage_total"])

    # 9. 规则库缺少必要字段或出现重复 ID 时能够发现
    def test_validation_detects_broken_rules(self):
        bad = [
            {"rule_id": "X-1", "policy_id": "GZU-2KC-2025-81", "rule_type": "activity_rule",
             "category": "思想成长与实践公益教育类", "activity_name": "缺字段规则",
             "points": 0.1},  # 缺少 source_page/source_excerpt 等
            {"rule_id": "X-1", "policy_id": "GZU-2KC-2025-81", "rule_type": "activity_rule",
             "category": "思想成长与实践公益教育类", "activity_name": "重复ID规则",
             "points": 0.1},  # 重复 rule_id
            {"rule_id": "X-2", "policy_id": "GZU-2KC-2025-81", "rule_type": "activity_rule",
             "category": "思想成长与实践公益教育类", "activity_name": "无分值规则",
             "points": None},  # activity_rule 无分值
            {"rule_id": "X-3", "policy_id": "UNKNOWN", "rule_type": "activity_rule",
             "category": "思想成长与实践公益教育类", "activity_name": "未知政策",
             "points": 0.1},
        ]
        for f in ("applicable_cohorts", "subcategory", "keywords", "eligibility_conditions",
                  "participation_or_award_type", "points_qualifier", "calculation_method",
                  "individual_or_team", "evidence_requirements", "source_document",
                  "source_page", "source_clause_or_table", "source_excerpt",
                  "verification_status", "competition_level", "award_level_or_rank",
                  "coefficient", "team_rank_coefficients", "cap", "duplicate_credit_rules",
                  "exceptions", "notes"):
            for r in bad:
                r.setdefault(f, None)
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "bad.jsonl")
            with open(path, "w", encoding="utf-8") as f:
                for r in bad:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            p = subprocess.run([PY, os.path.join(REPO, "scripts", "validate_rules.py"),
                                "--rules", path, "--json"],
                               capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(p.returncode, 1)
            errs = json.loads(p.stdout)["errors"]
            joined = "\n".join(errs)
            self.assertIn("rule_id 重复", joined)
            self.assertIn("activity_rule 的 points", joined)
            self.assertIn("不在 policy_metadata.json 中", joined)

    def test_index_matches_rules(self):
        rules = L.load_rules()
        with open(os.path.join(REPO, "rules", "index.json"), encoding="utf-8") as f:
            idx = json.load(f)
        self.assertEqual(idx["rule_count"], len(rules))
        idx_ids = set()
        for ids in idx["keywords"].values():
            idx_ids.update(ids)
        self.assertEqual(idx_ids, {r["rule_id"] for r in rules})
        rebuilt = L.build_index(rules, L.load_metadata())
        self.assertEqual(rebuilt["rule_count"], idx["rule_count"])


class CategoryMappingTest(unittest.TestCase):
    def test_policy_to_folder_and_platform(self):
        self.assertEqual(L.category_to_folder("思想成长与实践公益教育类"), "思政类")
        self.assertEqual(L.category_to_folder("创新与创业实训实践类"), "三创类")
        self.assertEqual(L.category_to_folder("体育运动与审美体验类"), "体育类")
        self.assertEqual(L.platform_category("体育运动与审美体验类"),
                         "【2025级】体育运动与审美体验类")
        self.assertEqual(L.platform_category("创新与创业实训实践类"),
                         "【2025级】创新与创业实训实践类")
        self.assertEqual(L.platform_category("思想成长与实践公益教育类"),
                         "【2025级】思想成长与实践公益教育类")

    def test_cli_search_json(self):
        p = subprocess.run([PY, os.path.join(REPO, "scripts", "search_rules.py"),
                            "search", "--query", "青马工程", "--cohort", "2025"],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(p.returncode, 0, p.stderr)
        out = json.loads(p.stdout)
        self.assertEqual(out["status"], "multiple_candidates")  # 5 个级别，需补充
        ids = {c["rule_id"] for c in out["candidates"]}
        self.assertTrue({f"ZZ-2025-{i:03d}" for i in range(4, 9)} <= ids)


if __name__ == "__main__":
    unittest.main()
