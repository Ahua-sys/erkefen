#!/usr/bin/env python3
"""二课分「新建申报」自动化（依赖 bsk CLI + Edge 扩展，见 references/declare-workflow.md）。

把一张本地截图按既定路径提交为一条新建申报：
登录 → 首页「我的」→「我的申报」→「申请申报」→ 填表 → 上传截图 → 确定申请。

用法（dry-run 只填到上传前，不提交，用于验证）：
  python declare.py --image <截图.jpg> --project-name "2025年广州大学运动会" \
      --credit 0.1 --category "【2025级】体育运动与审美体验类" --year 2025 --collective no [--no-submit]

--category 也接受简写：三创 / 体育 / 思政（自动映射到 2025 级对应分类）。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

BASE = "http://dierketang.gzhu.edu.cn/second_class_sj"
BSK = os.path.expanduser("~/.local/bin/bsk.exe")

# 2025 级三类映射（用户为 2025 级；非 2025 级用分类下拉里的无前缀项）
CATEGORY_SHORTCUTS = {
    "三创": "【2025级】创新与创业实训实践类",
    "体育": "【2025级】体育运动与审美体验类",
    "思政": "【2025级】思想成长与实践公益教育类",
}

ENV = dict(os.environ, BSK_AUTO_START="0")


def bsk(*args, timeout=120):
    """跑一条 bsk 命令，返回 (exit_code, stdout+stderr)。"""
    r = subprocess.run([BSK, *args], capture_output=True, text=True,
                       timeout=timeout, env=ENV, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def bsk_json(*args, timeout=120):
    code, out = bsk(*args, timeout=timeout)
    m = re.search(r"\{.*\}", out, re.S)
    if not m:
        raise RuntimeError(f"bsk {' '.join(args)} 无 JSON 输出:\n{out[:500]}")
    return json.loads(m.group(0))


def js(sid, expr, timeout=60):
    """页面内执行 JS 表达式，返回其值。"""
    d = bsk_json("evaluate", "--session", sid, "--json", f"--timeout={timeout}", expr)
    if not d.get("ok"):
        raise RuntimeError(f"evaluate 失败: {d}")
    return d.get("value")


def js_click_text(sid, text):
    """点击页面中文本完全等于 text 的元素（靠事件冒泡触发 Vue 处理器）。"""
    return js(sid, (
        "(() => { const els = [...document.querySelectorAll('p,span,div,button,a')];"
        f" const t = els.find(e => e.children.length===0 && e.textContent.trim() === {json.dumps(text)});"
        " if (!t) return false; (t.closest('div')||t).click(); return true; })()"
    ))


def parse_refs(obs_text):
    """从 observe 输出解析 [{'ref','kind','label'}]。

    注意：交互元素带 @eN 引用（如 `@e7 button "我的"`），
    而结构节点（RootWebArea/sectionheader/paragraph/StaticText/img）没有引用，
    行格式为 `RootWebArea "我的"`。这两类都要解析——页面状态判断依赖 RootWebArea。
    ref 为 None 表示该节点无交互引用。
    """
    refs = []
    for line in obs_text.splitlines():
        m = re.match(r'\s*(?:@(e\d+)\s+)?(\w+)\s+"(.*)"', line)
        if m:
            refs.append({"ref": f"@{m.group(1)}" if m.group(1) else None,
                         "kind": m.group(2), "label": m.group(3)})
    return refs


def observe(sid, max_tokens=4000):
    _, out = bsk("observe", "--session", sid, f"--max-tokens={max_tokens}")
    return out


def find_button(refs, *keywords):
    for r in refs:
        if r["kind"] == "button" and all(k in r["label"] for k in keywords):
            return r
    return None


def labeled_textboxes(refs):
    """{'LabelText': textbox 节点}。

    observe 里 LabelText 与其后的 textbox 相邻；textbox 自带的 [ctx: ...] 标注
    不可靠（会指到相邻段落），一律以 LabelText 为准。
    """
    out = {}
    last = None
    for r in refs:
        if r["kind"] == "LabelText":
            last = r["label"]
        elif r["kind"] == "textbox" and last:
            out.setdefault(last, r)
    return out


def popup_options(refs):
    """弹窗里的可选项按钮（排除取消/确认；表单字段按钮带 * 前缀也不会混入）。"""
    return [r for r in refs if r["kind"] == "button"
            and r["label"] not in ("取消", "确认") and not r["label"].startswith("*")]


def popup_open(obs, refs):
    """模态弹窗是否打开：observe 出现第二层（mask 覆盖），且含 picker 的确认按钮
    或列表弹窗的 tabpanel/list。弹窗打开时表单层被标注为 occluded，不会枚举其节点。"""
    if not re.search(r"@layers\s+2\b", obs):
        return False
    return (bool(find_button(refs, "确认")) or "tabpanel" in obs
            or re.search(r'^\s*list(\s|"|$)', obs, re.M))


def wait_until(sid, predicate, tries=12, interval=2.0, desc="条件"):
    last = ""
    for i in range(tries):
        obs = observe(sid)
        last = obs
        if predicate(obs, parse_refs(obs)):
            return obs
        time.sleep(interval)
    raise RuntimeError(f"等待{desc}超时。最后一次 observe 输出：\n{last[:1500]}")


def with_fresh_ref(sid, finder, desc, run, ok_marker, attempts=3):
    """执行依赖 ref 的动作；ref 因页面重渲染漂移而失效时，自动重新 observe 重试。

    实测：picker 确认后表单异步更新字段值，紧接着的 observe 可能抓到旧树，
    下一个命令就报 "observation ref was not found" —— 重试即可。
    run(ref) 返回命令输出字符串；ok_marker 是成功标志（click ok / fill ok / upload ok）。
    """
    out = ""
    for attempt in range(attempts):
        refs = parse_refs(observe(sid))
        target = finder(refs)
        if not target:
            raise RuntimeError(f"找不到: {desc}")
        _, out = bsk(*run(target["ref"]))
        if ok_marker in out:
            return out
        if ("not found" in out or "unknown" in out) and attempt < attempts - 1:
            print(f"  {desc} 的 ref 失效，重试（{attempt + 2}/{attempts}）…")
            time.sleep(1.5)
            continue
        return out
    return out


def open_popup(sid, field_keywords, desc, attempts=3):
    """点击字段打开弹窗，带重试。

    实测偶发：表单刚渲染出来时点击字段无反应（Vue handler / 基础数据未就绪），
    此时重试即可。每次重试前重新 observe 取最新 ref。
    """
    field = None
    for attempt in range(attempts):
        if field is None:
            refs = parse_refs(observe(sid))
            field = find_button(refs, *field_keywords)
            if not field:
                raise RuntimeError(f"找不到字段: {desc}")
        code, out = bsk("click", field["ref"], "--session", sid)
        if "click ok" not in out:
            raise RuntimeError(f"点击字段「{desc}」失败: {out[:200]}")
        try:
            return wait_until(sid, lambda o, rf: popup_open(o, rf) and popup_options(rf),
                              tries=4, interval=1.0, desc=f"{desc} 弹窗")
        except RuntimeError:
            if attempt == attempts - 1:
                raise
            print(f"  「{desc}」弹窗未打开，重试（{attempt + 2}/{attempts}）…")
            time.sleep(1.5)
            field = None  # 下一轮重新 observe 取最新 ref
    raise RuntimeError(f"打不开「{desc}」弹窗")


def pick(sid, field_keywords, option_text, desc):
    """打开下拉 → 选中选项 →（picker 型弹窗）点确认。

    平台有两种弹窗（2026-09 实测）：
    - picker：学年学期 / 项目获奖自然年 / 是否为集体项目。有「取消/确认」按钮，
      点选项后弹窗不关，必须再点「确认」。
    - 列表弹窗：项目分类。无确认按钮，分类平铺成 list，点选项即选中并自动关闭。
    option_text 传 None 表示选第一个选项（学年学期默认唯一最新项）。

    注意：ref 只在当次 observe 快照内有效，弹窗每次开合后都必须重新 observe。
    """
    obs = open_popup(sid, field_keywords, desc)
    refs = parse_refs(obs)
    opts = popup_options(refs)
    if option_text is None:
        target = opts[0] if opts else None
    else:
        target = next((r for r in opts if r["label"].strip() == option_text), None)
    if not target:
        raise RuntimeError(f"{desc} 弹窗里没有选项 {option_text!r}，现有: {[r['label'] for r in opts]}")

    def find_option(refs):
        """在最新快照里重新定位目标选项（ref 可能漂移，按标签找）。"""
        opts = popup_options(refs)
        if option_text is None:
            return opts[0] if opts else None
        return next((r for r in opts if r["label"].strip() == option_text), None)

    out = with_fresh_ref(sid, find_option, f"{desc}选项「{target['label']}」",
                         lambda ref: ("click", ref, "--session", sid), "click ok")
    if "click ok" not in out:
        raise RuntimeError(f"点击选项「{target['label']}」失败: {out[:200]}")

    # picker 型：弹窗还开着就点确认；列表型此时已自动关闭
    time.sleep(0.8)
    obs2 = observe(sid)
    refs2 = parse_refs(obs2)
    if popup_open(obs2, refs2) and find_button(refs2, "确认"):
        out = with_fresh_ref(
            sid, lambda rf: find_button(rf, "确认"), f"{desc} 确认",
            lambda ref: ("click", ref, "--session", sid), "click ok")
        if "click ok" not in out:
            raise RuntimeError(f"点击「确认」失败: {desc}: {out[:200]}")
    # 等弹窗完全关闭、页面恢复可操作
    wait_until(sid, lambda o, rf: not popup_open(o, rf),
               tries=6, interval=1.0, desc=f"{desc} 弹窗关闭")
    time.sleep(0.5)


def ensure_logged_in(sid):
    """不在登录页就直接返回；在登录页则走统一身份认证并请求人工协助。"""
    # 先等页面渲染出 RootWebArea（ navigate 后 a11y 树可能还没就绪）
    def root_ready(obs, refs):
        return any(r["kind"] == "RootWebArea" for r in refs)

    wait_until(sid, root_ready, tries=10, interval=1.5, desc="页面渲染")
    obs = observe(sid)
    refs = parse_refs(obs)
    root = next((r for r in refs if r["kind"] == "RootWebArea"), None)
    if not (root and "登录" in root["label"]):
        return
    print("检测到登录页，点击「统一身份认证」…")
    if not js_click_text(sid, "统一身份认证"):
        raise RuntimeError("登录页上找不到「统一身份认证」按钮")
    time.sleep(5)
    print("已跳转统一身份认证，请在弹出的浏览器窗口里手动完成登录（学号+密码）…")
    bsk("request-help", "--session", sid,
        "--prompt", "请在统一身份认证页面完成登录（学号+密码，含验证码/扫码）。登录成功回到第二课堂后我会继续。",
        "--timeout", "600s", timeout=660)
    # 登录后回到首页
    bsk("navigate", f"{BASE}/home", "--session", sid)
    wait_until(sid, lambda obs, refs: any(
        r["kind"] == "RootWebArea" and "登录" not in r["label"] for r in refs),
        tries=15, interval=2.0, desc="登录完成")


def main():
    ap = argparse.ArgumentParser(description="二课分新建申报自动化")
    ap.add_argument("--image", required=True, help="要上传的截图路径（jpg/png，≤500KB）")
    ap.add_argument("--project-name", required=True, help="项目名称（写截图上的活动名）")
    ap.add_argument("--credit", required=True, help="申报学分，如 0.1")
    ap.add_argument("--category", required=True, help="项目分类：分类下拉里的原文，或简写 三创/体育/思政")
    ap.add_argument("--year", required=True, help="项目获奖自然年，如 2025")
    ap.add_argument("--collective", default="no", choices=["yes", "no"], help="是否集体项目，默认 no")
    ap.add_argument("--no-submit", action="store_true", help="只填到上传后，不点确定申请（验证用）")
    args = ap.parse_args()

    if not os.path.exists(BSK):
        sys.exit(f"找不到 bsk CLI: {BSK}（先装 BrowserSkill CLI）")
    if not os.path.isfile(args.image):
        sys.exit(f"截图不存在: {args.image}")
    size_kb = os.path.getsize(args.image) / 1024
    if size_kb > 500:
        sys.exit(f"截图 {size_kb:.0f}KB 超过 500KB 上限，先压缩")

    # 前置检查：daemon + 扩展
    try:
        st = bsk_json("status", "--json")
    except Exception as e:
        sys.exit(f"daemon 未响应: {e}\n先在宿主环境跑: bsk daemon start --foreground")
    if not st.get("browsers"):
        sys.exit("没有浏览器连接：请先手动打开 Edge 并确认 BrowserSkill 扩展已连接")

    category = CATEGORY_SHORTCUTS.get(args.category, args.category)

    sid = bsk_json("session", "start", "--json")["session_id"]
    print(f"session={sid}")
    try:
        # 1. 首页（带登录兜底）
        bsk("navigate", f"{BASE}/home", "--session", sid)
        ensure_logged_in(sid)

        # 2. 我的 → 我的申报 → 申请申报
        # 底部 tab 必须真实点击（JS click 不触发路由）；且要等首页活动列表加载完才渲染成按钮
        def tab_ready(obs, refs):
            return any(r["kind"] == "button" and r["label"] == "我的" for r in refs)

        obs = wait_until(sid, tab_ready, tries=15, interval=2.0, desc="底部 tab 渲染")
        refs = parse_refs(obs)
        my_tab = [r for r in refs if r["kind"] == "button" and r["label"] == "我的"]
        code, out = bsk("click", my_tab[-1]["ref"], "--session", sid)
        if "click ok" not in out:
            raise RuntimeError(f"点击「我的」tab 失败: {out[:300]}")
        wait_until(sid, lambda o, rf: any(
            r["kind"] == "RootWebArea" and "我的" in r["label"] for r in rf),
            tries=8, interval=1.5, desc="「我的」页")
        time.sleep(2)  # 等磁贴渲染
        if not js_click_text(sid, "我的申报"):
            raise RuntimeError("「我的」页找不到「我的申报」入口")
        time.sleep(2.5)
        if not js_click_text(sid, "申请申报"):
            raise RuntimeError("「我的申报」页找不到「申请申报」")
        wait_until(sid, lambda obs, refs: find_button(refs, "学年学期"),
                   tries=8, interval=1.0, desc="新建申报表单")
        time.sleep(1.5)  # 等表单基础数据/handler 就绪再点字段

        # 3. 学年学期（默认唯一最新项）→ 项目分类
        pick(sid, ["学年学期"], None, "学年学期")  # None = 弹窗第一个选项（当前唯一，即最新）
        refs = parse_refs(observe(sid))
        term_btn = find_button(refs, "学年学期")
        print(f"学年学期: {term_btn['label'] if term_btn else '已选'}")

        pick(sid, ["项目分类"], category, "项目分类")
        print(f"项目分类: {category}")

        # 4. 申报学分 + 项目名称（注意：填完名称才会弹出「是否为集体项目」字段）
        refs = parse_refs(observe(sid))
        fields = labeled_textboxes(refs)
        credit_box, name_box = fields.get("申报学分"), fields.get("项目名称")
        if not credit_box or not name_box:
            raise RuntimeError(f"找不到 申报学分/项目名称 输入框，现有 LabelText: {list(fields)}")

        def find_box(label):
            return lambda refs: labeled_textboxes(refs).get(label)

        out = with_fresh_ref(sid, find_box("申报学分"), "申报学分",
                             lambda ref: ("fill", ref, "--value", args.credit, "--session", sid),
                             "fill ok")
        if "fill ok" not in out:
            raise RuntimeError(f"填写申报学分失败: {out[:200]}")
        out = with_fresh_ref(sid, find_box("项目名称"), "项目名称",
                             lambda ref: ("fill", ref, "--value", args.project_name, "--session", sid),
                             "fill ok")
        if "fill ok" not in out:
            raise RuntimeError(f"填写项目名称失败: {out[:200]}")
        time.sleep(1.5)
        print(f"申报学分: {args.credit} | 项目名称: {args.project_name}")

        # 5. 是否集体项目 + 获奖自然年
        pick(sid, ["集体项目"], {"yes": "是", "no": "否"}[args.collective], "是否集体项目")
        pick(sid, ["获奖自然年"], args.year, "项目获奖自然年")
        print(f"集体项目: {'是' if args.collective == 'yes' else '否'} | 获奖自然年: {args.year}")

        # 6. 上传附件
        out = with_fresh_ref(sid, lambda refs: find_button(refs, "上传图片"), "上传图片",
                             lambda ref: ("upload", ref, "--file", args.image, "--session", sid),
                             "upload ok")
        if "upload ok" not in out:
            sys.exit(f"上传失败:\n{out[:600]}\n（检查 Edge 扩展是否已开「允许访问文件网址」）")
        time.sleep(1.5)
        print(f"附件: {os.path.basename(args.image)} ({size_kb:.0f}KB) 已上传")

        # 7. 确定申请（实测点击后直接跳回「我的申报」列表；此处仅兜底二次确认框）
        if args.no_submit:
            print("--no-submit：已填到上传后，未提交。")
        else:
            out = with_fresh_ref(sid, lambda refs: find_button(refs, "确定申请"), "确定申请",
                                 lambda ref: ("click", ref, "--session", sid), "click ok")
            if "click ok" not in out:
                raise RuntimeError(f"点击「确定申请」失败: {out[:200]}")
            time.sleep(2.5)
            obs = observe(sid)
            refs = parse_refs(obs)
            if popup_open(obs, refs):
                for kw in ("确认", "确定"):
                    dlg = find_button(refs, kw)
                    if dlg and "申请" not in dlg["label"]:
                        bsk("click", dlg["ref"], "--session", sid)
                        time.sleep(2.5)
                        break
            obs = wait_until(sid, lambda o, rf: "我的申报" in o and args.project_name in o,
                             tries=10, interval=1.5, desc="提交结果")
            ok = "未审核" in obs or "审核" in obs
            print(("提交成功" if ok else "已提交但未确认审核状态") +
                  f"：{args.project_name} / {category} / {args.credit} 分")
    finally:
        try:
            bsk("session", "stop", sid)
        except Exception:
            pass


if __name__ == "__main__":
    main()
