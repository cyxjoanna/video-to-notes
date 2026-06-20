# 安装说明

## 依赖安装

```bash
pip install -r requirements.txt
```

ffmpeg 是系统依赖，需单独安装：
- Windows: `winget install ffmpeg` 或手动添加 PATH
- macOS: `brew install ffmpeg`
- Linux: `apt install ffmpeg`

## 首次使用

加载 skill 后 Agent 会依次询问：
1. 输出目录路径
2. 是否需要代理
3. 确认依赖已安装

首次转录时 Whisper 会自动下载 small 模型（约 500MB）。
