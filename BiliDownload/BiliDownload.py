import requests
import re
import json
import os
import sys
import subprocess
import threading
import time
from tqdm import tqdm

# ---- 全局常量和配置 ----
HEADERS = {
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
    "referer": "https://www.bilibili.com/",
    # 请在这里填入你自己的有效Cookie，否则可能无法获取高清晰度视频
    "cookie": "buvid3=602DA801-DD82-AA31-6F48-687ACD04E1B021622infoc; b_nut=1750303821; _uuid=CD1E83DB-4136-6CCB-31102-10BF102F4821A522075infoc; header_theme_version=CLOSE; enable_web_push=DISABLE; enable_feed_channel=ENABLE; buvid_fp=c698f86fb29e1e1a605287ca9f85eb95; CURRENT_QUALITY=0; rpdid=|(m)mku)Rm)0J'u~lR|uYuk); buvid4=3D9C7997-C983-D180-0A55-969DBD6E4EEE22392-025061911-k/TNXIcZfad8HHp6k6mtq4ZsyneG4pORcrxifsQVtTtCaGXX8cxTZ9AdipZxgeNt; b_lsid=DA54BC96_19AFDB6EF01; bmg_af_switch=1; bmg_src_def_domain=i0.hdslb.com; bili_ticket=eyJhbGciOiJIUzI1NiIsImtpZCI6InMwMyIsInR5cCI6IkpXVCJ9.eyJleHAiOjE3NjU0NTI0MTYsImlhdCI6MTc2NTE5MzE1NiwicGx0IjotMX0.Nr3Hbj0W08b3qrM3xJBS80NgxeGUO6CK4q94fITSaBg; bili_ticket_expires=1765452356; SESSDATA=4d774a7e%2C1780745299%2Cb04c6%2Ac1CjBwki4v_a2ZSOTAFoGIo5REfWZMqcu4GpgD3btxZs216wtB8bUaMQhrqc5lL7QZPjgSVkl1VWtXMExSZkRpZm05M0x4blZxODBvUWNwdnM3dnMzc0dMVnBEZEVJOGJ4TUwtRS11VXlXUnlVUF9fQXBscTFpdTlsRGdrNUM1V2ZsMm1ITWNWQ25nIIEC; bili_jct=4c7f0f51ce49e801de3124e2643f20e7; DedeUserID=121163295; DedeUserID__ckMd5=5ee9dc37201faaa2; CURRENT_FNVAL=2000; sid=7ti4v9b0; theme-tip-show=SHOWED; home_feed_column=4; browser_resolution=885-778; theme-avatar-tip-show=SHOWED"
}
QUALITY_MAP = {
    127: '8K 超高清', 120: '4K 超清', 116: '1080P 60帧', 112: '1080P 高码率',
    80: '1080P 高清', 74: '720P 60帧', 64: '720P 高清', 32: '480P 清晰', 16: '360P 流畅'
}


# ---- 辅助函数 ----
def get_ffmpeg_path():
    """
    动态获取 ffmpeg 的路径。
    在打包后的环境中，它会根据操作系统寻找正确的可执行文件名。
    """
    if getattr(sys, 'frozen', False):
        # 程序被打包后
        application_path = sys._MEIPASS
        if sys.platform == "win32":
            return os.path.join(application_path, 'ffmpeg.exe')
        else: # macOS, Linux
            return os.path.join(application_path, 'ffmpeg')
    else:
        # 作为 .py 脚本运行
        return 'ffmpeg'


def download_with_threading(url, filename, headers):
    """多线程下载函数，带Tqdm进度条。"""
    try:
        head_resp = requests.head(url, headers=headers, timeout=10)
        head_resp.raise_for_status()
        total_size = int(head_resp.headers.get('content-length', 0))
    except requests.exceptions.RequestException:
        try:
            response = requests.get(url, headers=headers, stream=True, timeout=10)
            response.raise_for_status()
            total_size = int(response.headers.get('content-length', 0))
        except requests.exceptions.RequestException as e:
            print(f"\n无法获取文件大小: {e}")
            return False

    if total_size == 0:
        print("\n警告: 无法获取文件大小，进度条将不可用。")

    pbar = tqdm(total=total_size, unit='iB', unit_scale=True, desc=os.path.basename(filename).split('.')[0])

    download_success = True

    def download_worker():
        nonlocal download_success
        try:
            response = requests.get(url, headers=headers, stream=True, timeout=20)
            response.raise_for_status()
            with open(filename, 'wb') as f:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
                        pbar.update(len(chunk))
        except requests.exceptions.RequestException as e:
            pbar.close()
            print(f"\n下载线程出错: {e}")
            download_success = False

    downloader_thread = threading.Thread(target=download_worker)
    downloader_thread.start()
    downloader_thread.join()
    pbar.close()

    if not download_success: return False

    if total_size > 0 and os.path.exists(filename):
        final_size = os.path.getsize(filename)
        if final_size < total_size:
            print(f"\n警告: 文件下载不完整! 期望大小: {total_size}, 实际大小: {final_size}")
            return False

    return os.path.exists(filename)


# ---- 核心处理逻辑：处理单个视频 ----
def process_single_video(url, preferred_quality_id):
    """处理单个视频的下载和合并。返回 True 表示成功，False 表示失败。"""
    temp_video_file = f"temp_video_{os.getpid()}.m4s"
    temp_audio_file = f"temp_audio_{os.getpid()}.m4s"

    try:
        print(f"\n{'=' * 20}\n正在处理URL: {url}")
        res = requests.get(url, headers=HEADERS, timeout=15).text

        # 1. 解析视频标题和ID
        title = ""
        try:
            match = re.search(r'<title.*?>(.*?)_哔哩哔哩_bilibili</title>', res)
            if match:
                title = match.group(1).strip()
            else:
                match = re.search(r'<title>(.*?)</title>', res)
                if match: title = match.group(1).split('_哔哩哔哩_bilibili')[0].strip()
            if not title: title = f"bilivideo_{int(time.time())}"
        except Exception:
            title = f"bilivideo_{int(time.time())}"

        playinfo_match = re.search(r'<script>window.__playinfo__=({.*?})</script>', res)
        if not playinfo_match:
            print("错误: 无法在页面中找到视频信息('window.__playinfo__')。可能是付费/地区限制视频，或URL无效。")
            return False

        playinfo_json = json.loads(playinfo_match.group(1))
        data_node = playinfo_json.get('data', {})
        dash_data = data_node.get('dash', {})

        if not dash_data:
            print("错误: 未找到DASH格式的视频流。该视频可能不支持此种下载方式。")
            return False

        video_streams = dash_data.get('video', [])
        audio_streams = dash_data.get('audio', [])

        if not video_streams or not audio_streams:
            print("错误: 视频或音频流列表为空。")
            return False

        print(f"视频标题: {title}")

        # 2. 选择视频流
        video_streams.sort(key=lambda x: x['id'], reverse=True)
        selected_stream = None
        if preferred_quality_id != 0:
            for stream in video_streams:
                if stream['id'] == preferred_quality_id:
                    selected_stream = stream
                    print(f"已匹配到期望清晰度: {QUALITY_MAP.get(stream['id'], '未知')}")
                    break

        if not selected_stream:
            selected_stream = video_streams[0]
            print(f"未找到期望清晰度，自动选择最高可用清晰度: {QUALITY_MAP.get(selected_stream['id'], '未知')}")

        video_url = selected_stream['baseUrl']
        audio_url = audio_streams[0]['baseUrl']
        selected_quality_name = QUALITY_MAP.get(selected_stream['id'], f"{selected_stream.get('height')}P")

        # 3. 构建文件名并下载
        sanitized_title = re.sub(r'[\\/:*?"<>|]', '_', title)
        sanitized_quality = re.sub(r'[\\/:*?"<>|]', '_', selected_quality_name)

        # --- 这是实现目标的关键代码 ---
        # 判断程序是作为脚本运行还是被打包了
        if getattr(sys, 'frozen', False):
            # 如果是打包后的可执行文件，获取其所在目录
            program_dir = os.path.dirname(sys.executable)
        else:
            # 如果是作为 .py 脚本运行，获取脚本所在目录
            program_dir = os.path.dirname(os.path.abspath(__file__))

        # 将文件名和程序目录组合成一个完整的绝对路径
        output_file = os.path.join(program_dir, f"[{sanitized_quality}] {sanitized_title}.mp4")
        # --- 关键代码结束 ---

        if os.path.exists(output_file):
            print(f"文件 '{output_file}' 已存在，跳过下载。")
            return True
        print(f"最终文件名: {output_file}")

        print("\n开始下载视频流...")
        if not download_with_threading(video_url, temp_video_file, HEADERS): raise IOError("视频文件下载失败")

        print("\n开始下载音频流...")
        if not download_with_threading(audio_url, temp_audio_file, HEADERS): raise IOError("音频文件下载失败")

        # 4. 合并
        print("\n正在使用 FFmpeg 合并音视频...")
        ffmpeg_path = get_ffmpeg_path()
        command = [ffmpeg_path, '-i', temp_video_file, '-i', temp_audio_file, '-c', 'copy', '-y', output_file]

        try:
            subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"视频合并完成！已保存为: {output_file}")
            return True
        except FileNotFoundError:
            if sys.platform == "win32":
                print(
                    "\n**错误**: 未找到 'ffmpeg'。请确保 **ffmpeg.exe** 已安装并位于系统PATH中，或与此脚本在同一目录下。")
            else:
                print("\n**错误**: 未找到 'ffmpeg'。请使用包管理器安装它 (例如在 macOS 上: 'brew install ffmpeg')。")
            return False
        except subprocess.CalledProcessError as e:
            print(f"\n错误: ffmpeg 合并文件时出错: {e}。")
            return False

    except Exception as e:
        print(f"\n处理URL时发生严重错误: {e}")
        return False
    finally:
        if os.path.exists(temp_video_file): os.remove(temp_video_file)
        if os.path.exists(temp_audio_file): os.remove(temp_audio_file)


# ---- 主函数：重构为“收集-执行”循环 ----
def main():
    """主函数，包含“收集-执行”的交互式循环。"""
    exit_program = False
    while not exit_program:
        # --- STAGE 1: URL 收集 ---
        urls_to_download = []
        print("\n" + "#" * 60)
        print("### BiliBili 批量下载器 (命令触发版) ###".center(60))
        print("#" * 60)
        print("请逐行输入B站视频URL，每输入一个后按回车。")
        print("\n可用命令:")
        print("  'ok' 或 'start'   : 开始下载已添加的全部视频")
        print("  'list'            : 查看已添加的URL列表")
        print("  'clear'           : 清空当前URL列表")
        print("  'exit'            : 退出程序")
        print("-" * 25)

        while True:
            prompt = f"[{len(urls_to_download)}] 请输入URL或命令: "
            user_input = input(prompt).strip()

            command = user_input.lower()

            if command in ('ok', 'start'):
                if not urls_to_download:
                    print("URL列表为空，请输入至少一个URL后再开始下载。")
                    continue
                break

            elif command == 'exit':
                exit_program = True
                break

            elif command == 'list':
                if not urls_to_download:
                    print("当前列表为空。")
                else:
                    print("\n--- 当前待下载列表 ---")
                    for i, url in enumerate(urls_to_download):
                        print(f"  {i + 1}: {url}")
                    print("----------------------")
                continue

            elif command == 'clear':
                urls_to_download.clear()
                print("URL列表已清空。")
                continue

            elif user_input.startswith('http'):
                if user_input not in urls_to_download:
                    urls_to_download.append(user_input)
                    print(f"  -> 已添加第 {len(urls_to_download)} 个URL。")
                else:
                    print("  -> 此URL已存在于列表中。")

            elif not user_input:
                continue

            else:
                print("  -> 无效输入。请输入有效的URL或命令 (ok, list, clear, exit)。")

        if exit_program:
            break

        # --- STAGE 2: 选择清晰度 ---
        print("\n请为本轮任务选择期望的视频清晰度（将优先选择此项，若无则自动选择最高）：")
        quality_options = sorted(QUALITY_MAP.items(), key=lambda x: x[0], reverse=True)

        print("[0] 自动选择每个视频的最高清晰度")
        for i, (qid, qname) in enumerate(quality_options):
            print(f"[{i + 1}] {qname}")

        while True:
            try:
                choice = int(input(f"\n请输入选项 (0-{len(quality_options)}): "))
                if 0 <= choice <= len(quality_options):
                    break
                else:
                    print("无效选项，请重新输入。")
            except ValueError:
                print("请输入数字。")

        preferred_quality_id = 0
        if choice > 0:
            preferred_quality_id = quality_options[choice - 1][0]
            print(f"\n已选择期望清晰度: **{QUALITY_MAP[preferred_quality_id]}**")
        else:
            print("\n已选择: **自动选择最高清晰度**")

        # --- STAGE 3: 执行下载任务 ---
        success_count = 0
        fail_count = 0
        total_videos = len(urls_to_download)

        for i, url in enumerate(urls_to_download):
            print(f"\n--- 开始处理第 {i + 1} / {total_videos} 个视频 ---")
            if process_single_video(url, preferred_quality_id):
                success_count += 1
            else:
                fail_count += 1
                print(f"--- 第 {i + 1} / {total_videos} 个视频处理失败 ---")

        # --- STAGE 4: 总结并准备下一轮 ---
        print(f"\n{'=' * 20}\n本轮任务已完成！")
        print(f"**总任务**: {total_videos}")
        print(f"**成功**: {success_count}")
        print(f"**失败**: {fail_count}")
        print("\n正在返回主菜单...")
        time.sleep(3)

    print("\n程序已退出。感谢使用！")
    input("按回车键关闭窗口...")


if __name__ == "__main__":
    main()
