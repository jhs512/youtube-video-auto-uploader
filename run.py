import os
import time
import google_auth_httplib2
import google_auth_oauthlib
import googleapiclient.discovery
import googleapiclient.errors
import googleapiclient.http
import json

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

def authenticate_youtube():
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

    # Load client secrets file, put the path of your file
    client_secrets_file = "client.json"
    
    flow = google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file(
        client_secrets_file, SCOPES)
    credentials = flow.run_local_server(port = 8070)

    youtube = googleapiclient.discovery.build(
        "youtube", "v3", credentials=credentials)

    return youtube

def load_config():
    with open('config.json', 'r', encoding='utf-8') as f:
        return json.load(f)

def upload_video(youtube, file_path, config):
    file_name = os.path.basename(file_path)
    file_name_without_ext = os.path.splitext(file_name)[0]
    
    request_body = {
        "snippet": {
            "categoryId": config['category_id'],
            "title": file_name,
            "description": config['default_description'],
            "tags": config['default_tags']
        },
        "status":{
            "privacyStatus": config.get('privacy_status', 'private')
        }
    }

    request = youtube.videos().insert(
        part="snippet,status",
        body=request_body,
        media_body=googleapiclient.http.MediaFileUpload(file_path, chunksize=-1, resumable=True)
    )

    response = None 
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"Upload {int(status.progress()*100)}%")

    return response['id']

def process_videos():
    config = load_config()
    youtube = authenticate_youtube()
    
    while True:
        try:
            for filename in os.listdir(config['upload_folder']):
                if filename.startswith(config['prefix']) and filename.endswith(".mp4"):
                    file_path = os.path.join(config['upload_folder'], filename)
                    original_name = filename[len(config['prefix']):]  # prefix를 제외한 원본 파일명
                    
                    # uploading 상태로 파일명 변경
                    uploading_name = f"{config['status_prefix']['uploading']}{original_name}"
                    uploading_path = os.path.join(config['upload_folder'], uploading_name)
                    os.rename(file_path, uploading_path)
                    
                    # 업로드
                    video_id = upload_video(youtube, uploading_path, config)
                    
                    # 완료 후 파일명 변경 (템플릿 사용)
                    done_name = config['output_filename_template'].format(
                        original_name=original_name.replace('.mp4', ''),
                        video_id=video_id
                    )
                    done_name = f"{config['status_prefix']['done']}{done_name}"
                    done_path = os.path.join(config['upload_folder'], done_name)
                    os.rename(uploading_path, done_path)
                    
                    # 로그 기록
                    log_entry = config['log_template'].format(
                        file_name_without_ext=original_name[:-4],
                        url=f"https://youtu.be/{video_id}"
                    )
                    with open("upload.log", "a", encoding="utf-8") as log:
                        log.write(log_entry + "\n")
                    
                    print(f"성공적으로 처리됨: {filename}")
            
            time.sleep(config['scan_interval'])
            
        except KeyboardInterrupt:
            print("프로그램을 종료합니다.")
            break
        except Exception as e:
            print(f"에러 발생: {str(e)}")
            time.sleep(config['scan_interval'])

if __name__ == "__main__":
    process_videos()