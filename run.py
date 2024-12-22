import os
import time
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from pathlib import Path
import google_auth_httplib2
import google_auth_oauthlib
import googleapiclient.discovery
import googleapiclient.errors
import googleapiclient.http
import json

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",  # 플레이리스트 관리 권한 추가
]

@dataclass
class VideoFile:
    """비디오 파일 정보를 담는 데이터 클래스"""
    path: Path
    original_name: str
    
    @property
    def name_without_ext(self) -> str:
        return os.path.splitext(self.original_name)[0]

class YouTubeUploader:
    """YouTube 업로드 관련 기능을 담당하는 클래스"""
    def __init__(self, client_secrets_file: str = "client.json", port: int = 8070):
        self.client_secrets_file = client_secrets_file
        self.port = port
        self.youtube = self._authenticate()
        self._playlists_cache = {}  # 플레이리스트 캐시
    
    def _authenticate(self) -> Any:
        """YouTube API 인��� 수행하고 클라이언트를 반환합니다."""
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
        flow = google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file(
            self.client_secrets_file, SCOPES)
        credentials = flow.run_local_server(port=self.port)
        return googleapiclient.discovery.build("youtube", "v3", credentials=credentials)
    
    def _get_or_create_playlist(self, code: str, config: Dict[str, Any]) -> str:
        """플레이리스트를 가져오거나 생성합니다."""
        playlist_config = config.get('playlist', {})
        if not playlist_config.get('enable'):
            return None
            
        title = playlist_config.get('title', f'Playlist for {code}')
        description = playlist_config.get('description', '')
        code_marker = f"\nCODE: {code}"
        
        # 캐시가 비어있으면 모든 플레이리스트를 가져옴
        if not self._playlists_cache:
            print("플레이리스트 목록을 가져오는 중...")
            request = self.youtube.playlists().list(
                part="snippet,id",
                mine=True,
                maxResults=50
            )
            while request:
                response = request.execute()
                for playlist in response.get('items', []):
                    playlist_desc = playlist['snippet'].get('description', '')
                    # CODE 마커로 플레이리스트 식별
                    if '\nCODE:' in playlist_desc:
                        playlist_code = playlist_desc.split('\nCODE:')[-1].strip()
                        self._playlists_cache[playlist_code] = playlist['id']
                request = self.youtube.playlists().list_next(request, response)
        
        # 캐시에서 코드로 플레이리스트 검색
        if code in self._playlists_cache:
            print(f"기존 플레이리스트 사용 (코드: {code})")
            return self._playlists_cache[code]
        
        # 플레이리스트가 없으면 생성
        try:
            print(f"플레이리스트 생성 중 (코드: {code})")
            playlist = self.youtube.playlists().insert(
                part="snippet,status",
                body={
                    "snippet": {
                        "title": title,
                        "description": f"{description}{code_marker}"
                    },
                    "status": {
                        "privacyStatus": config.get('privacy_status', 'unlisted')
                    }
                }
            ).execute()
            
            playlist_id = playlist['id']
            self._playlists_cache[code] = playlist_id
            print(f"플레이리스트 생성 완료 (ID: {playlist_id})")
            return playlist_id
            
        except Exception as e:
            print(f"플레이리스트 생성 중 오류 발생: {str(e)}")
            raise
    
    def _add_to_playlist(self, playlist_id: str, video_id: str) -> None:
        """비디오를 플레이리스트에 추가합니다."""
        self.youtube.playlistItems().insert(
            part="snippet",
            body={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {
                        "kind": "youtube#video",
                        "videoId": video_id
                    }
                }
            }
        ).execute()

    def _execute_upload(self, request) -> str:
        """업로드를 실행하고 진행상황을 표시합니다."""
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

    def upload(self, video_file: Path, config: Dict[str, Any]) -> str:
        """비디오를 YouTube에 업로드하고 video_id를 반환합니다."""
        try:
            print(f"업로드 준비 중: {video_file}")
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

            video_id = self._execute_upload(request)
            print(f"비디오 ID: {video_id}")
            
            # 플레이리스트 설정이 있으면 처리
            if config.get('playlist', {}).get('enable') and config.get('code'):
                try:
                    print(f"플레이리스트 처리 시작 (코드: {config['code']})")
                    playlist_id = self._get_or_create_playlist(config['code'], config)
                    if playlist_id:
                        self._add_to_playlist(playlist_id, video_id)
                        print(f"비디오가 플레이리스트에 추가되었습니다 (코드: {config['code']})")
                except Exception as playlist_error:
                    print(f"플레이리스트 처리 중 오류 발생: {str(playlist_error)}")
            
            return video_id
            
        except Exception as e:
            print(f"업로드 중 오류 발생: {str(e)}")
            raise
    
    def _prepare_request(self, video_file: Path, config: Dict[str, Any]) -> Dict[str, Any]:
        """비디오 업로드 요청 본문을 생성합니다."""
        original_name = video_file.name[len(config['status_prefix']['uploading']):]
        
        return {
            "snippet": {
                "categoryId": config['category_id'],
                "title": original_name.replace('.mp4', ''),
                "description": config['default_description'],
                "tags": config['default_tags']
            },
            "status": {
                "privacyStatus": config.get('privacy_status', 'private')
            }
        }
    
    def _execute_upload(self, request) -> str:
        """업로드를 실행하고 진행상황을 표시합니다."""
        response = None
        while response is None:
            try:
                status, response = request.next_chunk()
                if status:
                    print(f"Upload {int(status.progress()*100)}%")
            except Exception as e:
                print(f"청크 업로드 중 오류 발���: {str(e)}")
                raise
        return response['id']

class VideoProcessor:
    """비디오 처리 로직을 담당하는 클래스"""
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.upload_folder = Path(config['upload_folder'])
        self.uploader = YouTubeUploader()
        
    def _ensure_directory(self, path: Path) -> None:
        """디렉토리가 없으면 생성합니다."""
        path.mkdir(parents=True, exist_ok=True)
    
    def _get_group_config(self, filename: str) -> Dict[str, Any]:
        """파일명에 따른 그룹 설정을 반환합니다."""
        base_config = self.config.copy()
        
        if 'group_settings' in self.config:
            for code, group_config in self.config['group_settings'].items():
                # regex 패턴이 파일명에 포함되어 있는지 확인
                pattern = group_config.get('regex', '')
                if pattern and pattern in filename:
                    print(f"그룹 설정 적용: {code} (패턴: {pattern})")
                    
                    # 그룹 설정의 변수들을 처리
                    processed_config = {}
                    for key, value in group_config.items():
                        if isinstance(value, str):
                            # {code} 변를 실제 코드값으로 대체
                            try:
                                processed_config[key] = value.format(code=code)
                            except KeyError:
                                # format 실패시 원본값 사용
                                processed_config[key] = value
                        else:
                            processed_config[key] = value
                    
                    # code 값을 설정에 추가
                    processed_config['code'] = code
                    
                    # 기본 설정에 처리된 그룹 설정을 덮어씁니다
                    base_config.update(processed_config)
                    break
        
        # 어떤 설정이 적용되었는지 디버그 출력
        print(f"파일 '{filename}'에 적용된 설정:")
        print(f"- privacy_status: {base_config.get('privacy_status')}")
        print(f"- after_upload_dir: {base_config.get('after_upload_dir')}")
        print(f"- log_file_path: {base_config.get('log_file_path')}")
        print(f"- playlist: {base_config.get('playlist', False)}")
        print(f"- code: {base_config.get('code', '')}")
        
        return base_config
    
    def _move_to_after_upload(self, file_path: Path, config: Dict[str, Any]) -> None:
        """업드 완료된 파일을 after_upload 디렉토리로 이동합니다."""
        if 'after_upload_dir' in config:
            after_upload_dir = Path(config['after_upload_dir'])
            self._ensure_directory(after_upload_dir)
            
            new_path = after_upload_dir / file_path.name
            file_path.rename(new_path)
            print(f"파일 이동됨: {new_path}")
    
    def _write_log(self, video: VideoFile, video_id: str, config: Dict[str, Any]) -> None:
        """로그 파일에 업로드 결과를 기록합니다."""
        log_entry = config['log_template'].format(
            file_name_without_ext=video.name_without_ext,
            url=f"https://youtu.be/{video_id}"
        )
        log_path = Path(config.get('log_file_path', self.config['log_file_path']))
        self._ensure_directory(log_path.parent)
        
        with open(log_path, "a", encoding="utf-8") as log:
            log.write(log_entry + "\n")
    
    def _finish_upload(self, uploading_path: Path, video: VideoFile, video_id: str, config: Dict[str, Any]) -> None:
        """업로드 완료 후 파일명을 변경하고 이동합니다."""
        # 먼저 완료 상태의 파일명을 생성
        done_name = config['output_filename_template'].format(
            original_name=video.name_without_ext,
            video_id=video_id
        )
        done_name = f"{config['status_prefix']['done']}{done_name}"
        
        if 'after_upload_dir' in config:
            # after_upload_dir가 설정되어 있으면 해당 디렉토리로 이동
            after_upload_dir = Path(config['after_upload_dir'])
            self._ensure_directory(after_upload_dir)
            
            # 이동할 경로에서 파일명 변경
            done_path = after_upload_dir / done_name
            uploading_path.rename(done_path)
            print(f"파일 이동됨: {done_path}")
        else:
            # after_upload_dir가 없으면 원래 위치에서 파일명만 변경
            done_path = self.upload_folder / done_name
            uploading_path.rename(done_path)
    
    def process_video(self, video: VideoFile) -> None:
        """단일 비디오 파일을 처리합니다."""
        try:
            # 파일명에 따른 설정을 가져옵니다
            video_config = self._get_group_config(video.original_name)
            
            uploading_path = self._prepare_upload(video)
            video_id = self.uploader.upload(uploading_path, video_config)
            self._finish_upload(uploading_path, video, video_id, video_config)
            self._write_log(video, video_id, video_config)
            print(f"성공적으로 처리됨: {video.original_name}")
            
        except Exception as e:
            print(f"비디오 처리 중 오류 발생: {str(e)}")
            # 업로드 실패 시 원래 상태로 복원
            if 'uploading_path' in locals():
                original_name = f"{self.config['prefix']}{video.original_name}"
                original_path = self.upload_folder / original_name
                try:
                    uploading_path.rename(original_path)
                    print(f"파일 상태 복원됨: {original_path}")
                except Exception as rename_error:
                    print(f"파일 상태 복원 실패: {str(rename_error)}")
            raise
    
    def _prepare_upload(self, video: VideoFile) -> Path:
        """업로드 준비 상태로 파일을 변경합니다."""
        uploading_name = f"{self.config['status_prefix']['uploading']}{video.original_name}"
        uploading_path = self.upload_folder / uploading_name
        video.path.rename(uploading_path)
        return uploading_path
    
    def get_pending_videos(self) -> List[VideoFile]:
        """업로드할 파일 목록을 정렬하여 반환합니다."""
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

def load_config() -> Dict[str, Any]:
    """기본 설정과 사용자 설정을 로드하여 병합된 설정을 반환합니다."""
    with open('config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    if os.path.exists('userConfig.json'):
        with open('userConfig.json', 'r', encoding='utf-8') as f:
            config.update(json.load(f))
    
    return config

def main() -> None:
    """메인 처리 루프를 실행합니다."""
    config = load_config()
    processor = VideoProcessor(config)
    
    while True:
        try:
            for video in processor.get_pending_videos():
                processor.process_video(video)
            time.sleep(config['scan_interval'])
            
        except KeyboardInterrupt:
            print("프로그램을 종료합니다.")
            break
        except Exception as e:
            print(f"에러 발생: {str(e)}")
            time.sleep(config['scan_interval'])

if __name__ == "__main__":
    main()