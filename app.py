from flask import Flask, request, jsonify
import subprocess
import os
import requests
from google.oauth2.service_account import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import json
import traceback
from pathlib import Path
import sys

# 強制輸出不緩衝
sys.stdout.flush()
os.environ['PYTHONUNBUFFERED'] = '1'

app = Flask(__name__)

# Google Drive API 凭证(从环境变量读取)
GOOGLE_CREDENTIALS_JSON = os.getenv('GOOGLE_CREDENTIALS_JSON', '{}')

def get_drive_service():
    """获取 Google Drive API 服务"""
    try:
        credentials_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
        credentials = Credentials.from_service_account_info(
            credentials_dict,
            scopes=['https://www.googleapis.com/auth/drive']
        )
        return build('drive', 'v3', credentials=credentials)
    except Exception as e:
        print(f"[ERROR] Error creating Drive service: {e}", flush=True)
        return None

def upload_to_google_drive(file_path, folder_id, file_name):
    """
    上传文件到 Google Drive
    
    Args:
        file_path: 本地文件路径
        folder_id: Google Drive 文件夹 ID
        file_name: 上传后的文件名
    
    Returns:
        file_id: 上传后的文件 ID,或 None 如果上传失败
    """
    try:
        service = get_drive_service()
        if not service:
            print(f"[ERROR] Failed to get Drive service", flush=True)
            return None
        
        file_metadata = {
            'name': file_name,
            'parents': [folder_id]
        }
        
        media = MediaFileUpload(file_path, mimetype='audio/mpeg')
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id'
        ).execute()
        
        return file.get('id')
    except Exception as e:
        print(f"[ERROR] Error uploading to Google Drive: {e}", flush=True)
        traceback.print_exc()
        return None

def download_file_from_url(url, output_path):
    """从 URL 下载文件"""
    try:
        response = requests.get(url, timeout=300)
        response.raise_for_status()
        
        with open(output_path, 'wb') as f:
            f.write(response.content)
        
        return True
    except Exception as e:
        print(f"[ERROR] Error downloading file: {e}", flush=True)
        return False

@app.route('/split-audio', methods=['POST'])
def split_audio():
    """
    切割音档并上传到 Google Drive
    
    期望的 JSON 数据:
    {
        "file_url": "Google Drive 文件下载链接",
        "file_name": "原始文件名",
        "chunk_duration_minutes": 10,
        "folder_id": "04_audio_chunks 文件夹 ID"
    }
    """
    print("[DEBUG] 進入 split_audio 函數", flush=True)
    
    try:
        # 檢查 JSON 是否為空
        data = request.get_json(force=True, silent=False)
        
        if data is None:
            print("[ERROR] JSON 解析失敗，data 為 None", flush=True)
            return jsonify({
                'status': 'error',
                'message': 'JSON 解析失敗，請確保 Content-Type 是 application/json'
            }), 400
        
        print(f"[DEBUG] 收到的 JSON 數據: {data}", flush=True)
        
        file_url = data.get('file_url')
        file_name = data.get('file_name', 'audio.mp3')
        
        # 確保 chunk_duration 是整數
        chunk_duration = data.get('chunk_duration_minutes', 10)
        try:
            chunk_duration = int(chunk_duration)
        except (ValueError, TypeError):
            print(f"[WARNING] chunk_duration 無效: {chunk_duration}，使用默認值 10", flush=True)
            chunk_duration = 10
        
        folder_id = data.get('folder_id') or os.getenv('GOOGLE_DRIVE_CHUNKS_FOLDER')
        
        print(f"[DEBUG] file_url: {file_url}", flush=True)
        print(f"[DEBUG] file_name: {file_name}", flush=True)
        print(f"[DEBUG] chunk_duration (int): {chunk_duration}", flush=True)
        print(f"[DEBUG] folder_id: {folder_id}", flush=True)
        
        if not file_url or not folder_id:
            print(f"[ERROR] 缺少必要參數", flush=True)
            return jsonify({
                'status': 'error',
                'message': '缺少必要参数: file_url 或 folder_id'
            }), 400
        
        # 创建临时目录
        temp_dir = f'/tmp/{os.urandom(8).hex()}'
        os.makedirs(temp_dir, exist_ok=True)
        print(f"[DEBUG] 創建臨時目錄: {temp_dir}", flush=True)
        
        try:
            # 1. 下载文件
            input_file = os.path.join(temp_dir, file_name)
            print(f"[DEBUG] 下载文件: {file_url}", flush=True)
            
            if not download_file_from_url(file_url, input_file):
                print(f"[ERROR] 下載文件失敗", flush=True)
                return jsonify({
                    'status': 'error',
                    'message': '下载文件失败'
                }), 400
            
            file_size = os.path.getsize(input_file)
            print(f"[DEBUG] 下载完成,文件大小: {file_size} bytes", flush=True)
            
            # 2. 用 FFmpeg 切割
            output_dir = os.path.join(temp_dir, 'chunks')
            os.makedirs(output_dir, exist_ok=True)
            
            # 生成输出文件名前缀(去掉扩展名)
            file_base = os.path.splitext(file_name)[0]
            output_pattern = os.path.join(output_dir, f'{file_base}_part_%03d.mp3')
            
            # FFmpeg 命令 - 直接複製，不重新編碼
            segment_seconds = chunk_duration * 60
            print(f"[DEBUG] segment_seconds: {segment_seconds} (type: {type(segment_seconds).__name__})", flush=True)
            
            cmd = [
                'ffmpeg',
                '-i', input_file,
                '-f', 'segment',
                '-segment_time', str(segment_seconds),
                '-c', 'copy',
                output_pattern
            ]
            
            print(f"[DEBUG] 执行 FFmpeg 切割命令...", flush=True)
            print(f"[DEBUG] 命令: {' '.join(cmd)}", flush=True)
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                print(f"[ERROR] FFmpeg stderr: {result.stderr}", flush=True)
                print(f"[DEBUG] FFmpeg stdout: {result.stdout}", flush=True)
            
            # 3. 检查切割结果
            chunks = sorted([f for f in os.listdir(output_dir) if f.endswith('.mp3')])
            print(f"[DEBUG] 切割成功,生成了 {len(chunks)} 个文件", flush=True)
            
            if not chunks:
                print(f"[ERROR] 沒有生成切割文件", flush=True)
                return jsonify({
                    'status': 'error',
                    'message': '没有生成切割文件'
                }), 400
            
            # 4. 上传所有切割后的文件到 Google Drive
            uploaded_files = []
            
            print(f"[DEBUG] 开始上传 {len(chunks)} 个文件到 Google Drive...", flush=True)
            
            for i, chunk_file in enumerate(chunks):
                chunk_path = os.path.join(output_dir, chunk_file)
                
                file_id = upload_to_google_drive(
                    chunk_path,
                    folder_id,
                    chunk_file
                )
                
                if file_id:
                    uploaded_files.append({
                        'file_name': chunk_file,
                        'file_id': file_id,
                        'segment_number': i + 1
                    })
                    print(f"[DEBUG] ✓ 上传成功 #{i+1}: {chunk_file}", flush=True)
                else:
                    print(f"[ERROR] ✗ 上传失败 #{i+1}: {chunk_file}", flush=True)
            
            print(f"[DEBUG] 完成！上傳了 {len(uploaded_files)} 個文件", flush=True)
            
            return jsonify({
                'status': 'success',
                'message': f'成功切割并上传了 {len(uploaded_files)} 个片段',
                'total_segments': len(uploaded_files),
                'segments': uploaded_files,
                'folder_id': folder_id
            }), 200
        
        finally:
            # 清理临时文件
            print(f"[DEBUG] 清理臨時文件: {temp_dir}", flush=True)
            subprocess.run(['rm', '-rf', temp_dir], capture_output=True)
    
    except Exception as e:
        print(f"[ERROR] split_audio 異常: {str(e)}", flush=True)
        traceback.print_exc()
        return jsonify({
            'status': 'error',
            'message': f'服务器错误: {str(e)}'
        }), 500

@app.route('/health', methods=['GET'])
def health():
    """健康检查端点"""
    return jsonify({'status': 'ok', 'ffmpeg': 'available'}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)), debug=False)
關鍵修改總結
