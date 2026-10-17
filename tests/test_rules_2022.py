# -*- coding: utf-8 -*-
"""学院旧细则（GZU-JXT-2KC-2022，2024级及以前经统学院）规则测试。

覆盖：年级门禁、毕业9学分4类、与2025版的差异点（党课0.3/大创分级/观众上限/
参赛分/集体折算两方式/劳动教育类）、compute 团队系数、declare 的 cohort 交叉核对。
"""
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import rules_lib as L  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class OldPolicyRuleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = L.load_rules()
        cls.meta = L.load_metadata()
        cls.old = [r for r in cls.rules if r["policy_id"] == L.POLICY_2022]

    def search(self, query, **kw):
        return L.search_rules(query, rules=self.rules, metadata=self.meta, **kw)

    # 1. 年级门禁：2024级及以前命中学院细则且无 gate
    def test_cohort_gate(self):
        for c in (2024, 2023, 2022, 2021):
            pol, gate = L.policy_for_cohort(self.meta, c)
            self.assertEqual(pol["policy_id"], L.POLICY_2022, f"{c}级应适用学院旧细则")
            self.assertIsNone(gate)
        pol, gate = L.policy_for_cohort(self.meta, 2025)
        self.assertEqual(pol["policy_id"], L.POLICY_2025)
        self.assertIsNone(gate)

    # 2. 毕业总要求 9 学分 / 四类各 2（与 2025 版 6 学分 3 类不同）
    def test_graduation_requirement(self):
        r = L.get_rule("PL-2022-001", rules=self.rules)
        self.assertEqual(r["points"], 9)
        pol = L.get_policy(self.meta, L.POLICY_2022)
        gr = pol["graduation_requirement"]
        self.assertEqual(gr["total_credits_min"], 9)
        self.assertEqual(len(gr["per_category_min"]), 4)
        self.assertEqual(set(gr["per_category_min"].values()), {2})
        self.assertIn("劳动教育课程类", gr["per_category_min"])
        self.assertIn("经典百书诵读", gr["category_notes"]["体育运动与审美体验类"])

    # 3. 与 2025 版的差异点：党课 0.3（2025 版 0.2）
    def test_party_lecture_differs_from_2025(self):
        r = self.search("党课", cohort=2024)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["candidates"][0]["points"], 0.3)
        r = self.search("党课", cohort=2025)
        self.assertEqual(r["candidates"][0]["points"], 0.2)

    # 4. 大创分级：负责人国家级4/省级3/校级1/院级0.5，多候选要求澄清
    def test_chuangtian_project_levels(self):
        r = self.search("大创 立项 结题", cohort=2024)
        self.assertEqual(r["status"], "multiple_candidates")
        pts = {c["rule_id"]: c["points"] for c in r["candidates"]}
        self.assertEqual(pts.get("SC-2022-051"), 4)
        self.assertEqual(pts.get("SC-2022-052"), 3)
        self.assertEqual(pts.get("SC-2022-053"), 1)
        self.assertEqual(pts.get("SC-2022-054"), 0.5)

    # 5. 观众观摩：0.1/次，每学年上限 2 分（表5备注1 原文口径）
    def test_audience_cap(self):
        r = L.get_rule("TY-2022-037", rules=self.rules)
        self.assertEqual(r["points"], 0.1)
        self.assertEqual(r["cap"]["max_credits"], 2)
        self.assertEqual(r["cap"]["scope"], "观众类每学年")
        self.assertTrue(r["evidence_requirements"])

    # 6. 参赛分：学科 0.15 / 文体 0.2（不按级别，与 2025 版不同）
    def test_participation_points(self):
        r = L.get_rule("PL-2022-010", rules=self.rules)
        self.assertIn("0.15", r["calculation_method"])
        self.assertIn("0.2", r["calculation_method"])

    # 7. 集体项目两套折算方式都有记录
    def test_collective_two_methods(self):
        r = L.get_rule("PL-2022-007", rules=self.rules)
        self.assertEqual(r["team_rank_coefficients"],
                         {"1": 0.8, "2": 0.7, "3": 0.6, "4": 0.5, "5_and_below": 0.4})
        self.assertIn("无成员排名", r["notes"])
        self.assertIn("特等奖0.8", r["notes"].replace(" ", ""))

    # 8. 劳动教育类存在且构成 0.5+1.5
    def test_labor_education_category(self):
        ld = [r for r in self.old if r["category"] == "劳动教育课程类"]
        self.assertGreaterEqual(len(ld), 6)
        r = L.get_rule("LD-2022-001", rules=self.rules)
        self.assertEqual(r["points"], 0.5)
        r = L.get_rule("LD-2022-006", rules=self.rules)
        self.assertEqual(r["points"], 0.2)

    # 9. compute：2022 规则团队排名系数（2×0.7=1.4 类）
    def test_compute_team_rank(self):
        r = L.get_rule("TY-2022-006", rules=self.rules)  # 国际级一等奖 6 分
        out = L.compute_points(r, team_rank=2)
        self.assertEqual(out["coefficient"], 0.7)
        self.assertEqual(out["final_points"], 4.2)  # 6×0.7=4.2
        out = L.compute_points(r, team_rank=7)
        self.assertEqual(out["coefficient"], 0.4)
        self.assertEqual(out["final_points"], 2.4)  # 6×0.4=2.4

    # 10. 类别别名按政策分流：2024级"思政"→思想政治课社会实践类
    def test_category_alias_per_policy(self):
        self.assertEqual(L.resolve_category("思政", L.POLICY_2022), "思想政治课社会实践类")
        self.assertEqual(L.resolve_category("思政", L.POLICY_2025), "思想成长与实践公益教育类")
        self.assertEqual(L.resolve_category("劳动教育", L.POLICY_2022), "劳动教育课程类")
        self.assertIsNone(L.resolve_category("劳动教育", L.POLICY_2025))

    # 11. 平台分类：2024级无前缀 / 2025级带前缀
    def test_platform_category_per_cohort(self):
        self.assertEqual(L.platform_category("体育运动与审美体验类", cohort=2024),
                         "体育运动与审美体验类")
        self.assertEqual(L.platform_category("体育运动与审美体验类", cohort=2025),
                         "【2025级】体育运动与审美体验类")
        self.assertEqual(L.platform_category("思想政治课社会实践类", cohort=2024),
                         "思想政治课社会实践类")
        self.assertEqual(L.platform_category("劳动教育课程类", cohort=2024),
                         "劳动教育课程类")

    # 12. not_found 消息按政策动态化（提到学院细则而非81号）
    def test_not_found_message_policy_aware(self):
        r = self.search("量子波动速读", cohort=2024)
        self.assertEqual(r["status"], "not_found")
        self.assertIn("经统学院", r["message"])
        self.assertIn("PL-2022-013", r["message"])

    # 13. 全部 2022 规则可追溯原文
    def test_all_old_rules_traceable(self):
        for r in self.old:
            self.assertTrue(r.get("source_page"), r["rule_id"])
            self.assertTrue((r.get("source_excerpt") or "").strip(), r["rule_id"])
            self.assertEqual(r["verification_status"], "已核验", r["rule_id"])

    # 14. declare --cohort 交叉核对：跨政策套用被拒绝（子进程实测 exit=1）
    def test_declare_cohort_mismatch_rejected(self):
        py = sys.executable
        dec = os.path.join(REPO, "scripts", "declare.py")
        img = os.path.join(REPO, "tests", "_cohort_test.jpg")
        if not os.path.exists(img):
            from PIL import Image
            Image.new("RGB", (800, 600), (255, 255, 255)).save(img, "JPEG", quality=90)
        # 2024 级 + 2025 规则 → 拒绝
        p = subprocess.run([py, dec, "--image", img, "--project-name", "t",
                            "--rule-id", "ZZ-2025-001", "--year", "2024",
                            "--cohort", "2024", "--preview"],
                           capture_output=True, text=True, encoding="utf-8", timeout=120)
        self.assertEqual(p.returncode, 1)
        self.assertIn("政策不匹配", p.stdout + p.stderr)
        # 2025 级 + 2022 规则 → 拒绝
        p = subprocess.run([py, dec, "--image", img, "--project-name", "t",
                            "--rule-id", "ZZ-2022-001", "--year", "2025",
                            "--cohort", "2025", "--preview"],
                           capture_output=True, text=True, encoding="utf-8", timeout=120)
        self.assertEqual(p.returncode, 1)
        self.assertIn("政策不匹配", p.stdout + p.stderr)
        # 2024 级 + 2022 规则 → 正常 preview
        p = subprocess.run([py, dec, "--image", img, "--project-name", "t",
                            "--rule-id", "ZZ-2022-001", "--year", "2024",
                            "--cohort", "2024", "--preview"],
                           capture_output=True, text=True, encoding="utf-8", timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("思想政治课社会实践类", p.stdout)
        self.assertIn('"cohort": "2024"', p.stdout)
        os.remove(img)


if __name__ == "__main__":
    unittest.main()
