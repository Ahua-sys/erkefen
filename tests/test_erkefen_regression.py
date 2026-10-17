# -*- coding: utf-8 -*-
"""erkefen.py 原有 PDF 检索/截图/分类功能回归测试。

用 pymupdf 现场生成带文字层和表格线的测试 PDF（虚构学生数据），
验证 search/preview/lines/shot 全链路与 500KB 上限、红框、类别归档不被破坏。
"""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
ERKEFEN = os.path.join(REPO, "scripts", "erkefen.py")


def make_test_pdf(path):
    """生成三页测试 PDF：第1页文字层表格（含类别页眉），第2页带绘制横线的表格，
    第3页带完整表头（标题+表头行+数据行，模拟每页有表头的扫描件）。

    中文用 pymupdf 内置 china-s 字体，保证文字层可检索。
    """
    import pymupdf
    doc = pymupdf.open()
    # 第 1 页：类别页眉 + 文字层表格
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 60), "经典百书阅读活动 第二课堂加分证明", fontsize=14, fontname="china-s")
    page.insert_text((72, 90), "给予体育运动与审美体验类(文体活动)加分", fontsize=11, fontname="china-s")
    page.insert_text((72, 130), "班级        姓名      学号        第二课堂加分", fontsize=10, fontname="china-s")
    rows = [("计算机2501", "张三", "2025001234", "0.2"),
            ("计算机2501", "李四", "2025001235", "0.2"),
            ("计算机2502", "王五", "2025001236", "0.2")]
    y = 160
    for r in rows:
        page.insert_text((72, y), "  ".join(r), fontsize=10, fontname="china-s")
        y += 24
    # 第 2 页：绘制横线的表格（模拟扫描件表格结构），无表头行
    page2 = doc.new_page(width=595, height=842)
    page2.insert_text((72, 60), "活动参与名单（第二页）", fontsize=12, fontname="china-s")
    top, x0, x1 = 100, 60, 535
    for i in range(7):
        yy = top + 26 * i
        page2.draw_line((x0, yy), (x1, yy), width=0.8)
    names = ["赵钱", "孙李", "周吴", "郑王", "冯陈", "楮卫"]
    for i, n in enumerate(names):
        page2.insert_text((72, top + 26 * i + 18), f"计算机2503  {n}  2025002{i:03d}  0.1",
                          fontsize=10, fontname="china-s")
    # 第 3 页：完整表头 + 数据行（模拟每页都有表头的集体卡）
    page3 = doc.new_page(width=595, height=842)
    page3.insert_text((72, 60), "测试运动会集体卡", fontsize=14, fontname="china-s")
    page3.insert_text((72, 90), "给予体育类加分 每次0.1学分", fontsize=11, fontname="china-s")
    page3.draw_line((60, 120), (535, 120), width=0.8)
    page3.insert_text((70, 140), "学院    班级    姓名    学分", fontsize=10, fontname="china-s")
    page3.draw_line((60, 152), (535, 152), width=0.8)
    for i, n in enumerate(["测试甲", "测试乙", "测试丙", "测试丁"]):
        yy = 152 + 30 * i
        page3.insert_text((70, yy + 20), f"统计学院  统计251  {n}  0.1", fontsize=10, fontname="china-s")
        page3.draw_line((60, yy + 30), (535, yy + 30), width=0.8)
    doc.save(path)
    doc.close()


class ErkefenRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="ekf_test_")
        cls.pdf = os.path.join(cls.tmp, "测试活动加分证明.pdf")
        make_test_pdf(cls.pdf)
        cls.work = os.path.join(cls.tmp, "work")
        os.makedirs(cls.work, exist_ok=True)

    def run_ekf(self, *args):
        return subprocess.run([PY, ERKEFEN, *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=180)

    # 10a. 文字层检索命中
    def test_search_text_hit(self):
        p = self.run_ekf("search", self.pdf, "--name", "张三")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("page=1", p.stdout)
        self.assertNotIn("NO_TEXT_HIT", p.stdout)

    def test_search_text_miss(self):
        p = self.run_ekf("search", self.pdf, "--name", "不存在的人")
        self.assertEqual(p.returncode, 0)
        self.assertIn("NO_TEXT_HIT", p.stdout)

    # 10b. preview 渲染全部页
    def test_preview(self):
        tmp = os.path.join(self.tmp, "prev")
        p = self.run_ekf("preview", self.pdf, "--tmp", tmp)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(os.path.isfile(os.path.join(tmp, "p01.png")))
        self.assertTrue(os.path.isfile(os.path.join(tmp, "p02.png")))
        self.assertTrue(os.path.isfile(os.path.join(tmp, "p03.png")))

    # 10c. lines 检测第 2 页横线
    def test_lines(self):
        p = self.run_ekf("lines", self.pdf, "--page", "2", "--fx1", "0.1", "--fx2", "0.9")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("(", p.stdout)

    # 10d. shot：文字层命中用 --box，输出同名 JPG ≤500KB 且含红框
    def test_shot_with_box(self):
        p = self.run_ekf("search", self.pdf, "--name", "张三")
        rect = [float(v) for v in p.stdout.split("rect=(")[1].split(")")[0].split(",")]
        k = 200 / 72
        box = f"{int(60 * k)},{int((rect[1] - 6) * k)},{int(535 * k)},{int((rect[3] + 6) * k)}"
        out = self.run_ekf("shot", self.pdf, "--page", "1", "--category", "体育类",
                           "--outdir", self.work, "--box", box)
        self.assertEqual(out.returncode, 0, out.stderr)
        jpg = os.path.join(self.work, "体育类", "测试活动加分证明.jpg")
        self.assertTrue(os.path.isfile(jpg))
        self.assertLessEqual(os.path.getsize(jpg), 500 * 1024)
        from PIL import Image
        import numpy as np
        im = Image.open(jpg)
        self.assertEqual(im.format, "JPEG")
        self.assertEqual(im.mode, "RGB")
        # 红框存在（存在纯红像素）
        arr = np.array(im.convert("RGB"))
        self.assertTrue(((arr[:, :, 0] > 250) & (arr[:, :, 1] < 10) & (arr[:, :, 2] < 10)).any())

    # 10e. shot：扫描件路径用 --top-index 选行，归档到正确类别文件夹
    def test_shot_with_top_index_and_folders(self):
        out = self.run_ekf("shot", self.pdf, "--page", "2", "--category", "思政",
                           "--outdir", self.work, "--top-index", "3", "--fx1", "0.1", "--fx2", "0.9")
        self.assertEqual(out.returncode, 0, out.stderr)
        jpg = os.path.join(self.work, "思政类", "测试活动加分证明.jpg")
        self.assertTrue(os.path.isfile(jpg))
        self.assertLessEqual(os.path.getsize(jpg), 500 * 1024)
        # 第 2 页无表头 → 默认走页眉拼接
        self.assertIn("页眉拼接", out.stdout)

    # 10f. shot：目标页自带表头时 --no-stitch 整页直截（用户规范）
    def test_shot_no_stitch_full_page(self):
        out = self.run_ekf("shot", self.pdf, "--page", "3", "--category", "体育",
                           "--outdir", self.work, "--top-index", "2",
                           "--fx1", "0.1", "--fx2", "0.9", "--no-stitch")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("整页直截", out.stdout)
        jpg = os.path.join(self.work, "体育类", "测试活动加分证明.jpg")
        self.assertTrue(os.path.isfile(jpg))
        self.assertLessEqual(os.path.getsize(jpg), 500 * 1024)
        from PIL import Image
        import numpy as np
        im = Image.open(jpg)
        self.assertEqual(im.format, "JPEG")
        self.assertEqual(im.mode, "RGB")
        # 整页直截 = 原页高宽比（不拼接，宽度即为页宽）
        self.assertGreater(im.height, im.width)
        arr = np.array(im.convert("RGB"))
        self.assertTrue(((arr[:, :, 0] > 250) & (arr[:, :, 1] < 10) & (arr[:, :, 2] < 10)).any())

    def test_unknown_category_rejected(self):
        p = self.run_ekf("shot", self.pdf, "--page", "1", "--category", "不存在类",
                         "--outdir", self.work, "--box", "0,0,10,10")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("未知类别", p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
