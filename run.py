import os
import time
from dataclasses import dataclass
from typing import Dict, Any, Optional, Protocol, List
from pathlib import Path
import google_auth_oauthlib
import googleapiclient.discovery
import googleapiclient.errors
import googleapiclient.http
import json

# YouTube API 권한 범위 정의
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",  # 플레이리스트 관리 권한
]

# 타입 정의
class PlaylistConfig(Protocol):
    code: str
    enable: bool
    addFirst: bool
    title: str
    description: str

class GroupConfig(Protocol):
    regex: str
    after_upload_dir: str
    privacy_status: str
    log_template: str
    log_file_path: str
    playlist: Optional[PlaylistConfig]

@dataclass
class VideoFile:
    """비디오 파일 정보를 담는 데이터 클래스"""
    path: Path
    original_name: str
    
    @property
    def name_without_ext(self) -> str:
        return os.path.splitext(self.original_name)[0]

class ConfigManager:
    """설정 관리를 담당하는 클래스"""
    def __init__(self, config_path: str = 'config.json', user_config_path: str = 'userConfig.json'):
        self.config = self._load_config(config_path, user_config_path)
        
    def _load_config(self, config_path: str, user_config_path: str) -> Dict[str, Any]:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        if os.path.exists(user_config_path):
            with open(user_config_path, 'r', encoding='utf-8') as f:
                config.update(json.load(f))
        
        return config
    
    def get_group_config(self, filename: str) -> Dict[str, Any]:
        """파일명에 따 그룹 설정을 반환"""
        base_config = self.config.copy()
        
        if 'group_settings' in self.config:
            for code, group_config in self.config['group_settings'].items():
                if group_config.get('regex', '') in filename:
                    processed_config = self._process_group_config(group_config, code)
                    base_config.update(processed_config)
                    break
        
        return base_config
    
    def _process_group_config(self, group_config: Dict[str, Any], code: str) -> Dict[str, Any]:
        """그룹 설정의 변수들을 처리"""
        processed_config = {}
        for key, value in group_config.items():
            if isinstance(value, str):
                try:
                    processed_config[key] = value.format(code=code)
                except KeyError:
                    processed_config[key] = value
            else:
                processed_config[key] = value
        processed_config['code'] = code
        return processed_config

class FileManager:
    """파일 처리를 담당하는 클래스"""
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.upload_folder = Path(config['upload_folder'])
    
    def ensure_directory(self, path: Path) -> None:
        """디렉토리가 없으면 생성"""
        path.mkdir(parents=True, exist_ok=True)
    
    def prepare_upload(self, video: VideoFile) -> Path:
        """업로드 준비 상태로 파일을 변경"""
        uploading_name = f"{self.config['status_prefix']['uploading']}{video.original_name}"
        uploading_path = self.upload_folder / uploading_name
        video.path.rename(uploading_path)
        return uploading_path
    
    def finish_upload(self, uploading_path: Path, video: VideoFile, video_id: str, config: Dict[str, Any]) -> None:
        """업로드 완료 후 파일 처리"""
        done_name = self._create_done_filename(video, video_id, config)
        target_dir = Path(config.get('after_upload_dir', self.upload_folder))
        self.ensure_directory(target_dir)
        done_path = target_dir / done_name
        uploading_path.rename(done_path)
        print(f"파일 이동됨: {done_path}")
    
    def _create_done_filename(self, video: VideoFile, video_id: str, config: Dict[str, Any]) -> str:
        """완료된 파일의 이름 생성"""
        base_name = config['output_filename_template'].format(
            original_name=video.name_without_ext,
            video_id=video_id
        )
        return f"{config['status_prefix']['done']}{base_name}"
    
    def restore_original(self, uploading_path: Path, video: VideoFile) -> None:
        """실패 시 원래 상태로 복원"""
        original_name = f"{self.config['prefix']}{video.original_name}"
        original_path = self.upload_folder / original_name
        uploading_path.rename(original_path)
        print(f"파일 상태 복원됨: {original_path}")
    
    def get_pending_videos(self) -> List[VideoFile]:
        """업로드할 파일 목록을 정렬하여 반환"""
        files = sorted([
            f for f in self.upload_folder.iterdir()
            if f.name.startswith(self.config['prefix']) and f.name.endswith(".mp4")
        ])
        return [
            VideoFile(
                path=f,
                original_name=f.name[len(self.config['prefix']):]
            ) for f in files
        ]

class YouTubeUploader:
    """YouTube 업로드 관련 기능을 담당하는 클래스"""
    def __init__(self, client_secrets_file: str = "client.json", port: int = 8070):
        self.client_secrets_file = client_secrets_file
        self.port = port
        self.youtube = self._authenticate()
    
    def _authenticate(self) -> Any:
        """YouTube API 인증"""
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
        flow = google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file(
            self.client_secrets_file, SCOPES)
        credentials = flow.run_local_server(port=self.port)
        return googleapiclient.discovery.build("youtube", "v3", credentials=credentials)
    
    def upload_video(self, video_file: Path, config: Dict[str, Any]) -> str:
        """비디오 업로드 실행"""
        request_body = self._prepare_request(video_file, config)
        media = googleapiclient.http.MediaFileUpload(
            str(video_file), 
            chunksize=-1, 
            resumable=True
        )
        
        request = self.youtube.videos().insert(
            part="snippet,status",
            body=request_body,
            media_body=media
        )

        return self._execute_upload(request)
    
    def add_to_playlist(self, playlist_id: str, video_id: str, add_first: bool = False) -> None:
        """플레이리스트에 비디오 추가"""
        body = {
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {
                    "kind": "youtube#video",
                    "videoId": video_id
                }
            }
        }
        
        if add_first:
            body["snippet"]["position"] = 0
        
        self.youtube.playlistItems().insert(
            part="snippet",
            body=body
        ).execute()
    
    def _prepare_request(self, video_file: Path, config: Dict[str, Any]) -> Dict[str, Any]:
        """업로드 요청 본문을 준비"""
        # 접두어(u_)를 제거한 실제 파일명 추출
        original_name = video_file.name[len(config['status_prefix']['uploading']):]
        video_title = os.path.splitext(original_name)[0]
        
        return {
            "snippet": {
                "title": video_title,  # 접두어가 제거된 제목 사용
                "description": config.get('default_description', ''),
                "tags": config.get('default_tags', []),
                "categoryId": str(config.get('category_id', '22'))
            },
            "status": {
                "privacyStatus": config.get('privacy_status', 'private'),
                "selfDeclaredMadeForKids": False
            }
        }
    
    def _execute_upload(self, request: Any) -> str:
        """업로드 실행 및 진행률 표시"""
        print("비디오 업로드 시작...")
        response = None
        while response is None:
            try:
                status, response = request.next_chunk()
                if status:
                    print(f"Upload {int(status.progress() * 100)}%")
            except Exception as e:
                print(f"청크 업로드 중 오류 발생: {str(e)}")
                raise
        print("비디오 업로드 완료")
        return response['id']

class VideoProcessor:
    """비디오 처리 로직을 담당하는 클래스"""
    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.config = config_manager.config
        self.file_manager = FileManager(self.config)
        self.uploader = YouTubeUploader()
    
    def process_video(self, video: VideoFile) -> None:
        """단일 비디오 파일을 처리"""
        uploading_path = None
        try:
            video_config = self.config_manager.get_group_config(video.original_name)
            uploading_path = self.file_manager.prepare_upload(video)
            
            video_id = self.uploader.upload_video(uploading_path, video_config)
            print(f"비디오 ID: {video_id}")
            
            self._handle_playlist(video_config, video_id)
            self.file_manager.finish_upload(uploading_path, video, video_id, video_config)
            self._write_log(video, video_id, video_config)
            
            print(f"성공적으로 처리됨: {video.original_name}")
            
        except Exception as e:
            print(f"비디오 처리 중 오류 발생: {str(e)}")
            if uploading_path:
                try:
                    self.file_manager.restore_original(uploading_path, video)
                except Exception as rename_error:
                    print(f"파일 상태 복원 실패: {str(rename_error)}")
            raise
    
    def _handle_playlist(self, config: Dict[str, Any], video_id: str) -> None:
        """플레이리스트 처리"""
        playlist_config = config.get('playlist', {})
        if playlist_config.get('enable') and playlist_config.get('code'):
            try:
                print(f"플레이리스트에 추가 중 (코드: {playlist_config['code']})")
                add_first = playlist_config.get('addFirst', False)
                self.uploader.add_to_playlist(
                    playlist_config['code'],
                    video_id,
                    add_first=add_first
                )
                position_str = "맨 앞" if add_first else "맨 뒤"
                print(f"비디오가 플레이리스트의 {position_str}에 추가되었습니다")
            except Exception as playlist_error:
                print(f"플레이리스트 처리 중 오류 발생: {str(playlist_error)}")
    
    def get_pending_videos(self) -> List[VideoFile]:
        """업로드할 파일 목록을 반환"""
        return self.file_manager.get_pending_videos()
    
    def _write_log(self, video: VideoFile, video_id: str, config: Dict[str, Any]) -> None:
        """업로드 로그를 작성"""
        try:
            log_file_path = config.get('log_file_path')
            if not log_file_path:
                return
            
            log_template = config.get('log_template', '- [{file_name_without_ext}]({url})')
            video_url = f"https://youtu.be/{video_id}"
            
            log_entry = log_template.format(
                file_name_without_ext=video.name_without_ext,
                url=video_url,
                video_id=video_id
            )
            
            log_path = Path(log_file_path)
            self.file_manager.ensure_directory(log_path.parent)
            
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(f"{log_entry}\n")
                
            print(f"로그가 작성됨: {log_file_path}")
            
        except Exception as log_error:
            print(f"로그 작성 중 오류 발생: {str(log_error)}")

def main() -> None:
    """메인 처리 루프를 실행"""
    config_manager = ConfigManager()
    processor = VideoProcessor(config_manager)
    
    try:
        while True:
            try:
                for video in processor.get_pending_videos():
                    processor.process_video(video)
                time.sleep(config_manager.config['scan_interval'])
                
            except KeyboardInterrupt:
                print("\n프로그램을 종료합니다...")
                break
            except Exception as e:
                print(f"에러 발생: {str(e)}")
                time.sleep(config_manager.config['scan_interval'])
    finally:
        print("프로그램이 종료되었습니다.")

if __name__ == "__main__":
    main()