# -*- coding: utf-8 -*-
"""declare.py 申报流程测试：字段映射、附件限制、--no-submit、--preview、规则核对。

不访问真实平台：用脚本化的假 bsk（状态机）驱动完整填表流程，
验证 --no-submit 停在提交前、正式提交才点「确定申请」。
"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import declare  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FakeBSK:
    """脚本化假 bsk：按页面状态返回 observe 文本，click/fill/upload 返回 ok。"""

    CAT_OPT = "【2025级】体育运动与审美体验类"

    def __init__(self):
        self.state = "home"
        self.clicked = []
        self.filled = {}
        self.name_filled = False

    # ---- observe 文本（含 @eN 引用行与无引用结构行两类） ----
    def observe_text(self):
        if self.state == "home":
            return ('@layers 1 focus=L1\nL1 page\nRootWebArea "第二课堂 首页"\n'
                    '@e7 button "我的"\n@e8 button "活动"\n')
        if self.state == "my_page":
            return ('@layers 1 focus=L1\nRootWebArea "我的"\n'
                    'paragraph "我的申报"\nparagraph "我的活动"\n')
        if self.state == "form":
            # 真实平台：填完项目名称后才弹出「是否集体项目」字段
            extra = ""
            if self.name_filled:
                extra = ('@e16 button "是否集体项目"\n@e17 button "获奖自然年"\n')
            return ('@layers 1 focus=L1\nRootWebArea "新建申报"\n'
                    '@e10 button "学年学期"\n@e11 button "项目分类"\n'
                    'LabelText "申报学分"\n@e12 textbox ""\n'
                    'LabelText "项目名称"\n@e13 textbox ""\n'
                    'LabelText "申报说明"\n@e14 textbox ""\n'
                    '@e15 button "上传图片"\n@e18 button "确定申请"\n' + extra)
        if self.state == "term_picker":
            return ('@layers 2 focus=L1\nL1 mask cover=100%\nL2 page\n'
                    '@e20 button "取消"\n@e21 button "确认"\n@e22 button "2025-2026 学年"\n')
        if self.state == "cat_list":
            return ('@layers 2 focus=L1\nL1 mask cover=100%\nL2 tabpanel\n'
                    f'@e30 button "{self.CAT_OPT}"\n@e31 button "【2025级】创新与创业实训实践类"\n'
                    '@e32 button "【2025级】思想成长与实践公益教育类"\n')
        if self.state == "collective_picker":
            return ('@layers 2 focus=L1\nL1 mask cover=100%\nL2 page\n'
                    '@e20 button "取消"\n@e21 button "确认"\n@e40 button "是"\n@e41 button "否"\n')
        if self.state == "year_picker":
            return ('@layers 2 focus=L1\nL1 mask cover=100%\nL2 page\n'
                    '@e20 button "取消"\n@e21 button "确认"\n'
                    '@e50 button "2024"\n@e51 button "2025"\n@e52 button "2026"\n')
        if self.state == "submitted":
            return ('@layers 1 focus=L1\nRootWebArea "我的申报"\n'
                    'paragraph "测试项目名称"\nparagraph "未审核"\n')
        return '@layers 1 focus=L1\nRootWebArea "未知"\n'

    def _label_of(self, ref):
        import re
        for line in self.observe_text().splitlines():
            m = re.match(r'\s*@(e\d+)\s+button\s+"(.*)"', line)
            if m and f"@{m.group(1)}" == ref:
                return m.group(2)
        return ""

    def bsk(self, *args):
        cmd = args[0] if args else ""
        if cmd == "observe":
            return 0, self.observe_text()
        if cmd == "click":
            ref = args[1]
            label = self._label_of(ref)
            self.clicked.append(label)
            if label == "我的":
                self.state = "my_page"
            elif label == "学年学期":
                self.state = "term_picker"
            elif label == "2025-2026 学年":
                self.state = "term_picker"  # picker 点选项不关窗
            elif label == "项目分类":
                self.state = "cat_list"
            elif label == self.CAT_OPT:
                self.state = "form"  # 列表弹窗点选项即关
            elif "集体项目" in label:
                self.state = "collective_picker"
            elif "获奖自然年" in label:
                self.state = "year_picker"
            elif label in ("是", "否", "2024", "2025", "2026"):
                pass  # picker 选项，弹窗仍开着
            elif label == "确认":
                self.state = "form"
            elif label == "上传图片":
                self.state = "form"
            elif label == "确定申请":
                self.state = "submitted"
            return 0, "click ok"
        if cmd == "fill":
            self.filled[args[1]] = args[3]
            if args[1] == "@e13":  # 项目名称填完 → 弹出集体项目字段
                self.name_filled = True
            return 0, "fill ok"
        if cmd == "upload":
            self.clicked.append("上传图片")  # 上传也是用户可见动作，记录便于断言
            return 0, "upload ok"
        if cmd == "navigate":
            self.state = "home"
            return 0, ""
        if cmd == "session" and len(args) > 1 and args[1] == "stop":
            return 0, ""
        return 0, ""

    def bsk_json(self, *args, **kwargs):
        cmd = args[0] if args else ""
        if cmd == "status":
            return {"browsers": ["edge"]}
        if cmd == "session":
            return {"session_id": "s1"}
        if cmd == "evaluate":
            expr = args[-1]
            # js_click_text 用 json.dumps 内嵌文本，中文会变成 \uXXXX 转义，两种形式都要匹配
            if "我的申报" in expr or json.dumps("我的申报")[1:-1] in expr:
                self.state = "my_page"
            if "申请申报" in expr or json.dumps("申请申报")[1:-1] in expr:
                self.state = "form"
            return {"ok": True, "value": True}
        return {}


class DeclareTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeBSK()
        self.tmp = tempfile.mkdtemp(prefix="declare_test_")
        # 100KB 测试截图
        from PIL import Image
        self.img = os.path.join(self.tmp, "test.jpg")
        Image.new("RGB", (800, 600), (255, 255, 255)).save(self.img, "JPEG", quality=90)
        # >500KB 测试截图：随机噪点（纯色 JPEG 压缩率极高，必须用噪点才能超限）
        import numpy as np
        rng = np.random.default_rng(42)
        noise = rng.integers(0, 256, (1500, 1500, 3), dtype=np.uint8)
        self.big = os.path.join(self.tmp, "big.jpg")
        Image.fromarray(noise, "RGB").save(self.big, "JPEG", quality=95)
        self.assertGreater(os.path.getsize(self.big), 500 * 1024,
                           "测试用大图未超过 500KB，换噪点参数")

    def run_main(self, *argv):
        buf = io.StringIO()
        with mock.patch.object(declare, "bsk", self.fake.bsk), \
             mock.patch.object(declare, "bsk_json", self.fake.bsk_json), \
             mock.patch.object(declare.time, "sleep", lambda *_: None), \
             redirect_stdout(buf):
            with mock.patch.object(sys, "argv", ["declare.py", *argv]):
                declare.main()
        return buf.getvalue()

    # 11a. 分类简写映射保持有效
    def test_category_shortcuts(self):
        self.assertEqual(declare.CATEGORY_SHORTCUTS["三创"], "【2025级】创新与创业实训实践类")
        self.assertEqual(declare.CATEGORY_SHORTCUTS["体育"], "【2025级】体育运动与审美体验类")
        self.assertEqual(declare.CATEGORY_SHORTCUTS["思政"], "【2025级】思想成长与实践公益教育类")

    # 11c. 平台地址可配置：--base-url 覆盖默认域名（非广州大学用户场景）
    def test_base_url_override(self):
        out = self.run_main("--image", self.img, "--project-name", "x",
                            "--rule-id", "TY-2025-001", "--year", "2025",
                            "--cohort", "2025", "--base-url", "http://other.school/sj",
                            "--preview")
        self.assertIn("http://other.school/sj", out)
        # 默认域名（不传 --base-url）仍可用
        out = self.run_main("--image", self.img, "--project-name", "x",
                            "--rule-id", "TY-2025-001", "--year", "2025",
                            "--cohort", "2025", "--preview")
        self.assertIn("dierketang.gzhu.edu.cn", out)

    # 11b. observe 解析（两类行都不能丢）与弹窗判断不被破坏
    def test_parse_refs_and_popup(self):
        obs = ('@layers 2 focus=L1\nL1 mask cover=100%\n'
               '@e7 button "确认"\nRootWebArea "新建申报"\n')
        refs = declare.parse_refs(obs)
        self.assertTrue(any(r["kind"] == "button" and r["ref"] == "@e7" for r in refs))
        self.assertTrue(any(r["kind"] == "RootWebArea" and r["ref"] is None for r in refs))
        self.assertTrue(declare.popup_open(obs, refs))
        obs2 = '@layers 1 focus=L1\nL1 page\n@e7 button "我的"\n'
        self.assertFalse(declare.popup_open(obs2, declare.parse_refs(obs2)))

    # 11c. 附件 >500KB 拒绝，且不驱动浏览器
    def test_oversize_image_rejected(self):
        with mock.patch.object(declare, "bsk", self.fake.bsk), \
             mock.patch.object(declare, "bsk_json", self.fake.bsk_json), \
             mock.patch.object(sys, "argv", ["declare.py", "--image", self.big,
                                             "--project-name", "x", "--credit", "0.1",
                                             "--category", "体育", "--year", "2025"]):
            with self.assertRaises(SystemExit):
                declare.main()
        self.assertEqual(self.fake.clicked, [])

    # 11d. --no-submit：全流程填写但停在提交前
    def test_no_submit_stops_before_confirm(self):
        out = self.run_main("--image", self.img, "--project-name", "测试项目名称",
                            "--credit", "0.1", "--category", "体育", "--year", "2025",
                            "--no-submit")
        self.assertIn("--no-submit", out)
        self.assertNotIn("确定申请", self.fake.clicked)
        self.assertNotEqual(self.fake.state, "submitted")
        # 关键字段都已填/选
        self.assertEqual(self.fake.filled.get("@e12"), "0.1")     # 申报学分
        self.assertEqual(self.fake.filled.get("@e13"), "测试项目名称")  # 项目名称
        self.assertIn(FakeBSK.CAT_OPT, self.fake.clicked)    # 项目分类选项
        self.assertIn("确认", self.fake.clicked)                  # picker 确认
        self.assertIn("上传图片", self.fake.clicked)              # 上传

    # 12. 正式提交：点确定申请并校验审核状态
    def test_submit_path(self):
        out = self.run_main("--image", self.img, "--project-name", "测试项目名称",
                            "--credit", "0.1", "--category", "体育", "--year", "2025")
        self.assertIn("确定申请", self.fake.clicked)
        self.assertEqual(self.fake.state, "submitted")
        self.assertIn("提交成功", out)

    # --rule-id：分值/类别与规则一致才放行
    def test_rule_id_consistent(self):
        out = self.run_main("--image", self.img, "--project-name", "经典百书阅读",
                            "--rule-id", "TY-2025-001", "--year", "2025", "--no-submit")
        self.assertEqual(self.fake.filled.get("@e12"), "0.2")  # 分值取自规则
        self.assertIn(FakeBSK.CAT_OPT, self.fake.clicked)  # 类别由规则推导

    def test_rule_id_mismatch_blocked(self):
        with mock.patch.object(sys, "argv", ["declare.py", "--image", self.img,
                                             "--project-name", "x", "--credit", "0.5",
                                             "--rule-id", "TY-2025-001", "--year", "2025",
                                             "--no-submit"]):
            with self.assertRaises(SystemExit):
                declare.main()
        self.assertEqual(self.fake.clicked, [])

    # --preview：不驱动浏览器
    def test_preview_no_browser(self):
        out = self.run_main("--image", self.img, "--project-name", "预览测试",
                            "--rule-id", "TY-2025-001", "--year", "2025", "--preview")
        self.assertIn("preview", out)
        self.assertIn("0.2", out)
        self.assertIn("附表 三、（一）", out)
        self.assertEqual(self.fake.clicked, [])

    # 阶段 F 汇总函数：类别与规则不一致时拒绝
    def test_resolve_submission_category_conflict(self):
        ns = mock.Mock(credit="0.2", category="三创", rule_id="TY-2025-001")
        with self.assertRaises(SystemExit):
            declare.resolve_submission(ns)


if __name__ == "__main__":
    unittest.main()
