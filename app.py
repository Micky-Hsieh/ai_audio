from flask import Flask, request, jsonify
import subprocess
import os
import tempfile
from urllib.request import urlopen

app = Flask(__name__)

@app.route('/split-audio', methods=['POST'])
def split_audio():
    """
    接收音频文件，切割成 20 分钟的片段
    
    请求格式：
    {
        "file_url": "https://...",  # 音频文件 URL
        "file_name": "audio.mp3",    # 文件名
        "chunk_duration_minutes": 20  # 切割时长（分钟）
    }
    """
    try:
        data = request.json
        file_url = data.get('file_url')
        file_name = data.get('file_name', 'audio.mp3')
        chunk_duration = int(data.get('chunk_duration_minutes', 20)) * 60  # 转秒数
        
        if not file_url:
            return jsonify({"error": "file_url 是必需的"}), 400
        
        # 创建临时目录
        with tempfile.TemporaryDirectory() as tmpdir:
            # 下载音频文件
            input_path = os.path.join(tmpdir, file_name)
            print(f"下载音频文件...")
            urlopen(file_url)  # 验证 URL 可访问
            
            # 使用 FFmpeg 获取音频时长
            duration_cmd = [
                'ffmpeg', '-i', file_url,
                '-f', 'null', '-'
            ]
            
            # 获取时长信息
            try:
                result = subprocess.run(
                    ['ffprobe', '-v', 'error', '-show_entries',
                     'format=duration', '-of',
                     'default=noprint_wrappers=1:nokey=1:nokey=1',
                     file_url],
                    capture_output=True, text=True, timeout=30
                )
                total_duration = float(result.stdout.strip())
            except:
                total_duration = None
            
            # 计算切割片段数
            if total_duration:
                num_segments = int(total_duration / chunk_duration) + (1 if total_duration % chunk_duration else 0)
            else:
                num_segments = 1
            
            # 使用 FFmpeg 切割音频
            base_name = os.path.splitext(file_name)[0]
            output_pattern = os.path.join(tmpdir, f'{base_name}_part_%03d.mp3')
            
            split_cmd = [
                'ffmpeg', '-i', file_url,
                '-f', 'segment',
                '-segment_time', str(chunk_duration),
                '-c', 'copy',
                '-segment_format', 'mp3',
                output_pattern
            ]
            
            print(f"切割音频...")
            subprocess.run(split_cmd, capture_output=True, check=True)
            
            # 返回切割后的文件信息
            segments = []
            for i in range(num_segments):
                seg_name = f'{base_name}_part_{i:03d}.mp3'
                seg_path = os.path.join(tmpdir, seg_name)
                if os.path.exists(seg_path):
                    segments.append({
                        "segment_number": i + 1,
                        "file_name": seg_name,
                        "file_path": seg_path
                    })
            
            return jsonify({
                "status": "success",
                "total_segments": len(segments),
                "segments": segments,
                "message": f"成功切割成 {len(segments)} 个片段"
            }), 200
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/health', methods=['GET'])
def health():
    """健康检查端点"""
    return jsonify({"status": "ok"}), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)