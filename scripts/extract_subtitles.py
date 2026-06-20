#!/usr/bin/env python3
"""
跨平台视频字幕提取工具
支持：YouTube / B站 / 抖音 / 小红书 / 微博 / TikTok

策略：
  - YouTube: youtube_transcript_api（0下载）
  - B站: yt-dlp 字幕 → bilibili-api 音频 → 通用音频下载（三级回退）
  - 其他: yt-dlp 下载音频 → Whisper 转录 → 删除音频

输出：JSON 到 stdout
"""

import sys
import os
import re
import json
import glob
import shutil
import tempfile
import subprocess
import argparse
import urllib.request
from pathlib import Path


# ── 平台检测 ──

PLATFORM_PATTERNS = {
    "youtube": [
        r"youtube\.com/watch\?",
        r"youtu\.be/",
        r"youtube\.com/shorts/",
        r"youtube\.com/embed/",
    ],
    "bilibili": [
        r"bilibili\.com/video/",
        r"bilibili\.com/BV",
        r"b23\.tv/",
    ],
    "douyin": [
        r"douyin\.com/video/",
        r"douyin\.com/note/",
        r"v\.douyin\.com/",
        r"iesdouyin\.com/",
    ],
    "xiaohongshu": [
        r"xiaohongshu\.com/explore/",
        r"xiaohongshu\.com/discovery/item/",
        r"xhslink\.com/",
    ],
    "weibo": [
        r"weibo\.com/",
        r"weibo\.cn/",
    ],
    "tiktok": [
        r"tiktok\.com/",
        r"vm\.tiktok\.com/",
    ],
}


def detect_platform(url: str) -> str | None:
    for platform, patterns in PLATFORM_PATTERNS.items():
        for p in patterns:
            if re.search(p, url, re.IGNORECASE):
                return platform
    return None


# ── 工具函数 ──

def get_ydlp_cmd(extra_args=None):
    """获取 yt-dlp 基础命令（含代理支持）
    
    优先使用 shutil.which 检测本机可执行文件名（yt-dlp 或 yt-dlp.exe），
    找不到时仍返回 yt-dlp，让错误信息自然暴露。
    """
    ytdlp = "yt-dlp"
    for name in ["yt-dlp", "yt-dlp.exe"]:
        if shutil.which(name):
            ytdlp = name
            break
    cmd = [ytdlp, "--no-check-certificates"]
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or ""
    if proxy:
        cmd.extend(["--proxy", proxy])
    if extra_args:
        cmd.extend(extra_args)
    return cmd


def get_title_from_ytdlp(url: str, timeout: int = 30) -> str:
    """用 yt-dlp --get-title 获取视频标题"""
    try:
        result = subprocess.run(
            get_ydlp_cmd(["--get-title", "--skip-download", url]),
            capture_output=True, text=True, timeout=timeout,
        )
        title = result.stdout.strip().split("\n")[0] if result.stdout.strip() else ""
        return title
    except Exception:
        return ""


def looks_like_video_id(s: str) -> bool:
    if re.match(r'^[a-zA-Z0-9_-]{11}$', s.strip()):
        return True
    if re.match(r'^BV[a-zA-Z0-9]{10}$', s.strip()):
        return True
    return False


def parse_vtt(path: str) -> str:
    """把 VTT 字幕解析成纯文本"""
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    out = []
    for line in lines:
        line = line.strip()
        if not line or line == "WEBVTT" or "-->" in line:
            continue
        if line.isdigit() or line.startswith(("Kind:", "Language:", "NOTE")):
            continue
        line = re.sub(r"<[^>]+>", "", line).replace("&nbsp;", " ").strip()
        if not line or (out and out[-1] == line):
            continue
        out.append(line)
    return "\n".join(out)


def _vtt_time_to_sec(t: str) -> float:
    """Convert VTT timestamp (00:00:01.500 or 00:01.500) to seconds"""
    t = t.replace(",", ".")
    parts = t.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    elif len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return 0


def parse_vtt_segments(path: str) -> list:
    """Parse VTT into segments [{start, end, text}]"""
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    segments = []
    current_start = None
    current_end = None
    current_lines = []
    for line in lines:
        line = line.strip()
        if "-->" in line:
            if current_start is not None and current_lines:
                segments.append({
                    "start": _vtt_time_to_sec(current_start),
                    "end": _vtt_time_to_sec(current_end),
                    "text": " ".join(current_lines),
                })
            parts = line.split("-->")
            current_start = parts[0].strip()
            # VTT 时间戳后可能跟 settings（如 align:start position:0%），只取时间戳部分
            current_end = parts[1].strip().split()[0]
            current_lines = []
        elif line and not line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")) and not line.isdigit():
            cleaned = re.sub(r"<[^>]+>", "", line).replace("&nbsp;", " ").strip()
            if cleaned:
                current_lines.append(cleaned)
    if current_start is not None and current_lines:
        segments.append({
            "start": _vtt_time_to_sec(current_start),
            "end": _vtt_time_to_sec(current_end),
            "text": " ".join(current_lines),
        })
    return segments


# ── YouTube: youtube_transcript_api ──

def _extract_youtube_api(url: str) -> dict:
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api.proxies import GenericProxyConfig

    proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or ""
    proxy_config = GenericProxyConfig(http_url=proxy_url, https_url=proxy_url) if proxy_url else None

    patterns = [
        r"v=([a-zA-Z0-9_-]{11})",
        r"youtu\.be/([a-zA-Z0-9_-]{11})",
        r"shorts/([a-zA-Z0-9_-]{11})",
        r"embed/([a-zA-Z0-9_-]{11})",
    ]
    video_id = None
    for p in patterns:
        m = re.search(p, url)
        if m:
            video_id = m.group(1)
            break
    if not video_id:
        return {"success": False, "error": "无法从 URL 提取 YouTube video_id"}

    title = get_title_from_ytdlp(url)
    if title and looks_like_video_id(title):
        title = ""

    transcript_text = None
    lang = None

    try:
        api = YouTubeTranscriptApi(proxy_config=proxy_config) if proxy_config else YouTubeTranscriptApi()
        transcript_list = api.list(video_id)

        for lang_code in ["zh-CN", "zh-Hans", "zh", "zh-Hant", "zh-TW"]:
            try:
                transcript = transcript_list.find_transcript([lang_code])
                data = transcript.fetch()
                transcript_text = "\n".join(item.text for item in data.snippets)
                lang = "zh"
                break
            except Exception:
                continue

        if not transcript_text:
            for lang_code in ["en", "en-US"]:
                try:
                    transcript = transcript_list.find_transcript([lang_code])
                    data = transcript.fetch()
                    transcript_text = "\n".join(item.text for item in data.snippets)
                    lang = "en"
                    break
                except Exception:
                    continue

        if not transcript_text:
            try:
                transcript = transcript_list.find_generated_transcript(["zh-CN", "zh-Hans", "zh"])
                data = transcript.fetch()
                transcript_text = "\n".join(item.text for item in data.snippets)
                lang = "zh"
            except Exception:
                pass

        if not transcript_text:
            try:
                transcript = transcript_list.find_generated_transcript(["en"])
                data = transcript.fetch()
                transcript_text = "\n".join(item.text for item in data.snippets)
                lang = "en"
            except Exception:
                pass

    except Exception as e:
        return {"success": False, "error": f"获取字幕失败: {str(e)}"}

    if not transcript_text:
        return {"success": False, "error": "该视频没有任何可用字幕"}

    # Build segments from API response
    api_segments = []
    for item in data.snippets:
        seg = {"start": item.start, "text": item.text}
        # youtube_transcript_api snippet 字段为 start/duration/text，不含 end
        seg["end"] = item.start + item.duration if hasattr(item, 'duration') and item.duration else item.start + 5
        api_segments.append(seg)

    return {
        "success": True,
        "platform": "youtube",
        "title": title,
        "title_is_id": bool(title and looks_like_video_id(title)),
        "language": lang,
        "text": transcript_text,
        "segments": api_segments,
        "source": "subtitle",
    }


def extract_youtube(url: str, model_name: str = "tiny") -> dict:
    """YouTube 提取：先尝试 API 字幕，失败时回退 Whisper 转录"""
    result = _extract_youtube_api(url)
    if result.get("success"):
        return result
    # 回退：下载音频 + Whisper
    print(f"  🔄 YouTube字幕API失败: {result.get('error', '')}", file=sys.stderr)
    print("  🔄 回退到 Whisper 转录...", file=sys.stderr)
    return extract_by_audio(url, "youtube", model_name)


# ── B站：三级回退策略 ──

def resolve_b23(url: str) -> str:
    import urllib.request
    if 'b23.tv' not in url:
        return url
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, timeout=15)
        return resp.geturl()
    except Exception:
        return url

def extract_bilibili(url: str, model_name: str = "tiny") -> dict:
    """B站提取：字幕 → bilibili-api音频 → 通用音频下载"""
    url = resolve_b23(url)  # 先解析短链
    title = get_title_from_ytdlp(url)

    # 策略1: yt-dlp 写字幕
    result = _bilibili_subs(url, title)
    if result.get("success"):
        return result

    # 策略2: bilibili-api 下载音频
    print("  🔄 B站无字幕，用 bilibili-api 下载音频...", file=sys.stderr)
    result = _bilibili_api_audio(url, title, model_name)
    if result.get("success"):
        return result

    # 策略3: 通用音频下载（兜底）
    print("  🔄 B站API失败，回退通用音频下载...", file=sys.stderr)
    return extract_by_audio(url, "bilibili", model_name)


def _bilibili_subs(url: str, title: str) -> dict:
    """用 yt-dlp --write-subs 取字幕"""
    tmpdir = tempfile.mkdtemp(prefix="bili_sub_")
    try:
        subprocess.run(
            get_ydlp_cmd([
                "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "--referer", "https://www.bilibili.com",
                "--skip-download",
                "--write-subs", "--write-auto-subs",
                "--sub-langs", "zh-Hans,zh-CN,zh,zh-Hant,ai-zh,en,en-US",
                "--sub-format", "vtt",
                "-o", os.path.join(tmpdir, "sub.%(ext)s"),
                url,
            ]),
            capture_output=True, text=True, timeout=120,
        )

        vtt_files = glob.glob(os.path.join(tmpdir, "*.vtt"))
        if not vtt_files:
            return {"success": False, "error": "no_subtitle_tracks", "title": title, "platform": "bilibili"}

        def rank(path):
            name = os.path.basename(path).lower()
            priorities = ["zh-hans", "zh-cn", "zh", "zh-hant", "ai-zh", "en"]
            for i, tag in enumerate(priorities):
                if tag in name:
                    return i
            return 99
        chosen = sorted(vtt_files, key=rank)[0]

        text = parse_vtt(chosen)
        if len(text) < 50:
            return {"success": False, "error": "字幕内容过短", "title": title, "platform": "bilibili"}

        lang = "zh" if re.search(r"[\u4e00-\u9fff]", text) else "en"

        vtt_segments = parse_vtt_segments(chosen)

        return {
            "success": True,
            "platform": "bilibili",
            "title": title,
            "language": lang,
            "text": text,
            "segments": vtt_segments,
            "source": "subtitle",
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "B站请求超时", "title": title, "platform": "bilibili"}
    except Exception as e:
        return {"success": False, "error": f"B站处理异常: {str(e)}", "title": title, "platform": "bilibili"}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _bilibili_api_audio(url: str, title: str, model_name: str = "tiny") -> dict:
    """用 bilibili-api 获取音频流直接下载 → Whisper 转录"""
    try:
        import asyncio
        from bilibili_api import video

        # 尝试从URL提取BV号
        bv_match = re.search(r"BV[a-zA-Z0-9]{10}", url)
        if not bv_match:
            try:
                resp = urllib.request.urlopen(
                    urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}),
                    timeout=15
                )
                resolved = resp.geturl()
                bv_match = re.search(r"BV[a-zA-Z0-9]{10}", resolved)
                print(f"  🔍 Resolved to '{resolved}': {'found BV' if bv_match else 'no BV'}", file=sys.stderr)
            except Exception as e2:
                print(f"  🔍 Resolve error: {e2}", file=sys.stderr)
        if not bv_match:
            return {"success": False, "error": "无法提取BV号", "title": title, "platform": "bilibili"}
        bvid = bv_match.group(0)

        async def get_audio_data():
            v = video.Video(bvid)
            info = await v.get_info()
            dl = await v.get_download_url(0)
            return info["title"], dl["dash"]["audio"][0]["baseUrl"]

        bili_title, audio_url = asyncio.run(get_audio_data())
        if not title or bili_title:
            title = bili_title

        tmpdir = tempfile.mkdtemp(prefix="bili_api_audio_")
        audio_path = os.path.join(tmpdir, "audio.m4s")

        # 用 urllib 下载（避免 req 变量名冲突）
        audio_req = urllib.request.Request(audio_url, headers={
            "Referer": "https://www.bilibili.com",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })
        with urllib.request.urlopen(audio_req, timeout=120) as resp:
            with open(audio_path, "wb") as f:
                f.write(resp.read())

        if not os.path.exists(audio_path) or os.path.getsize(audio_path) < 1000:
            shutil.rmtree(tmpdir, ignore_errors=True)
            return {"success": False, "error": "音频下载失败", "title": title, "platform": "bilibili"}

        size_mb = os.path.getsize(audio_path) / (1024 * 1024)
        print(f"  ✅ 音频下载完成 ({size_mb:.1f} MB)", file=sys.stderr)

        from faster_whisper import WhisperModel
        print(f"  🎤 faster-whisper 转录中...（{model_name}模型）", file=sys.stderr)
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
        segments_gen, info = model.transcribe(audio_path, beam_size=5, language=None)

        detected_lang = info.language
        segments_list = list(segments_gen)  # materialize
        text_parts = [seg.text.strip() for seg in segments_list]
        text = " ".join(text_parts).strip()
        segments_out = [{"start": seg.start, "end": seg.end, "text": seg.text.strip()} for seg in segments_list]

        lang_map = {"zh": "zh", "en": "en", "ja": "en"}
        lang = lang_map.get(detected_lang, "en")

        shutil.rmtree(tmpdir, ignore_errors=True)
        return {
            "success": True,
            "platform": "bilibili",
            "title": title,
            "language": lang,
            "text": text,
            "segments": segments_out,
            "source": "whisper_api",
        }

    except ImportError:
        return {"success": False, "error": "bilibili-api 未安装，请执行: pip install bilibili-api-python", "title": title, "platform": "bilibili"}
    except Exception as e:
        print(f"  ❌ B站API异常: {type(e).__name__}: {str(e)[:200]}", file=sys.stderr)
        return {"success": False, "error": f"B站API音频下载异常: {str(e)}", "title": title, "platform": "bilibili"}


# ── 其他平台：下载音频 → Whisper 转录 → 删音频 ──

def extract_by_audio(url: str, platform: str, model_name: str = "tiny") -> dict:
    """下载音频 → faster-whisper 转录 → 删除音频"""
    from faster_whisper import WhisperModel

    tmpdir = tempfile.mkdtemp(prefix=f"{platform}_audio_")
    audio_path = os.path.join(tmpdir, "audio.mp3")
    title = ""

    try:
        title = get_title_from_ytdlp(url, timeout=30)

        print(f"  ⬇️  下载音频...", file=sys.stderr)
        audio_dl = subprocess.run(
            get_ydlp_cmd([
                "--extract-audio", "--audio-format", "mp3",
                "--audio-quality", "128K",
                "-o", audio_path,
                url,
            ]),
            capture_output=True, text=True, timeout=600,
        )
        if audio_dl.returncode != 0:
            err_msg = audio_dl.stderr[:200] if audio_dl.stderr else "yt-dlp返回非零"
            raise RuntimeError(f"音频下载失败 (exit={audio_dl.returncode}): {err_msg}")

        if not os.path.exists(audio_path):
            mp3_files = glob.glob(os.path.join(tmpdir, "*.mp3"))
            if mp3_files:
                audio_path = mp3_files[0]
            else:
                return {"success": False, "error": "音频下载失败，未找到输出文件", "title": title, "platform": platform}

        size_mb = os.path.getsize(audio_path) / (1024 * 1024)
        print(f"  ✅ 音频下载完成 ({size_mb:.1f} MB)", file=sys.stderr)

        print(f"  🎤 faster-whisper 转录中...（{model_name}模型 int8 量化）", file=sys.stderr)
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
        segments_gen, info = model.transcribe(audio_path, beam_size=5, language=None)

        detected_lang = info.language
        segments_list = list(segments_gen)  # materialize
        text_parts = [seg.text.strip() for seg in segments_list]
        text = " ".join(text_parts).strip()
        segments_out = [{"start": seg.start, "end": seg.end, "text": seg.text.strip()} for seg in segments_list]

        if not text:
            return {"success": False, "error": "faster-whisper 转录结果为空", "title": title, "platform": platform}

        lang_map = {"zh": "zh", "en": "en", "ja": "en"}
        lang = lang_map.get(detected_lang, "en")

        return {
            "success": True,
            "platform": platform,
            "title": title,
            "language": lang,
            "text": text,
            "segments": segments_out,
            "source": "whisper",
            "duration_seconds": info.duration if hasattr(info, 'duration') and info.duration else 0,
        }

    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"{platform} 音频下载超时", "title": title, "platform": platform}
    except ImportError:
        return {"success": False, "error": "faster-whisper 未安装，请执行: pip install faster-whisper", "title": title, "platform": platform}
    except Exception as e:
        return {"success": False, "error": f"{platform} 处理异常: {str(e)}", "title": title, "platform": platform}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        print(f"  🗑️  临时音频已删除", file=sys.stderr)


# ── 主入口 ──

def main():
    parser = argparse.ArgumentParser(description="跨平台视频字幕提取工具")
    parser.add_argument("url", help="视频链接")
    parser.add_argument("--model", default="tiny", choices=["tiny", "base", "small", "medium", "large-v3"],
                        help="Whisper 模型大小（默认: tiny）。低保留率失败时请升级到 base 或 small 重跑。")
    args = parser.parse_args()

    url = args.url.strip()
    model_name = args.model

    platform = detect_platform(url)
    if not platform:
        try:
            result = subprocess.run(
                get_ydlp_cmd(["--get-title", "--skip-download", url]),
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                platform = "generic"
            else:
                print(json.dumps({"success": False, "error": f"无法识别平台或链接无效: {url}"}))
                sys.exit(1)
        except Exception:
            print(json.dumps({"success": False, "error": f"无法识别平台或链接无效: {url}"}))
            sys.exit(1)

    print(f"  🎯 平台: {platform}  模型: {model_name}", file=sys.stderr)

    if platform == "youtube":
        result = extract_youtube(url, model_name)
    elif platform == "bilibili":
        result = extract_bilibili(url, model_name)
    else:
        result = extract_by_audio(url, platform, model_name)

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
