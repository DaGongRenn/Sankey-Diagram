# -*- coding: utf-8 -*-
"""
render.py —— 把逐帧图像管道喂给 ffmpeg,合成 9:16 竖屏 H.264 mp4。

ffmpeg 定位顺序:环境变量 FFMPEG_BIN → 系统 PATH → 复用同仓库
node_modules/ffmpeg-static/ffmpeg(.exe) → imageio-ffmpeg 自带二进制。
"""
from __future__ import annotations
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

import config
import sankey

log = logging.getLogger("render")


def _ffmpeg_works(path: str) -> bool:
    """实际跑一下 `-version`,过滤掉失败下载留下的桩文件(node ffmpeg-static 在国内常是桩)。"""
    try:
        r = subprocess.run([path, "-version"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=12)
        return r.returncode == 0
    except Exception:
        return False


def find_ffmpeg() -> str:
    """按优先级收集候选,逐个验证可用性,返回第一个真正能跑的。"""
    cands = []
    if os.environ.get("FFMPEG_BIN"):
        cands.append(os.environ["FFMPEG_BIN"])
    if shutil.which("ffmpeg"):
        cands.append(shutil.which("ffmpeg"))
    exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    cands.append(str(config.BASE_DIR.parent / "node_modules" / "ffmpeg-static" / exe))
    try:
        import imageio_ffmpeg
        cands.append(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        pass
    for c in cands:
        if c and Path(c).exists() and _ffmpeg_works(c):
            return c
    raise RuntimeError("找不到可用 ffmpeg:请装系统 ffmpeg 或 `pip install imageio-ffmpeg`")


def frames_to_mp4(scene: dict, out_path: Path) -> Path:
    """逐帧渲染并写入 mp4。RGB 原始帧通过管道喂 ffmpeg(不经 PNG 编码,更快)。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = find_ffmpeg()
    cmd = [
        ffmpeg, "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{config.W}x{config.H}", "-r", str(config.FPS), "-i", "-",
        "-an",
        "-c:v", "libx264", "-preset", "medium", "-pix_fmt", config.PIX_FMT,
        "-crf", str(config.CRF),
        "-maxrate", config.VIDEO_BITRATE, "-bufsize", "16M",
        "-movflags", "+faststart",
        str(out_path),
    ]
    log.info("ffmpeg=%s → %s", ffmpeg, out_path.name)
    t0 = time.time()
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for i in range(config.TOTAL_FRAMES):
            img = sankey.draw_frame(scene, i)
            proc.stdin.write(img.tobytes())
            if i % 60 == 0:
                log.info("  渲染 %d/%d", i, config.TOTAL_FRAMES)
        proc.stdin.close()
    except BrokenPipeError:
        pass
    rc = proc.wait()
    if rc != 0 or not out_path.exists():
        raise RuntimeError(f"ffmpeg 失败 rc={rc}")
    size_mb = out_path.stat().st_size / 1e6
    log.info("✓ %s (%.1f MB, %.1fs)", out_path.name, size_mb, time.time() - t0)
    return out_path
