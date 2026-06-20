#!/usr/bin/env python3
"""
交付强制检查脚本 — 不通过则不能交付
用法: python deliver_check.py <笔记文件路径>
返回值: 0=通过, 1=不通过
共21项检查。
"""

import sys, re, os, argparse

PLACEHOLDER_KEYWORDS = ["完整内容请阅读","更多内容","待补充","见下文","请参考英文原文","（完整","（以下为","请阅读全文","查看更多","后续内容","编者注","此部分为","转录内容请见下文"]
REQUIRED_P1_SECTIONS = ["一句话总结","核心观点","操作建议","适合谁看"]
# 功能连词弱检查（清理时不应删除这些词，保留≥3个）
FUNC_MARKERS = ["因为","所以","但是","而且","可能"]
# 赘词（清理时可以删除，不检查）
FILLER_MARKERS = ["我觉得","对吧","就是说","然后","就是"]
ILLEGAL_FILENAME_CHARS = '\\/:*?"<>|'

def count_cn_chars(text):
    return sum(1 for c in text if '\u4e00'<=c<='\u9fff' or '\u3400'<=c<='\u4dbf' or '\uf900'<=c<='\ufaff')

def check_note(path, accept_low_retention=False):
    errors, warnings = [], []
    with open(path,'r',encoding='utf-8') as f: content = f.read()
    filename = os.path.basename(path)
    lines = content.split('\n')

    # 1. 占位符
    for kw in PLACEHOLDER_KEYWORDS:
        if kw in content:
            errors.append(f"[占位符] 发现禁止关键词: 「{kw}」"); break

    # 2. P1四段完整性
    for s in REQUIRED_P1_SECTIONS:
        if s not in content: errors.append(f"[P1缺失] 缺少「{s}」")

    # 3. 核心观点字数≥150
    op, in_op = "", False
    for line in lines:
        if '核心观点' in line and line.strip().startswith('#'): in_op = True; continue
        if in_op and (('操作建议' in line or '适合谁看' in line) and line.strip().startswith('#')): break
        if in_op: op += line + ' '
    cn = count_cn_chars(re.sub(r'[*#\n\r\s\-]','',op))
    if cn < 150: errors.append(f"[P1观点太薄] 核心观点仅 {cn} 字，需≥150")

    # 4. 总结≥15字
    su, in_su = "", False
    for line in lines:
        if '一句话总结' in line and line.strip().startswith('#'): in_su = True; continue
        if in_su and line.strip().startswith('#'): break
        if in_su: su += line + ' '
    cn_su = count_cn_chars(re.sub(r'[*#\n\r\s\-]','',su))
    if cn_su < 15: errors.append(f"[P1总结空洞] 总结仅 {cn_su} 字")

    # 5. Part 2 — 用正则匹配标题，兼容 emoji 和不同格式
    p2 = ""
    p2_match = re.search(r'##\s*[\U0001f4cb\U0001f4dd]?\s*第二部分', content)
    if p2_match:
        p2 = content[p2_match.end():]
    elif "## 第二部分" in content:
        p2 = content.split("## 第二部分")[1]
    else:
        errors.append("[P2缺失] 没有第二部分")

    bold_titles = []
    if p2:
        p3_match = re.search(r'##\s*[\U0001f4d6]?\s*第三部分', p2)
        if p3_match:
            p2 = p2[:p3_match.start()]
        elif "## 📖 第三部分" in p2:
            p2 = p2.split("## 📖 第三部分")[0]
        bold_titles = re.findall(r'^\*\*([^*]+)\*\*', p2, re.MULTILINE)
        if len(bold_titles) < 2: errors.append(f"[P2标题不足] 仅 {len(bold_titles)} 个")
        func_count = sum(p2.count(m) for m in FUNC_MARKERS)
        if func_count < 3: errors.append(f"[P2疑似重写] 功能连词仅{func_count}个，需≥3")
        seen = set()
        for para in [l.strip() for l in p2.split('\n') if l.strip() and len(l.strip())>20]:
            s = para[:30]
            if s in seen: warnings.append(f"[P2重复] 「{s}...」"); break
            seen.add(s)

    # 6. P1/P2比例
    p1_pattern = r'##\s*[\U0001f4cb]?\s*第一部分'
    p2_pattern = r'##\s*[\U0001f4cb\U0001f4dd]?\s*第二部分'
    p1_m = re.search(p1_pattern, content)
    p2_m = re.search(p2_pattern, content)
    if p1_m and p2_m:
        p1_text = content[p1_m.end():p2_m.start()]
        p2c = content[p2_m.end():]
        p3_m = re.search(r'##\s*[\U0001f4d6]?\s*第三部分', p2c)
        if p3_m: p2c = p2c[:p3_m.start()]
        elif "## 📖 第三部分" in p2c: p2c = p2c.split("## 📖 第三部分")[0]
        p2_cn = count_cn_chars(p2c)
        p1_cn = count_cn_chars(p1_text)
        if p1_cn > p2_cn * 0.5 and p2_cn > 0: warnings.append(f"[P1/P2比例] P1({p1_cn})超过P2({p2_cn})的50%")
    # 7. 文件名
    for c in filename:
        if c in ILLEGAL_FILENAME_CHARS: errors.append(f"[文件名非法] 含: {c}"); break

    # 8. 语言+P3 — 用正则匹配第三部分，兼容 emoji
    p3_pattern = r'##\s*[\U0001f4d6]?\s*第三部分'
    has_p3 = re.search(p3_pattern, content) is not None or "## 📖 第三部分" in content
    is_cn = count_cn_chars(content) > len(re.findall(r'[a-zA-Z]',content)) * 2
    if is_cn and has_p3: errors.append("[P3多余] 中文视频不应有第三部分")
    if not is_cn and not has_p3: errors.append("[P3缺失] 英文视频应有第三部分")

    # 9. 平台-语言
    sm = re.search(r'source:\s*(\S+)', content)
    if sm:
        p = sm.group(1).lower()
        if p in ["xiaohongshu","douyin","bilibili"] and not is_cn: warnings.append(f"[平台语言] {p}应为中文")
        if p == "youtube" and is_cn: warnings.append("[平台语言] YouTube应为英文")

    # 10. 源数据
    sj = path.replace('.md','.json')
    if os.path.exists(sj): warnings.append(f"[源数据] 原始JSON未清理: {os.path.basename(sj)}")

    # 11. 时间戳
    if has_p3:
        pf = content.split("## 📖 第三部分")[1]
        ts = re.findall(r'\[(\d+):(\d+)\]', pf)
        if ts:
            gaps = []
            for i in range(len(ts)-1):
                gap = (int(ts[i+1][0])*60+int(ts[i+1][1])) - (int(ts[i][0])*60+int(ts[i][1]))
                if gap > 240: gaps.append(f"[{ts[i][0]}:{ts[i][1]}]->[{ts[i+1][0]}:{ts[i+1][1]}]")
            if gaps: errors.append(f"[时间戳跳空] P3跳空 >2分钟: {'; '.join(gaps[:3])}")

    # 12. 音频完整性 — 分层校验
    dm = re.search(r'_duration:\s*(\d+)', content)
    am = re.search(r'_audio_mb:\s*([\d.]+)', content)
    ds = int(dm.group(1)) if dm else 0
    audio_ok = False  # 初始为未通过，由各层校验决定

    # 第1层：Part 3 时间戳（YouTube来源，最可靠）
    if has_p3:
        ts_vals = re.findall(r'\[(\d+):(\d+)\]', content)
        if ts_vals:
            last_ts = max(int(m)*60 + int(s) for m, s in ts_vals)
            if ds > 0 and last_ts > 0:
                ratio = last_ts / ds
                if ratio < 0.85:
                    errors.append(f"[音频截断] 最后时间戳{last_ts//60}:{last_ts%60:02d}，视频时长{ds//60}:{ds%60:02d}，仅覆盖{ratio*100:.0f}%")
                else:
                    audio_ok = True

    # 第2层：128kbps 文件大小估算（fallback，无时间戳时使用）
    if not audio_ok and dm and am:
        mb = float(am.group(1))
        expected = ds * 128 / 8 / 1024
        if mb < expected * 0.85:
            errors.append(f"[音频截断] 期望~{expected:.0f}MB(128kbps)，实际{mb:.1f}MB")

    # 第3层：转录字数 vs 时长（最终兜底）
    if ds > 0:
        cn_p2 = count_cn_chars(p2) if p2 else 0
        rr = re.search(r'_raw_chars:\s*(\d+)', content)
        raw_chars_val = int(rr.group(1)) if rr else count_cn_chars(content)
        min_chars = int(ds * 0.68) if raw_chars_val / max(ds, 1) > 4.5 else int(ds * 1.5)
        if cn_p2 < min_chars:
            if accept_low_retention:
                warnings.append(f"[转录缺失] 已绕过（--accept-low-retention）：视频{ds}秒，P2仅{cn_p2}字，需≥{min_chars}字")
            else:
                errors.append(f"[转录缺失] 视频{ds}秒，P2仅{cn_p2}字，需≥{min_chars}字。建议用 --model base 或 --model small 重跑，或使用 --accept-low-retention 绕过。")
        # 最终检查：最后一段是否完整
        if p2:
            last_para = [l for l in p2.split('\n') if l.strip()][-3:]
            last_text = ' '.join(last_para)
            abrupt_ends = ['...', '未完', '待续', '更多内容']
            if any(e in last_text for e in abrupt_ends):
                errors.append(f"[转录截断] Part 2 结尾含截断标记: {last_text[-50:]}")

    # 13. Frontmatter格式
    fm = re.search(r'^---\n(.*?)\n---', content, re.DOTALL)
    if fm:
        ft = fm.group(1)
        if re.search(r'tags:\s*\[', ft): errors.append("[Frontmatter] tags用了内联格式，应使用YAML列表")
        for fld in ['url','date']:
            if re.search(fld + r':\s*"[^"]*"', ft): warnings.append(f"[Frontmatter] {fld}有外层引号")

    # 14. 过度清理
    ci2 = count_cn_chars(p2) if p2 else 0
    rm = re.search(r'_raw_chars:\s*(\d+)', content)
    if rm and ci2 > 0:
        rc = int(rm.group(1))
        sr = rc / max(ds if dm else 600, 1)
        # 基础阈值：英文8%，中文按语速分档
        th = 0.08 if has_p3 else (0.10 if sr > 5.0 else (0.27 if sr > 3.5 else 0.45))
        # 35% 硬下限：中文视频且语速 ≥3.5 时，保留率不得低于 35%
        if not has_p3 and sr >= 3.5:
            th = 0.35
        if ci2 < rc * th:
            errors.append(f"[过度清理] 原始{rc}字，清理后仅{ci2}字({ci2/rc*100:.0f}%)")


    # 15-19. Part 3检查
    if has_p3:
        p3_m = re.search(r'##\s*[\U0001f4d6]?\s*第三部分', content)
        p3 = content[p3_m.end():] if p3_m else content.split("## 📖 第三部分")[1]
        p3e = len(re.findall(r'[a-zA-Z]', p3))
        rm2 = re.search(r'_raw_chars:\s*(\d+)', content)
        if rm2:
            ex = int(rm2.group(1))
            ac = len(p3)
            if ac < ex * 0.65: errors.append(f"[P3不完整] 原始{ex}字，P3仅{ac}字({ac/ex*100:.0f}%)")
        if p2 and p3e > 500:
            ci3 = count_cn_chars(p2)
            pw = len(re.findall(r"[a-zA-Z']+", p3))
            mt = int(pw * 0.5) if pw > 100 else int(p3e * 0.22)
            if ci3 < mt: errors.append(f"[翻译不足] 英{p3e}字母，中{ci3}字，需≥{mt}字")
        pt = p3.split('> 说明')[1] if '> 说明' in p3 else p3
        pp = len(re.findall(r'[.!?]', p3))
        pr = pp / max(len(pt), 1) * 1000  # 千分比 (per mille)
        if pr < 3: errors.append(f"[P3标点不足] 标点率{pr:.1f}‰")
        pb = pt.strip()
        if len(pb) > 3000 and '\n\n' not in pb[:3000]: errors.append("[P3无分段] 英文超3000字符无分段")
        tsl = len(re.findall(r'^\[\d+:\d+\]', pb, re.MULTILINE))
        if tsl > 10 and bold_titles and len(bold_titles) < tsl * 0.15:
            warnings.append(f"[翻译覆盖] P3有{tsl}个时间戳段，P2仅{len(bold_titles)}个小标题")

    # 20. 文件不为空
    if len(content) < 200: errors.append("[文件过短]")

    all_ok = len(errors) == 0
    for w in warnings: print(f"   ⚠️ {w}")
    return all_ok, errors


def main():
    parser = argparse.ArgumentParser(description="交付强制检查脚本 — 不通过则不能交付")
    parser.add_argument("path", help="笔记文件路径")
    parser.add_argument("--accept-low-retention", action="store_true",
                        help="绕过低保留率错误（过度清理/转录缺失），仅降级为警告。适用于已确认 tiny 质量可接受或已人工核实的场景。")
    args = parser.parse_args()
    passed, errors = check_note(args.path, accept_low_retention=args.accept_low_retention)
    if passed: print("✅ 自检通过，可以交付"); sys.exit(0)
    else:
        print("❌ 自检不通过，必须修复以下问题：")
        for e in errors: print(f"   {e}")
        print("\n修完重新运行本脚本，通过后才能交付"); sys.exit(1)

if __name__ == "__main__": main()
