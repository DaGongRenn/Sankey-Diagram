# -*- coding: utf-8 -*-
"""
sankey.py —— 桑基图核心:确定性布局 + 关键帧插值 + Pillow 逐帧绘制(科技风)。

三列:左(净流出,绿) → 中(主力资金 hub) → 右(净流入,红)。
闭合规则:主力净流入/流出不守恒,加灰色平衡节点让左右总额相等,桑基永远闭合。

稳定性(确定性)三件套:
  1) 固定显示集:用整段最后一帧取 流入 TopN / 流出 TopN,全程不再换板块;
  2) 固定顺序:按最后一帧 |金额| 降序定上下次序,全程不变;
  3) 双侧平衡槽:左右各预留一个灰槽,每帧只有一侧非零,几何稳定、不抖。

同输入必同输出(无随机)。金额单位:亿元。
"""
from __future__ import annotations
import logging
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

import config
from snapshots import progress_to_clock

log = logging.getLogger("sankey")

L = config.LAYOUT
C = config.COLORS


# ====================================================================
# 字体(取列表里第一个能加载的;带缓存)
# ====================================================================
_font_cache: dict = {}


def _load_font(paths, size):
    key = (tuple(paths), size)
    if key in _font_cache:
        return _font_cache[key]
    for p in paths:
        try:
            f = ImageFont.truetype(p, size)
            _font_cache[key] = f
            return f
        except Exception:
            continue
    f = ImageFont.load_default()
    _font_cache[key] = f
    return f


def font_cjk(size):
    return _load_font(config.FONT_CJK_PATHS, size)


def font_tech(size):
    return _load_font(config.FONT_TECH_PATHS, size)


# ====================================================================
# 插值:相邻关键帧之间 ease-in-out
# ====================================================================
def ease_in_out(t: float) -> float:
    """三次 ease-in-out:两端慢、中间快,带宽/数值过渡顺滑。"""
    return 4 * t * t * t if t < 0.5 else 1 - ((-2 * t + 2) ** 3) / 2


def interp_values(keyframes: list[tuple], p: float, names: list[str]) -> dict[str, float]:
    """在进度 p∈[0,1] 处插值出各板块当前累计主力净流入。

    keyframes: [(progress, boards, clock), ...],progress 严格递增。
    缺失板块兜底取 0(整段快照缺失=关键帧缺失,由相邻关键帧跨段插值容忍)。
    """
    if p <= keyframes[0][0]:
        b = keyframes[0][1]
        return {n: float(b.get(n, 0.0)) for n in names}
    if p >= keyframes[-1][0]:
        b = keyframes[-1][1]
        return {n: float(b.get(n, 0.0)) for n in names}
    for i in range(len(keyframes) - 1):
        p0, b0, _ = keyframes[i]
        p1, b1, _ = keyframes[i + 1]
        if p0 <= p <= p1:
            f = 0.0 if p1 == p0 else (p - p0) / (p1 - p0)
            e = ease_in_out(f)
            return {n: float(b0.get(n, 0.0)) * (1 - e) + float(b1.get(n, 0.0)) * e
                    for n in names}
    b = keyframes[-1][1]
    return {n: float(b.get(n, 0.0)) for n in names}


# ====================================================================
# 固定显示集(整段最后一帧决定)
# ====================================================================
def _match_whitelist(boards: dict[str, float], keywords: list[str]) -> list[str]:
    """把用户的「概念关键词」映射到当前数据里的实际板块名(子串匹配,精确优先、取最短)。
    日志会打印每个关键词匹配到了谁、谁没匹配上,便于调整 WHITELIST。"""
    names = list(boards.keys())
    chosen, used = [], set()
    for kw in keywords:
        cands = [n for n in names if (n == kw or kw in n) and n not in used]
        if cands:
            best = min(cands, key=lambda n: (n != kw, len(n)))   # 精确优先,再取最短
            chosen.append(best)
            used.add(best)
            log.info("白名单 %s → %s", kw, best)
        else:
            log.warning("白名单未匹配(忽略): %s", kw)
    return chosen


def build_display_set(last_boards: dict[str, float], top_n: int):
    """确定全程固定显示集与顺序,返回 (inflow_names, outflow_names),从上到下固定。

    - WHITELIST 非空:只显示白名单匹配到的板块,按当前资金流「方向」分左右、|金额|排序;
    - WHITELIST 为空:自动取 流入 TopN(右)/ 流出 TopN(左)。
    """
    if config.WHITELIST:
        chosen = _match_whitelist(last_boards, config.WHITELIST)
        inflow = sorted([n for n in chosen if last_boards.get(n, 0.0) >= 0],
                        key=lambda n: last_boards[n], reverse=True)
        outflow = sorted([n for n in chosen if last_boards.get(n, 0.0) < 0],
                         key=lambda n: last_boards[n])           # 最负在上
        return inflow, outflow
    by_desc = sorted(last_boards.items(), key=lambda kv: kv[1], reverse=True)
    by_asc = sorted(last_boards.items(), key=lambda kv: kv[1])
    inflow = [n for n, v in by_desc if v > 0][:top_n]            # 最大流入在上
    outflow = [n for n, v in by_asc if v < 0][:top_n]            # 最大流出在上
    return inflow, outflow


# ====================================================================
# 颜色:按当前数值正负在红/绿之间平滑过渡(过零点不突变)
# ====================================================================
def value_color(v: float):
    eps = config.ZERO_EPS
    if v >= eps:
        t = 1.0
    elif v <= -eps:
        t = 0.0
    else:
        t = (v + eps) / (2 * eps)        # -eps..+eps → 0..1
    g, r = C["outflow"], C["inflow"]
    return tuple(int(round(g[i] * (1 - t) + r[i] * t)) for i in range(3))


# ====================================================================
# 单帧布局:把当前数值换算成像素几何(节点 + 缎带)
# ====================================================================
BAL_LEFT = "__bal_left__"      # 增量入场(灰,左)
BAL_RIGHT = "__bal_right__"    # 资金离场(灰,右)


def compute_layout(values: dict[str, float], inflow_names, outflow_names, scale: float) -> dict:
    """根据当前帧数值算几何。确定性:顺序固定,只有高度随数值变。

    - 节点高度 ∝ |当前数值| × 固定比例尺 scale,带 MIN_BAND_PX 下限;
      scale 全程不变(prepare_scene 里按整段峰值算一次),所以盘中累计净额
      增长时,带子会「逐渐变粗」,直观呈现资金往哪儿聚集;
    - 左右各加一个灰色平衡节点(只有一侧非零)使两侧总额相等 → 桑基闭合;
    - 左右缎带在 hub 两侧按相同顺序居中堆叠,hub 高度取两侧较大者。
    """
    stack_top, stack_bot = L["stack_top"], L["stack_bottom"]
    center = (stack_top + stack_bot) / 2.0
    gap = L["node_gap"]

    left_vals = [abs(values[n]) for n in outflow_names]
    right_vals = [abs(values[n]) for n in inflow_names]
    left_total, right_total = sum(left_vals), sum(right_vals)
    T = max(left_total, right_total, 1e-6)

    # 平衡节点:让两侧都等于 T
    bal_left = max(0.0, right_total - left_total)     # 增量入场(左)
    bal_right = max(0.0, left_total - right_total)    # 资金离场(右)

    left_nodes = list(outflow_names) + ([BAL_LEFT] if bal_left > 1e-9 else [])
    left_node_vals = left_vals + ([bal_left] if bal_left > 1e-9 else [])
    right_nodes = list(inflow_names) + ([BAL_RIGHT] if bal_right > 1e-9 else [])
    right_node_vals = right_vals + ([bal_right] if bal_right > 1e-9 else [])

    def stack(names, vals):
        heights = [max(v * scale, config.MIN_BAND_PX) for v in vals]
        total = sum(heights) + gap * max(0, len(heights) - 1)
        y = center - total / 2.0
        placed = []
        for nm, h in zip(names, heights):
            placed.append({"name": nm, "y0": y, "h": h})
            y += h + gap
        return placed, sum(heights)

    left_stack, left_h = stack(left_nodes, left_node_vals)
    right_stack, right_h = stack(right_nodes, right_node_vals)

    # hub 几何
    hub_h = max(left_h, right_h)
    hub_x0 = L["hub_x"] - L["hub_w"] / 2.0
    hub_x1 = L["hub_x"] + L["hub_w"] / 2.0

    # 缎带在 hub 两侧的堆叠起点(各自居中)
    left_hub_y = center - left_h / 2.0
    right_hub_y = center - right_h / 2.0

    def build_side(stack_list, x_center, is_left, hub_y_start):
        nodes, ribbons = [], []
        hy = hub_y_start
        node_inner = (x_center + L["node_w"] / 2.0) if is_left else (x_center - L["node_w"] / 2.0)
        hub_edge = hub_x0 if is_left else hub_x1
        for nd in stack_list:
            nm, y0, h = nd["name"], nd["y0"], nd["h"]
            is_bal = nm in (BAL_LEFT, BAL_RIGHT)
            if is_bal:
                col = C["balance_in"] if nm == BAL_LEFT else C["balance_out"]
                val = nd["h"] / scale if scale > 0 else 0.0  # 反推近似值用于标注
                disp = bal_left if nm == BAL_LEFT else bal_right
            else:
                # 颜色严格按"列"定:左列恒绿(净流出)、右列恒红(净流入),
                # 同一侧永不混色。板块归属由整段最后一帧定死,全程不变。
                col = C["outflow"] if is_left else C["inflow"]
                disp = values[nm]
            nodes.append({"name": nm, "x": x_center, "y0": y0, "h": h,
                          "color": col, "value": disp, "is_left": is_left,
                          "is_bal": is_bal})
            ribbons.append({"is_left": is_left, "color": col,
                            "x_node": node_inner, "y_node": y0, "h_node": h,
                            "x_hub": hub_edge, "y_hub": hy, "h_hub": h})
            hy += h
        return nodes, ribbons

    ln, lr = build_side(left_stack, L["left_x"], True, left_hub_y)
    rn, rr = build_side(right_stack, L["right_x"], False, right_hub_y)

    return {
        "nodes": ln + rn,
        "ribbons": lr + rr,
        "hub": {"x0": hub_x0, "x1": hub_x1, "y0": center - hub_h / 2.0, "h": hub_h},
        "total": T, "scale": scale,
        "bal_left": bal_left, "bal_right": bal_right,
    }


# ====================================================================
# 绘制基元
# ====================================================================
def _bezier(p0, p1, p2, p3, n=24):
    """三次贝塞尔采样成折线点。"""
    pts = []
    for i in range(n + 1):
        t = i / n
        mt = 1 - t
        x = mt**3 * p0[0] + 3 * mt * mt * t * p1[0] + 3 * mt * t * t * p2[0] + t**3 * p3[0]
        y = mt**3 * p0[1] + 3 * mt * mt * t * p1[1] + 3 * mt * t * t * p2[1] + t**3 * p3[1]
        pts.append((x, y))
    return pts


def _ribbon_polygon(rb):
    """一条缎带的填充多边形:上下两条贝塞尔边围成。"""
    xa, xb = rb["x_node"], rb["x_hub"]
    midx = (xa + xb) / 2.0
    ya_t, ya_b = rb["y_node"], rb["y_node"] + rb["h_node"]
    yb_t, yb_b = rb["y_hub"], rb["y_hub"] + rb["h_hub"]
    top = _bezier((xa, ya_t), (midx, ya_t), (midx, yb_t), (xb, yb_t))
    bot = _bezier((xa, ya_b), (midx, ya_b), (midx, yb_b), (xb, yb_b))
    return top + bot[::-1]


def _draw_gradient_ribbon(img, rb):
    """缎带:沿流向(水平)做「节点色 → 中枢色」线性渐变填充,
    使左右两侧与中枢的颜色平滑过渡、衔接处不割裂。
    做法:在缎带包围盒内生成横向渐变图 + 用多边形当蒙版贴上去。"""
    poly = _ribbon_polygon(rb)
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    x0, y0 = int(np.floor(min(xs))), int(np.floor(min(ys)))
    x1, y1 = int(np.ceil(max(xs))), int(np.ceil(max(ys)))
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return
    c_node = np.array(rb["color"], dtype=np.float32)
    c_hub = np.array(C["core"], dtype=np.float32)                # 汇入中枢柔光核心色
    t = np.linspace(0.0, 1.0, w, dtype=np.float32)[:, None]      # 0=左边缘, 1=右边缘
    # 左缎带:节点在左→中枢在右;右缎带:中枢在左→节点在右
    grad = (c_node * (1 - t) + c_hub * t) if rb["is_left"] else (c_hub * (1 - t) + c_node * t)
    grad = np.broadcast_to(grad[None, :, :], (h, w, 3))
    grad_img = Image.fromarray(np.ascontiguousarray(grad, dtype=np.uint8), "RGB")
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).polygon([(px - x0, py - y0) for px, py in poly],
                                 fill=L["ribbon_alpha"])
    img.paste(grad_img, (x0, y0), mask)


def _background():
    """近黑→深蓝竖向渐变 + 细网格,监控大屏质感。"""
    a = np.linspace(0, 1, config.H).reshape(config.H, 1, 1)
    top = np.array(C["bg_top"], dtype=np.float32).reshape(1, 1, 3)
    bot = np.array(C["bg_bottom"], dtype=np.float32).reshape(1, 1, 3)
    grad = top * (1 - a) + bot * a
    grad = np.repeat(grad, config.W, axis=1).astype(np.uint8)
    img = Image.fromarray(grad, "RGB")
    d = ImageDraw.Draw(img, "RGBA")
    grid = C["grid"]
    for gx in range(0, config.W + 1, 120):
        d.line([(gx, 0), (gx, config.H)], fill=(*grid, 26), width=1)
    for gy in range(0, config.H + 1, 120):
        d.line([(0, gy), (config.W, gy)], fill=(*grid, 26), width=1)
    return img


def _label_two_color(d, x, y, name, val_str, color, anchor_left):
    """节点旁标注:板块名(白) + 数值(随色)。自动缩字号避免溢出屏幕。"""
    margin = L.get("label_margin", 10)
    avail = (x - margin) if anchor_left else (config.W - margin - x)
    size = 30
    while size >= 20:
        fn, ft = font_cjk(size), font_tech(size)
        w = fn.getlength(name) + 8 + ft.getlength(val_str)
        if w <= avail:
            break
        size -= 2
    fn, ft = font_cjk(size), font_tech(size)
    white = C["text"]
    if anchor_left:   # 右对齐,从节点向左排:… 名 值|x
        d.text((x, y), val_str, font=ft, fill=color, anchor="rm")
        d.text((x - ft.getlength(val_str) - 8, y), name, font=fn, fill=white, anchor="rm")
    else:             # 左对齐:x|名 值 …
        d.text((x, y), name, font=fn, fill=white, anchor="lm")
        d.text((x + fn.getlength(name) + 8, y), val_str, font=ft, fill=color, anchor="lm")


# ====================================================================
# 场景准备 + 单帧绘制
# ====================================================================
def prepare_scene(keyframes, session, date_label, market_kf=None, market_prev_kf=None) -> dict:
    """整段只算一次:固定显示集 + 标题 + 固定比例尺 + 全市场氛围条序列。"""
    last_boards = keyframes[-1][1]
    inflow, outflow = build_display_set(last_boards, config.TOP_N)
    title = config.TITLE_TMPL.format(date=date_label, label=config.SESSION_LABEL[session])

    # 固定比例尺:扫描全程关键帧,取「两侧总额」的峰值做锚(留 5% 余量),
    # 使任何一帧都不溢出绘图区,同时盘中带子随累计净额增长而变粗。
    max_total = 1e-6
    for _, b, _ in keyframes:
        to = sum(abs(float(b.get(n, 0.0))) for n in outflow)
        ti = sum(abs(float(b.get(n, 0.0))) for n in inflow)
        max_total = max(max_total, to, ti)
    stack_h = L["stack_bottom"] - L["stack_top"]
    max_nodes = max(len(inflow), len(outflow)) + 1            # +1 预留平衡槽
    usable = stack_h - L["node_gap"] * max(0, max_nodes - 1)
    scale = usable / (max_total * 1.05)

    log.info("显示集 流入Top=%s 流出Top=%s 峰值总额=%.1f亿 scale=%.2f px/亿",
             inflow, outflow, max_total, scale)
    return {"keyframes": keyframes, "inflow": inflow, "outflow": outflow,
            "names": inflow + outflow, "session": session, "title": title,
            "scale": scale, "market_kf": market_kf or [], "market_prev_kf": market_prev_kf or []}


def draw_frame(scene: dict, frame_index: int) -> Image.Image:
    """渲染第 frame_index 帧(0..TOTAL_FRAMES-1)→ RGB 图。"""
    p = frame_index / max(1, config.TOTAL_FRAMES - 1)
    values = interp_values(scene["keyframes"], p, scene["names"])
    lay = compute_layout(values, scene["inflow"], scene["outflow"], scene["scale"])
    # 中枢用"柔光核心":左右缎带都延伸到中线相接(无硬竖条),交汇处靠辉光桥接
    for rb in lay["ribbons"]:
        rb["x_hub"] = L["hub_x"]

    img = _background()

    # ---- 辉光层:缩半绘制+模糊+加性叠加(柔和霓虹,且省一半算力)----
    sw, sh = config.W // 2, config.H // 2
    glow = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for rb in lay["ribbons"]:
        poly = [(x / 2, y / 2) for x, y in _ribbon_polygon(rb)]
        gd.polygon(poly, fill=(*rb["color"], 150))
    for nd in lay["nodes"]:
        x0 = (nd["x"] - L["node_w"] / 2) / 2
        x1 = (nd["x"] + L["node_w"] / 2) / 2
        gd.rectangle([x0, nd["y0"] / 2, x1, (nd["y0"] + nd["h"]) / 2], fill=(*nd["color"], 220))
    glow = glow.filter(ImageFilter.GaussianBlur(L["glow_blur"] / 2)).resize((config.W, config.H))
    base = np.asarray(img, dtype=np.float32)
    gl = np.asarray(glow, dtype=np.float32)
    alpha = gl[..., 3:4] / 255.0 * L["glow_gain"]
    base = np.clip(base + gl[..., :3] * alpha, 0, 255).astype(np.uint8)
    img = Image.fromarray(base, "RGB")

    # ---- 缎带:节点色→核心色 渐变填充,左右延伸到中线相接 ----
    for rb in lay["ribbons"]:
        _draw_gradient_ribbon(img, rb)

    # ---- 中枢柔光核心:中线一道经高斯模糊的冷白光柱(无硬条),桥接左右交汇 ----
    hub = lay["hub"]
    cw = L["hub_w"] / 2 + 2
    core = Image.new("RGBA", (config.W, config.H), (0, 0, 0, 0))
    ImageDraw.Draw(core).rectangle(
        [L["hub_x"] - cw, hub["y0"], L["hub_x"] + cw, hub["y0"] + hub["h"]],
        fill=(*C["core"], 175))
    core = core.filter(ImageFilter.GaussianBlur(18))
    base = np.asarray(img, dtype=np.float32)
    cc = np.asarray(core, dtype=np.float32)
    a = cc[..., 3:4] / 255.0 * 0.9                # 偏暗的发光强度
    img = Image.fromarray(np.clip(base + cc[..., :3] * a, 0, 255).astype(np.uint8), "RGB")

    d = ImageDraw.Draw(img, "RGBA")
    d.text((L["hub_x"], hub["y0"] - 30), config.HUB_LABEL, font=font_cjk(34),
           fill=C["title"], anchor="mm")
    # 数字用科技字体、单位「亿」用中文字体(Bahnschrift 无 CJK 字形会出豆腐块)
    tot_num = f"{lay['total']:.0f}"
    d.text((L["hub_x"] - 9, hub["y0"] + hub["h"] + 28), tot_num,
           font=font_tech(30), fill=C["text_dim"], anchor="rm")
    d.text((L["hub_x"] - 7, hub["y0"] + hub["h"] + 28), "亿",
           font=font_cjk(24), fill=C["text_dim"], anchor="lm")

    # ---- 节点条带 + 描边 + 标注 ----
    for nd in lay["nodes"]:
        x0, x1 = nd["x"] - L["node_w"] / 2, nd["x"] + L["node_w"] / 2
        d.rectangle([x0, nd["y0"], x1, nd["y0"] + nd["h"]],
                    fill=(*nd["color"], 255), outline=(255, 255, 255, 60), width=1)
        ycen = nd["y0"] + nd["h"] / 2
        if nd["is_bal"]:
            name = "增量入场" if nd["name"] == BAL_LEFT else "资金离场"
            val_str = f"{nd['value']:.1f}"
        else:
            name = nd["name"]
            val_str = f"{nd['value']:+.1f}"
        _label_two_color(d, (x0 - L["label_pad"]) if nd["is_left"] else (x1 + L["label_pad"]),
                         ycen, name, val_str, nd["color"], anchor_left=nd["is_left"])

    _draw_overlays(d, scene, p)
    return img


def _draw_overlays(d, scene, p):
    """标题 / 右上时间戳 / 底部说明 / 水印位。"""
    # 顶部标题
    d.text((config.W / 2, 120), scene["title"], font=font_cjk(60),
           fill=C["title"], anchor="mm")
    # 图例
    lx = config.W / 2 - 150
    d.ellipse([lx, 178, lx + 22, 200], fill=(*C["inflow"], 255))
    d.text((lx + 32, 189), "净流入", font=font_cjk(28), fill=C["text"], anchor="lm")
    d.ellipse([lx + 170, 178, lx + 192, 200], fill=(*C["outflow"], 255))
    d.text((lx + 204, 189), "净流出", font=font_cjk(28), fill=C["text"], anchor="lm")
    # 右上角随帧时间戳
    clock = progress_to_clock(p, scene["session"])
    d.text((config.W - 28, 70), clock, font=font_tech(46), fill=C["hub"], anchor="rm")
    d.text((config.W - 28, 110), "实时累计", font=font_cjk(22), fill=C["text_dim"], anchor="rm")
    # 水印位(预留)
    if config.WATERMARK:
        d.text((config.W / 2, config.H - 132), config.WATERMARK,
               font=font_cjk(30), fill=(*C["text_dim"], 120), anchor="mm")
    # 底部免责说明
    d.text((config.W / 2, config.H - 70), config.DISCLAIMER,
           font=font_cjk(24), fill=C["text_dim"], anchor="mm")
    # 顶部全市场氛围条(在图例下方、主图上方;采得稀,随帧插值)
    if config.SHOW_MARKET_BAR and scene.get("market_kf"):
        mk = _interp_market(scene["market_kf"], p)
        if mk:
            prev = _interp_market(scene.get("market_prev_kf") or [], p)
            has_delta = bool(prev) and mk[2] > 0 and prev[2] > 0
            delta = (mk[2] - prev[2]) if has_delta else 0.0
            _draw_market_bar(d, mk[0], mk[1], mk[2], delta, has_delta)


def _interp_market(kf, p):
    """全市场序列在进度 p 处插值 →(up, down, turnover);空则 None。"""
    if not kf:
        return None
    if p <= kf[0][0]:
        return kf[0][1], kf[0][2], kf[0][3]
    if p >= kf[-1][0]:
        return kf[-1][1], kf[-1][2], kf[-1][3]
    for i in range(len(kf) - 1):
        p0, u0, d0, t0 = kf[i]
        p1, u1, d1, t1 = kf[i + 1]
        if p0 <= p <= p1:
            e = ease_in_out(0.0 if p1 == p0 else (p - p0) / (p1 - p0))
            return (u0 * (1 - e) + u1 * e, d0 * (1 - e) + d1 * e, t0 * (1 - e) + t1 * e)
    return kf[-1][1], kf[-1][2], kf[-1][3]


def _draw_market_bar(d, up, down, turnover, delta, has_delta):
    """涨跌家数分段条 + 成交额 + 较昨量变。小巧克制,不喧宾夺主。"""
    M = config.MARKET_BAR
    up_i, dn_i = int(round(up)), int(round(down))
    red, green, dim = C["inflow"], C["outflow"], C["text_dim"]
    fc = font_cjk(M["font_count"])
    # 涨跌家数:跌(绿)左、涨(红)右
    d.text((M["x0"], M["y_counts"]), f"跌 {dn_i}", font=fc, fill=green, anchor="lm")
    d.text((M["x1"], M["y_counts"]), f"涨 {up_i}", font=fc, fill=red, anchor="rm")
    # 分段条:宽度 ∝ 家数,中间留一道斜缝
    x0, x1, by, bh = M["x0"], M["x1"], M["bar_y"], M["bar_h"]
    split = x0 + (x1 - x0) * dn_i / max(up_i + dn_i, 1)
    d.rounded_rectangle([x0, by, max(x0 + bh, split - 5), by + bh], radius=bh / 2, fill=(*green, 235))
    d.rounded_rectangle([min(x1 - bh, split + 5), by, x1, by + bh], radius=bh / 2, fill=(*red, 235))
    # 成交额 + 较昨量变(整体居中,字更小);成交额拿不到(=0)就整行省略,只留涨跌家数
    if turnover > 0:
        fj = font_cjk(M["font_turn"])
        y = M["y_turnover"]
        left = f"成交 {turnover:.0f}亿"
        if has_delta:
            tag = "放量" if delta >= 0 else "缩量"
            dcol = red if delta >= 0 else green
            dstr = f"{tag} {delta:+.0f}亿"
            sep = "   ·   较昨 "
            wl, ws, wd = fj.getlength(left), fj.getlength(sep), fj.getlength(dstr)
            x = config.W / 2 - (wl + ws + wd) / 2
            d.text((x, y), left, font=fj, fill=dim, anchor="lm"); x += wl
            d.text((x, y), sep, font=fj, fill=dim, anchor="lm"); x += ws
            d.text((x, y), dstr, font=fj, fill=dcol, anchor="lm")
        else:
            d.text((config.W / 2, y), left, font=fj, fill=dim, anchor="mm")
