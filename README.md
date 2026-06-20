# Media Transcription Workflow

跨平台视频转录工作流。自动下载音频、Whisper 转录、生成结构化笔记（P1精读/P2逐字稿/P3原文），支持 YouTube / B站 / 小红书 / 抖音。

## 快速开始

```bash
pip install -r requirements.txt
```

提取视频字幕：

```bash
python scripts/extract_subtitles.py "https://www.youtube.com/watch?v=..." --model tiny
```

交付质量检查：

```bash
python scripts/deliver_check.py 笔记文件.md
```

详细使用说明见 [SKILL.md](SKILL.md)。

## 特性

- **四平台支持**：YouTube（API字幕优先）、B站（三级回退）、小红书、抖音
- **可配置模型**：支持 `--model tiny/base/small/medium/large-v3`，默认 tiny
- **自动质量门控**：`deliver_check.py` 21项检查拦截低质量交付
- **35% 硬下限**：中文视频保留率不足时强制阻断，逼 Agent 升级模型
- **自动升级流程**：tiny → base → small 三级重跑（Agent 按 SKILL.md 执行）

## 依赖

- Python 3.10+
- ffmpeg（系统级依赖）
- 各平台包见 requirements.txt

## License

MIT
