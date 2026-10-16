# -*- coding: utf-8 -*-
"""二课分证明截图工具

子命令:
  search   文字层搜索姓名, 定位页码与坐标
  preview  将全部页面渲染为 100dpi 预览图(供目视查找)
  lines    检测某页横向表格线(供选行)
  shot     生成最终截图: 红框标出行 + 页眉(类别)拼接 + JPG<=500KB 存入类别文件夹

依赖: pymupdf, Pillow, numpy  (装入托管 venv, 勿装全局)
"""
import argparse
import os
import sys

import numpy as np
import pymupdf
from PIL import Image, ImageDraw

DPI = 200
CATEGORIES = {
    "三创类": "三创类", "三创": "三创类",
    "体育类": "体育类", "体育": "体育类", "体育与审美": "体育类",
    "思政类": "思政类", "思政": "思政类", "思想政治": "思政类",
}
MAX_BYTES = 500 * 1024


# ---------- 横线检测: 24窗口链式聚类, 容忍扫描件轻微倾斜 ----------
def detect_hlines(gray, fx1=0.10, fx2=0.92, nw=24, min_win=12, thr=190, ratio=0.55):
    h, w = gray.shape
    x1, x2 = int(w * fx1), int(w * fx2)
    win_w = max(1, (x2 - x1) // nw)
    pts = []
    for k in range(nw):
        wx1 = x1 + k * win_w
        dark = (gray[:, wx1:wx1 + win_w] < thr).mean(axis=1)
        ys = np.where(dark > ratio)[0]
        groups = []
        for y in ys:
            if groups and y - groups[-1][-1] <= 4:
                groups[-1].append(y)
            else:
                groups.append([y])
        pts.extend((int(np.mean(g)), k) for g in groups)
    pts.sort()
    chains = []
    for y, k in pts:
        for c in reversed(chains):
            if abs(y - c["ys"][-1]) <= 6 and k not in c["wins"]:
                c["ys"].append(y)
                c["wins"].add(k)
                break
        else:
            chains.append({"ys": [y], "wins": {k}})
    lines = [(min(c["ys"]), max(c["ys"])) for c in chains if len(c["wins"]) >= min_win]
    lines.sort()
    merged = []
    for ln in lines:
        if merged and ln[0] <= merged[-1][1] + 8:
            merged[-1] = (min(merged[-1][0], ln[0]), max(merged[-1][1], ln[1]))
        else:
            merged.append(list(ln))
    return [tuple(m) for m in merged]


def row_x_extent(gray, y1, y2, fx1, fx2, thr=190, ratio=0.45):
    h, w = gray.shape
    seg = gray[max(0, y1):min(h, y2), int(w * fx1):int(w * fx2)]
    dark = (seg < thr).mean(axis=0)
    xs = np.where(dark > ratio)[0]
    if len(xs) < 2:
        return int(w * fx1), int(w * fx2)
    return int(w * fx1) + int(xs[0]), int(w * fx1) + int(xs[-1])


def render_page(pdf, page_no):
    doc = pymupdf.open(pdf)
    pix = doc[page_no].get_pixmap(dpi=DPI)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    doc.close()
    return img


def save_jpg_capped(img, path):
    """自适应压缩到 <=500KB: 先降质量, 再降分辨率"""
    img = img.convert("RGB")
    for q in (88, 80, 70, 60, 50):
        img.save(path, "JPEG", quality=q)
        if os.path.getsize(path) <= MAX_BYTES:
            return os.path.getsize(path)
    cur = img
    for _ in range(6):
        cur = cur.resize((int(cur.width * 0.85), int(cur.height * 0.85)), Image.LANCZOS)
        for q in (80, 65, 50):
            cur.save(path, "JPEG", quality=q)
            if os.path.getsize(path) <= MAX_BYTES:
                return os.path.getsize(path)
    return os.path.getsize(path)


def cmd_search(a):
    doc = pymupdf.open(a.pdf)
    found = False
    for i, page in enumerate(doc):
        for r in page.search_for(a.name):
            print(f"page={i + 1} rect=({r.x0:.1f},{r.y0:.1f},{r.x1:.1f},{r.y1:.1f})")
            found = True
    if not found:
        print("NO_TEXT_HIT (扫描件或无文字层, 请用 preview 目视查找)")
    doc.close()


def cmd_preview(a):
    os.makedirs(a.tmp, exist_ok=True)
    doc = pymupdf.open(a.pdf)
    for i, page in enumerate(doc):
        page.get_pixmap(dpi=100).save(os.path.join(a.tmp, f"p{i + 1:02d}.png"))
    print(f"{len(doc)} pages -> {a.tmp}")
    doc.close()


def cmd_lines(a):
    img = render_page(a.pdf, a.page - 1)
    gray = np.array(img.convert("L"))
    for ln in detect_hlines(gray, a.fx1, a.fx2):
        print(ln)


def pick_row(lines, mode, value, img_h):
    pairs = list(zip(lines[:-1], lines[1:]))
    max_gap = min(300, img_h // 12)
    sane = [p for p in pairs if p[1][1] - p[0][0] < max_gap * 1.6] or pairs
    if mode == "near":
        return min(sane, key=lambda p: abs((p[0][1] + p[1][0]) / 2 - value))
    if mode == "top":
        return sane[value - 1]
    return sane[len(sane) - value]  # bottom: value 从 1 开始


def cmd_shot(a):
    cat = CATEGORIES.get(a.category)
    if not cat:
        sys.exit(f"未知类别: {a.category} (可选: 三创类/体育类/思政类)")
    outdir = os.path.join(a.outdir, cat)
    os.makedirs(outdir, exist_ok=True)

    target = render_page(a.pdf, a.page - 1)
    gray = np.array(target.convert("L"))
    h, w = gray.shape

    if a.box:
        x1, y1, x2, y2 = [int(v) for v in a.box.split(",")]
    else:
        lines = detect_hlines(gray, a.fx1, a.fx2)
        mode, value = ("near", a.near) if a.near is not None else \
            ("top", a.top_index) if a.top_index is not None else ("bottom", a.bottom_index)
        (ya1, _), (_, yb2) = pick_row(lines, mode, value, h)
        y1, y2 = ya1, yb2
        x1, x2 = row_x_extent(gray, y1, y2, a.fx1, a.fx2)

    d = ImageDraw.Draw(target)
    for i in range(6):
        d.rectangle([x1 - i, y1 - i, x2 + i, y2 + i], outline=(255, 0, 0))

    stem = os.path.splitext(os.path.basename(a.pdf))[0]
    # 类别文字总是在第 1 页页眉; 姓名行若也在第 1 页则整页单图, 否则拼接
    if a.page == 1 and not a.force_stitch:
        final = target
    else:
        header = render_page(a.pdf, a.header_page - 1)
        hc = header.crop((0, int(header.height * a.hy0), header.width, int(header.height * a.hy1)))
        row_ctx = target.crop((0, max(0, y1 - a.ctx), w, min(h, y2 + a.ctx)))
        # 统一宽度后纵向拼接
        W = max(hc.width, row_ctx.width)
        hc = hc.resize((W, int(hc.height * W / hc.width)), Image.LANCZOS)
        row_ctx = row_ctx.resize((W, int(row_ctx.height * W / row_ctx.width)), Image.LANCZOS)
        final = Image.new("RGB", (W, hc.height + row_ctx.height + 8), (255, 255, 255))
        final.paste(hc, (0, 0))
        final.paste(row_ctx, (0, hc.height + 8))

    out = os.path.join(outdir, stem + ".jpg")
    size = save_jpg_capped(final, out)
    verify = os.path.join(a.outdir, ".verify_" + cat + "_" + stem + ".png")
    final.crop((0, max(0, final.height // 2 - 400), final.width, min(final.height, final.height // 2 + 400))).save(verify)
    print(f"saved: {out} ({size // 1024}KB)  box=({x1},{y1},{x2},{y2})")
    print(f"verify: {verify}")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search")
    s.add_argument("pdf")
    s.add_argument("--name", required=True)
    s.set_defaults(f=cmd_search)

    s = sub.add_parser("preview")
    s.add_argument("pdf")
    s.add_argument("--tmp", default=".ekf_preview")
    s.set_defaults(f=cmd_preview)

    s = sub.add_parser("lines")
    s.add_argument("pdf")
    s.add_argument("--page", type=int, required=True)
    s.add_argument("--fx1", type=float, default=0.10)
    s.add_argument("--fx2", type=float, default=0.92)
    s.set_defaults(f=cmd_lines)

    s = sub.add_parser("shot")
    s.add_argument("pdf")
    s.add_argument("--page", type=int, required=True, help="姓名所在页(1-based)")
    s.add_argument("--category", required=True, help="三创类/体育类/思政类(或别名)")
    s.add_argument("--outdir", required=True, help="工作目录(三类文件夹将建在其中)")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--box", help="显式框坐标 x1,y1,x2,y2 (200dpi像素)")
    g.add_argument("--near", type=float, help="目标行中心y(200dpi像素), 就近选行")
    g.add_argument("--top-index", type=int, help="从上数第N个数据行")
    g.add_argument("--bottom-index", type=int, help="从下数第N个数据行")
    s.add_argument("--fx1", type=float, default=0.10)
    s.add_argument("--fx2", type=float, default=0.92)
    s.add_argument("--header-page", type=int, default=1)
    s.add_argument("--hy0", type=float, default=0.02, help="页眉裁剪起始(高度分数)")
    s.add_argument("--hy1", type=float, default=0.32, help="页眉裁剪结束(高度分数)")
    s.add_argument("--ctx", type=int, default=140, help="拼接时行上下文像素")
    s.add_argument("--force-stitch", action="store_true")
    s.set_defaults(f=cmd_shot)

    a = p.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
