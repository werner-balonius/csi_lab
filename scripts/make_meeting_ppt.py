#!/usr/bin/env python3
"""make_meeting_ppt.py — 生成组会汇报 PPT。

依赖 python-pptx。所有数字来自仓库内已提交的分析文档与脚本输出，
不在此处重新计算，避免两处数字不一致。

    ~/csienv/bin/python scripts/make_meeting_ppt.py [输出路径]
"""

from __future__ import annotations

import sys
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

# 配色：深蓝主色，橙色强调，灰色正文
INK = RGBColor(0x1F, 0x2D, 0x3D)
ACCENT = RGBColor(0xC0, 0x5A, 0x1E)
MUTED = RGBColor(0x5A, 0x6B, 0x7B)
GOOD = RGBColor(0x1E, 0x6B, 0x3A)
BAD = RGBColor(0xA8, 0x2A, 0x2A)
LINE = RGBColor(0xD4, 0xDA, 0xE0)
BAND = RGBColor(0xF2, 0xF5, 0xF7)

W, H = Inches(13.333), Inches(7.5)
MARGIN = Inches(0.7)
BODY_W = W - 2 * MARGIN

FONT = "Helvetica Neue"
CJK = "PingFang SC"


def _set(run, size, bold=False, color=INK, font=CJK):
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    run.font.name = font


def add_slide(prs):
    return prs.slides.add_slide(prs.slide_layouts[6])


def title(slide, text, sub=None):
    box = slide.shapes.add_textbox(MARGIN, Inches(0.45), BODY_W, Inches(0.9))
    tf = box.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    r = p.add_run()
    r.text = text
    _set(r, 30, True, INK)

    # 标题下的分隔线
    y = Inches(1.32) if not sub else Inches(1.62)
    ln = slide.shapes.add_shape(1, MARGIN, y, BODY_W, Pt(2))
    ln.fill.solid()
    ln.fill.fore_color.rgb = ACCENT
    ln.line.fill.background()
    ln.shadow.inherit = False

    if sub:
        sb = slide.shapes.add_textbox(MARGIN, Inches(1.18), BODY_W, Inches(0.4))
        sp = sb.text_frame.paragraphs[0]
        r = sp.add_run()
        r.text = sub
        _set(r, 14, False, MUTED)
    return y


def bullets(slide, items, top, size=17, gap=0.42, left=None, width=None):
    """items: [(缩进级别, 文本, 颜色或None, 是否加粗)]"""
    left = left or MARGIN
    width = width or BODY_W
    box = slide.shapes.add_textbox(left, top, width, H - top - Inches(0.5))
    tf = box.text_frame
    tf.word_wrap = True
    first = True
    for lvl, text, color, bold in items:
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        p.level = lvl
        p.space_after = Pt(gap * 72 * 0.28)
        r = p.add_run()
        r.text = ("• " if lvl == 0 else "– ") + text if text else ""
        _set(r, size - lvl * 1.5, bold, color or INK)
    return box


def table(slide, data, top, col_w, size=14, header=True, height=None):
    rows, cols = len(data), len(data[0])
    total = sum(col_w)
    left = MARGIN + (BODY_W - Inches(total)) / 2
    h = height or Inches(0.42 * rows)
    shape = slide.shapes.add_table(rows, cols, left, top, Inches(total), h)
    tbl = shape.table
    for i, w in enumerate(col_w):
        tbl.columns[i].width = Inches(w)
    for r, row in enumerate(data):
        for c, val in enumerate(row):
            cell = tbl.cell(r, c)
            cell.text = ""
            para = cell.text_frame.paragraphs[0]
            run = para.add_run()
            txt = val[0] if isinstance(val, tuple) else val
            col = val[1] if isinstance(val, tuple) else None
            run.text = str(txt)
            is_head = header and r == 0
            _set(run, size, is_head or bool(col), col or (INK if is_head else MUTED))
            para.alignment = PP_ALIGN.LEFT if c == 0 else PP_ALIGN.CENTER
            cell.fill.solid()
            cell.fill.fore_color.rgb = BAND if is_head else RGBColor(0xFF, 0xFF, 0xFF)
            cell.margin_top = Pt(4)
            cell.margin_bottom = Pt(4)
    return shape


def callout(slide, text, top, color=ACCENT, height=0.8):
    """强调框：左侧竖条 + 文字"""
    bar = slide.shapes.add_shape(1, MARGIN, top, Pt(4), Inches(height))
    bar.fill.solid()
    bar.fill.fore_color.rgb = color
    bar.line.fill.background()
    bar.shadow.inherit = False
    box = slide.shapes.add_textbox(
        MARGIN + Inches(0.18), top - Inches(0.04), BODY_W - Inches(0.2), Inches(height)
    )
    tf = box.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    r = p.add_run()
    r.text = text
    _set(r, 16, True, color)
    return box


def footer(slide, text):
    box = slide.shapes.add_textbox(MARGIN, H - Inches(0.52), BODY_W, Inches(0.32))
    p = box.text_frame.paragraphs[0]
    r = p.add_run()
    r.text = text
    _set(r, 11, False, MUTED)


def build(out: Path):
    prs = Presentation()
    prs.slide_width, prs.slide_height = W, H

    # ---------------------------------------------------------------- 封面
    s = add_slide(prs)
    band = s.shapes.add_shape(1, Inches(0), Inches(2.45), W, Inches(2.3))
    band.fill.solid()
    band.fill.fore_color.rgb = BAND
    band.line.fill.background()
    band.shadow.inherit = False

    box = s.shapes.add_textbox(MARGIN, Inches(2.75), BODY_W, Inches(1.0))
    p = box.text_frame.paragraphs[0]
    r = p.add_run()
    r.text = "Person-in-WiFi-3D　实验系统组进展"
    _set(r, 40, True, INK)

    box = s.shapes.add_textbox(MARGIN, Inches(3.65), BODY_W, Inches(0.6))
    p = box.text_frame.paragraphs[0]
    r = p.add_run()
    r.text = "采集管线验证 · 首批数据 · 一次自我否定"
    _set(r, 20, False, ACCENT)

    box = s.shapes.add_textbox(MARGIN, Inches(5.3), BODY_W, Inches(1.0))
    tf = box.text_frame
    for t in ["Group 1（实验系统组）", "2026-08-14　覆盖 08-06 至 08-13"]:
        p = tf.add_paragraph()
        r = p.add_run()
        r.text = t
        _set(r, 15, False, MUTED)

    # ------------------------------------------------------------ 一句话总结
    s = add_slide(prs)
    title(s, "一句话总结")
    bullets(s, [
        (0, "采集管线已量化验证，可靠性有据可查", GOOD, True),
        (0, "拿到第一批有效数据：15 条主样本，含完整质量报告", None, False),
        (0, "确认全身运动可检测（walk_link2，1.43–1.59×，三次一致）", GOOD, True),
        (0, "小动作的初步阳性结果，经自查发现设计混杂，判定不成立", BAD, True),
        (0, "已定出下一轮采集的设计修正方案", None, False),
    ], Inches(2.1), size=20, gap=0.62)
    footer(s, "上次组会：08-06 之前，仅汇报采集系统，未涉及信号层结论")

    # ------------------------------------------------------- 上次组会以来
    s = add_slide(prs)
    title(s, "上次组会以来")
    table(s, [
        ["", "上次组会", "现在"],
        ["采集系统", "能跑通", ("量化验证", GOOD)],
        ["实验数据", "无", ("15 条有效主样本", GOOD)],
        ["信号层结论", "未涉及", "1 条成立、1 条自行推翻"],
        ["可复现性", "脚本", "脚本 + 逐 trial 报告 + 哈希校验"],
    ], Inches(2.3), [3.0, 3.2, 5.2], size=17, height=Inches(2.9))

    # ------------------------------------------------------------ 采集系统
    s = add_slide(prs)
    title(s, "采集系统：从「能跑」到「经过验证」")
    table(s, [
        ["指标", "实测"],
        ["双端 packet 匹配率", ("98.99 – 99.48 %", GOOD)],
        ["深度帧率 / 序号缺口", ("30 fps / 缺口 0", GOOD)],
        ["CSI–相机配对中位偏差", "8.3 ms"],
        ["两接收端时钟漂移拟合", "−21.49 ppm，残差 p95 84 µs"],
        ["数据子载波", "234（242 减 8 个导频）"],
    ], Inches(2.0), [5.0, 6.4], size=16, height=Inches(2.7))
    callout(s, "另修复三个「不报错但数据无效」的问题 —— 最危险的一类", Inches(5.0))
    footer(s, "① 内核自动升级致驱动失配　② H.265 参数集被预热丢弃　③ 批量失败被吞、退出码仍为 0")

    # ------------------------------------------------------------ 首批数据
    s = add_slide(prs)
    title(s, "第一批有效数据（08-11）")
    bullets(s, [
        (0, "15 条主样本：empty / walk_link2 / arm_wave / leg_lift / sit_to_stand 各 3 条", None, True),
        (0, "另有 1 条座椅布局校准空场", None, False),
        (0, "每条均有：逐 trial 质量报告、RGB 时间线核对、SHA-256 校验", None, False),
        (0, "协议标注：前空场 → 动作 → 后空场", None, False),
    ], Inches(2.0), size=17)
    callout(s, "数据分层：代码与脱敏特征进 Git；原始 CSI、深度、含人像视频留本地",
            Inches(4.35), MUTED)
    footer(s, "深度单文件 660–790 MB，超 GitHub 100 MB 限制；彩色视频含可识别画面")

    # ---------------------------------------------------------- 成立的结论
    s = add_slide(prs)
    title(s, "成立的结论：全身运动可检测")
    box = s.shapes.add_textbox(MARGIN, Inches(2.1), BODY_W, Inches(1.2))
    p = box.text_frame.paragraphs[0]
    r = p.add_run()
    r.text = "walk_link2　trial 内自归一化　1.43 – 1.59×"
    _set(r, 30, True, GOOD)
    bullets(s, [
        (0, "人穿越 node2 → node3 链路，三次重复一致", None, False),
        (0, "基线取同一次采集内的前后空场", None, True),
        (1, "信号与基线来自同一时段，不受跨 trial 状态差异影响", None, False),
        (1, "这也是它不受后文混杂问题影响的原因", ACCENT, False),
    ], Inches(3.5), size=17)

    # -------------------------------------------------------- 自我否定过程
    s = add_slide(prs)
    title(s, "一次自我否定：小动作分析的四个步骤")
    table(s, [
        ["", "做法", "结果"],
        ["1", "外部空场作基线", ("2.45 – 2.90×　看似显著", MUTED)],
        ["2", "改用 trial 内基线", ("0.61 – 0.95×　效应消失", None)],
        ["3", "换更细的子载波级特征", ("p = 0.0028，交叉验证 85 %", MUTED)],
        ["4", "检查与采集顺序的相关性", ("发现混杂，结论不成立", BAD)],
    ], Inches(2.1), [0.7, 4.6, 6.1], size=16, height=Inches(2.5))
    callout(s, "第 4 步是关键：前三步都只在看组间差异", Inches(5.0))
    footer(s, "arm_wave / leg_lift / sit_to_stand，各 3 条")

    # ------------------------------------------------------------ 混杂详情
    s = add_slide(prs)
    title(s, "混杂：类别与采集时间共线")
    table(s, [
        ["时段", "类别"],
        ["15:30 – 16:12", ("empty × 3", GOOD)],
        ["15:44 – 16:25", ("walk_link2 × 5", GOOD)],
        ["16:32 – 17:03", ("arm_wave × 3　leg_lift × 3", BAD)],
        ["17:21 – 17:29", ("sit_to_stand × 3", BAD)],
    ], Inches(1.95), [3.4, 7.2], size=16, height=Inches(2.3))
    bullets(s, [
        (0, "全部静止类在前，全部小动作类在后", BAD, True),
        (0, "显著特征与采集时刻的相关系数　r = −0.758", BAD, True),
        (0, "「静止 vs 动作」与「早 vs 晚」在统计上无法区分", None, False),
    ], Inches(4.5), size=17)
    footer(s, "唯一可作判据的 empty_chair（静止类、采于 17:15）：5 个特征中 3 个偏动作侧、2 个偏静止侧，仅 1 条，无法判定")

    # ------------------------------------------------------------ 方法学
    s = add_slide(prs)
    title(s, "方法学教训")
    callout(s, "trial 内基线能消除跨 trial 的状态差异，"
               "但消除不了随时间的系统性趋势", Inches(2.2), ACCENT, 1.0)
    bullets(s, [
        (0, "因为比值本身仍在漂移", None, False),
        (0, "判断特征是否反映目标效应，除组间差异外，还须检查它与"
            "采集顺序、时间、环境变量的相关性", None, True),
        (0, "漏掉这一步，就会得到 p < 0.01 但站不住的结论", BAD, True),
    ], Inches(3.7), size=18, gap=0.55)
    footer(s, "该问题在对外发表任何结论之前由组内自查发现")

    # ------------------------------------------------------------ 设计修正
    s = add_slide(prs)
    title(s, "下一轮采集的设计修正")
    table(s, [
        ["问题", "修正"],
        ["类别按时间分块", ("交错采集：empty → arm_wave → empty → leg_lift → …", GOOD)],
        ["基线离动作太远", "每类动作前后各插一条同布局空场"],
        ["布局与类别共线", "所有类别在同一布局下各采一遍"],
        ["环境变量未记录", "记录温度、时刻、门窗状态、人员位置"],
        ["样本量不足", ("3 vs 3 的置换检验下限即 p = 0.10 → 每类 ≥ 8 条", BAD)],
    ], Inches(2.0), [3.4, 7.6], size=15, height=Inches(3.0))

    # ------------------------------------------------------------ 讨论 1
    s = add_slide(prs)
    title(s, "需要讨论 ①　RGB-D 目前不是 3D 姿态真值")
    bullets(s, [
        (0, "现在产出的是同步的参考测量：", None, True),
        (1, "深度图 + 相机内参 + 帧时间戳 + CSI 包到深度帧的映射", None, False),
        (0, "要成为姿态标签，还需要：", None, True),
        (1, "选定并验证姿态估计模型", None, False),
        (1, "输出关节坐标与置信度、定义关节顺序与缺失策略", None, False),
        (1, "定义坐标系与相机外参、做精度校验", None, False),
    ], Inches(2.0), size=17)
    callout(s, "请导师明确：这一步属不属于 Group 1 的范围？", Inches(5.35))
    footer(s, "若属于 → 需确定模型选型；若不属于 → 交付接口与边界文档给下游")

    # ------------------------------------------------------------ 讨论 2
    s = add_slide(prs)
    title(s, "需要讨论 ②　三档间距实验是否按原计划推进")
    bullets(s, [
        (0, "原计划：5 / 10 / 15 m 三档 × 三场景 × 3 次 = 27 组", None, False),
        (0, "但目前存在三个障碍：", None, True),
        (1, "Link1 至今无响应（08-07 三次测得 −0.54 % / −0.39 %）", BAD, False),
        (1, "小动作可检测性未确立", BAD, False),
        (1, "每换一档需重新布局，而布局本身是最大变量", BAD, False),
        (2, "同一动作：新布局 +3.06 %，旧布局 +0.22 %", None, False),
    ], Inches(2.0), size=17)
    callout(s, "建议：先在 5 m 把方法学做扎实，再考虑扩展多档", Inches(5.5), GOOD)

    # ------------------------------------------------------------ 交付进度
    s = add_slide(prs)
    title(s, "交付进度对照 Group 1 职责")
    table(s, [
        ["职责项", "状态"],
        ["搭建 Wi-Fi sensing 测试床", ("完成", GOOD)],
        ["可靠采集 CSI", ("完成", GOOD)],
        ["采集深度参考数据", ("完成", GOOD)],
        ["多接收端对齐 + CSI/深度同步", ("完成", GOOD)],
        ["可复现的采集 / 记录 / 解析流程", ("完成", GOOD)],
        ["3D 骨骼标签", ("未开始 — 见讨论 ①", BAD)],
        ["场地外参标定", ("未开始", BAD)],
    ], Inches(1.95), [6.4, 4.6], size=16, height=Inches(3.5))

    # ------------------------------------------------------------ 结尾
    s = add_slide(prs)
    title(s, "小结")
    bullets(s, [
        (0, "采集管线可靠，有量化证据支撑", GOOD, True),
        (0, "全身运动的检测已确认", GOOD, True),
        (0, "小动作结论经自查推翻 —— 在对外发表之前发现", ACCENT, True),
        (0, "下一轮采集的设计已修正，等待两个决策后启动", None, True),
    ], Inches(2.3), size=21, gap=0.7)
    footer(s, "全部数字可由 scripts/verify_pilot_baseline.py 独立复算（仅依赖标准库）")

    prs.save(str(out))
    return len(prs.slides.__iter__.__self__._sldIdLst)


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else
                  Path.home() / "Desktop" / "组会汇报_20260814.pptx")
    target.parent.mkdir(parents=True, exist_ok=True)
    n = build(target)
    print(f"已生成 {target}（{n} 页）")
