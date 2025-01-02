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
import pickle
from google.auth.transport.requests import Request

# YouTube API 권한 범위 정의
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",  # 플레이리스트 관리 권한
]

# 타입 정의
class PlaylistConfig(Protocol):
    code: str
    enable: bool
    add_first: bool
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
    """설정 관련을 담당하는 클래스"""
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
    
    def safe_rename(self, src: Path, dst: Path) -> None:
        """안전하게 파일 이동 (이미 존재하는 경우 삭제)"""
        if dst.exists():
            dst.unlink()  # 기존 파일 삭제
        src.rename(dst)
    
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
        current_time = time.time()
        files = []
        
        for f in sorted(self.upload_folder.iterdir()):
            # r_ 로 시작하는 파일만 처리
            if not f.name.startswith(self.config['prefix']):
                continue
                
            # mp4나 md 파일만 처리
            if not (f.name.endswith(".mp4") or f.name.endswith(".md")):
                continue
            
            # 파일 마지막 수정 시간 확인
            modified_time = f.stat().st_mtime
            if current_time - modified_time < 30:  # 30초 이내에 수정된 파일은 제외
                continue
                
            files.append(f)
        
        return [
            VideoFile(
                path=f,
                original_name=f.name[len(self.config['prefix']):]
            ) for f in files
        ]

class YouTubeUploader:
    """YouTube 업로드 관련 기능을 담당하는 클래스"""
    def __init__(self, client_secrets_file: str = "client.json", token_file: str = "token.pickle", port: int = 8070):
        self.client_secrets_file = client_secrets_file
        self.token_file = token_file
        self.port = port
        self.youtube = self._authenticate()
    
    def _authenticate(self) -> Any:
        """YouTube API 인증"""
        credentials = None
        
        # 저장된 토큰이 있는지 확인
        if os.path.exists(self.token_file):
            print("저장된 인증 정보를 불러오는 중...")
            with open(self.token_file, 'rb') as token:
                credentials = pickle.load(token)
        
        # 토큰이 없거나 유효하지 않은 경우
        if not credentials or not credentials.valid:
            if credentials and credentials.expired and credentials.refresh_token:
                print("토큰 갱신 중...")
                credentials.refresh(Request())
            else:
                print("새로운 인증 진행 중...")
                os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
                flow = google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file(
                    self.client_secrets_file, SCOPES)
                credentials = flow.run_local_server(port=self.port)
            
            # 토큰 저장
            with open(self.token_file, 'wb') as token:
                pickle.dump(credentials, token)
        
        return googleapiclient.discovery.build("youtube", "v3", credentials=credentials)
    
    def upload_video(self, video_file: Path, config: Dict[str, Any]) -> str:
        """비디오 업로드 실행"""
        request_body = self._prepare_request(video_file, config)
        media = googleapiclient.http.MediaFileUpload(
            str(video_file), 
            chunksize=-1, 
            resumable=True
        )
        
        # 플레이리스트 설정이 있는지 확인
        playlist_config = config.get('playlist', {})
        if playlist_config.get('enable') and playlist_config.get('code'):
            request_body['snippet']['playlistId'] = playlist_config['code']
        
        request = self.youtube.videos().insert(
            part="snippet,status",
            body=request_body,
            media_body=media
        )

        video_id = self._execute_upload(request)
        
        # 플레이리스트에 추가 (업로드와 동시에 추가되지 않을 경우를 대비)
        if playlist_config.get('enable') and playlist_config.get('code'):
            try:
                self.add_to_playlist(
                    playlist_config['code'],
                    video_id,
                    add_first=playlist_config.get('add_first', False)
                )
            except Exception as e:
                print(f"플레이리스트 추가 중 오류 발생: {str(e)}")
        
        return video_id
    
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
        full_title = os.path.splitext(original_name)[0]
        
        # 제목이 100자를 초과하는 경우 자르기
        video_title = full_title[:100] if len(full_title) > 100 else full_title
        
        # 설명에 전체 제목 포함
        description = f"제목: {full_title}\n\n"
        if config.get('default_description'):
            description += config['default_description']
        
        return {
            "snippet": {
                "title": video_title,
                "description": description,
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
    
    def update_playlist_metadata(self, playlist_id: str, title: str, description: str, privacy_status: str = 'public') -> None:
        """재생목록의 메타데이터를 업데이트"""
        try:
            # 현재 재생목록 정보 조회
            current_playlist = self.youtube.playlists().list(
                part="snippet,status,contentDetails",
                id=playlist_id
            ).execute()
            
            if not current_playlist.get('items'):
                print(f"재생목록을 찾을 수 없음: {playlist_id}")
                return
            
            current_snippet = current_playlist['items'][0]['snippet']
            current_status = current_playlist['items'][0]['status']
            
            # 현재 값과 다른 경우에만 업데이트
            if (current_snippet.get('title') != title or 
                current_snippet.get('description') != description or 
                current_status.get('privacyStatus') != privacy_status):
                
                self.youtube.playlists().update(
                    part="snippet,status",
                    body={
                        "id": playlist_id,
                        "snippet": {
                            "title": title,
                            "description": description
                        },
                        "status": {
                            "privacyStatus": privacy_status
                        }
                    }
                ).execute()
                print(f"재생목록 메타데이터 업데이트됨: {title} (공개 상태: {privacy_status})")
            
            # 재생목록 정렬 방식을 수동으로 설정
            self._set_playlist_order_type(playlist_id)
            
        except Exception as e:
            print(f"재생목록 메타데이터 업데이트 중 오류 발생: {str(e)}")
    
    def _set_playlist_order_type(self, playlist_id: str) -> None:
        """재생목록의 정렬 방식을 수동으로 설정"""
        try:
            self.youtube.playlists().update(
                part="id,localizations",
                body={
                    "id": playlist_id,
                    "localizations": {
                        "": {  # 빈 문자열 키는 기본 설정을 의미
                            "orderType": "manual"
                        }
                    }
                }
            ).execute()
            print("재생목록 정렬 방식이 수동으로 설정되었습니다.")
        except Exception as e:
            print(f"재생목록 정렬 방식 설정 중 오류 발생: {str(e)}")
    
    def get_playlist_items(self, playlist_id: str) -> List[Dict[str, Any]]:
        """재생목록의 모든 영상 정보를 가져옴"""
        playlist_items = []
        next_page_token = None
        
        while True:
            request = self.youtube.playlistItems().list(
                part="snippet",
                playlistId=playlist_id,
                maxResults=50,
                pageToken=next_page_token
            )
            response = request.execute()
            
            for item in response['items']:
                playlist_items.append({
                    'id': item['id'],
                    'videoId': item['snippet']['resourceId']['videoId'],
                    'title': item['snippet']['title'],
                    'description': item['snippet'].get('description', '')
                })
            
            next_page_token = response.get('nextPageToken')
            if not next_page_token:
                break
        
        return playlist_items
    
    def update_video(self, video_id: str, title: str, description: str) -> None:
        """영상의 제목과 설명을 업데이트"""
        # 제목이 100자를 초과하는 경우 자르기
        title = title[:100] if len(title) > 100 else title
        
        self.youtube.videos().update(
            part="snippet",
            body={
                "id": video_id,
                "snippet": {
                    "title": title,
                    "description": description,
                    "categoryId": "22"
                }
            }
        ).execute()

class VideoProcessor:
    """비디오 처리 로직을 담당하는 클래스"""
    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.config = config_manager.config
        self.file_manager = FileManager(self.config)
        self.uploader = YouTubeUploader()
    
    def process_video(self, video: VideoFile) -> None:
        """비디오 파일 처리"""
        try:
            config = self.config_manager.get_group_config(video.original_name)
            
            # 마크다운 파일인 경우
            if video.original_name.endswith('.md'):
                # uploading 상태로 변경
                uploading_path = self.file_manager.prepare_upload(video)
                self._handle_markdown(uploading_path, video, config)
                return
            
            # 영상 파일 처리 (기존 코드)
            uploading_path = self.file_manager.prepare_upload(video)
            video_id = self.uploader.upload_video(uploading_path, config)
            self._write_log(video, video_id, config)
            self.file_manager.finish_upload(uploading_path, video, video_id, config)
            
        except Exception as e:
            print(f"비디오 처리 중 오류 발생: {str(e)}")
            # 실패 시 원래 상태로 복구
            if 'uploading_path' in locals():
                uploading_path.rename(video.path)
            raise
    
    def _handle_playlist(self, config: Dict[str, Any], video_id: str) -> None:
        """플레이리스트 처리"""
        playlist_config = config.get('playlist', {})
        if playlist_config.get('enable') and playlist_config.get('code'):
            try:
                playlist_id = playlist_config['code']
                print(f"플레이리스트에 추가 중 (코드: {playlist_id})")
                
                # 재생목록 메타데이터 업데이트
                self.uploader.update_playlist_metadata(
                    playlist_id,
                    playlist_config.get('title', ''),
                    playlist_config.get('description', ''),
                    playlist_config.get('privacy_status', 'public')
                )
                
                # 비디오 추가
                add_first = playlist_config.get('add_first', False)
                self.uploader.add_to_playlist(
                    playlist_id,
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
    
    def _handle_markdown(self, uploading_path: Path, video: VideoFile, config: Dict[str, Any]) -> None:
        """마크다운 파일 처리"""
        try:
            # 파일명에서 그룹 코드 추출 (예: p_13900.md -> p_13900)
            group_code = os.path.splitext(video.original_name)[0]
            
            # 해당 그룹의 설정이 있는지 확인
            if group_code in self.config_manager.config.get('group_settings', {}):
                group_config = self.config_manager.config['group_settings'][group_code]
                target_dir = Path(group_config['after_upload_dir'].format(code=group_code))
                log_file_path = group_config['log_file_path'].format(code=group_code)
                
                # 재생목록 설정이 있는 경우
                if 'playlist' in group_config and group_config['playlist'].get('enable'):
                    self._update_playlist_videos(uploading_path, group_config['playlist']['code'], log_file_path)
            else:
                # 기본 설정 사용
                target_dir = Path(config.get('after_upload_dir', self.file_manager.upload_folder))
                log_file_path = config.get('log_file_path')
            
            # 파재 시간을 파일명에 추가
            from datetime import datetime
            timestamp = datetime.now().strftime('___%Y_%m_%d__%H_%M_%S')
            name_without_ext = os.path.splitext(video.original_name)[0]
            ext = os.path.splitext(video.original_name)[1]
            new_filename = f"{config['status_prefix']['done']}{name_without_ext}{timestamp}{ext}"
            
            # 파일 이동
            self.file_manager.ensure_directory(target_dir)
            done_path = target_dir / new_filename
            self.file_manager.safe_rename(uploading_path, done_path)
            print(f"파일 이동됨: {done_path}")
            
        except Exception as e:
            print(f"마크다운 파일 처리 중 오류 발생: {str(e)}")
            raise
    
    def _update_playlist_videos(self, md_file: Path, playlist_id: str, log_file_path: str) -> None:
        """마크다운 파일의 내용을 기반으로 재생목록 영상들을 업데이트"""
        try:
            # 마크다운 파일 읽기
            with open(md_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 재생목록의 모든 영상 가져오기
            playlist_items = self.uploader.get_playlist_items(playlist_id)
            
            # 재생목록의 video_id 목록 생성
            playlist_video_ids = {item['videoId'] for item in playlist_items}
            
            # 변경 사항 추적
            changes = []
            youtube_links = []  # v2 버전용 유튜브 링크 저장
            
            # 마크다운에서 영상 정보 추출 및 업데이트
            import re
            
            # 두 가지 패턴 모두 매칭
            patterns = [
                r'\[([^\]]+)\]\(https://youtu\.be/([a-zA-Z0-9_-]+)\)',  # 직접 유튜브 링크
                r'\[([^\]]+)\]\(https://goto\.slog\.gg/youtube/[^/]+/(-?\d+)\)'  # goto.slog.gg 링크
            ]
            
            def normalize_title(title: str) -> str:
                return title
            
            for pattern in patterns:
                matches = re.finditer(pattern, content)
                for match in matches:
                    md_title = normalize_title(match.group(1))
                    
                    # goto.slog.gg 링크의 경우 position으로 videoId 찾기
                    if 'goto.slog.gg' in match.group(0):
                        position = int(match.group(2))
                        
                        if position < 0:
                            position = len(playlist_items) + position
                        else:
                            position = position - 1
                        
                        if position < len(playlist_items):
                            video_id = playlist_items[position]['videoId']
                            # v2 버전용 유튜브 링크 저장
                            youtube_links.append({
                                'original': match.group(0),
                                'youtube': f'[{md_title}](https://youtu.be/{video_id})'
                            })
                    else:
                        video_id = match.group(2)
                        youtube_links.append({
                            'original': match.group(0),
                            'youtube': match.group(0)  # 이미 유튜브 링크면 그대로 유지
                        })
                    
                    # 해당 영상이 재생목록에 있는지 확인
                    if video_id not in playlist_video_ids:
                        continue
                    
                    # 해당 영상 찾기
                    video_item = next((item for item in playlist_items if item['videoId'] == video_id), None)
                    
                    if video_item:
                        youtube_title = normalize_title(video_item['title'])
                        if youtube_title != md_title:
                            # 변경 사항 기록
                            changes.append({
                                'video_id': video_id,
                                'old_title': video_item['title'],
                                'new_title': match.group(1)  # 원본 마크다운 제목 사용
                            })
                            
                            # 영상 업데이트
                            self.uploader.update_video(video_id, match.group(1), f"제목: {match.group(1)}\n\n로그\n{video_item.get('description', '')}")
            
            # 변경 사항 로깅 (원본 버전)
            if changes:
                self._write_log_entries(changes, log_file_path)
                
                # v2 버전 로그 파일 생성
                v2_log_path = self._get_v2_log_path(log_file_path)
                
                # v2 버전 콘텐츠 생성
                v2_content = content
                for link in youtube_links:
                    v2_content = v2_content.replace(link['original'], link['youtube'])
                
                # v2 버전 저장
                with open(v2_log_path, 'w', encoding='utf-8') as f:
                    f.write(v2_content)
                
                print(f"v2 버전 로그가 생성됨: {v2_log_path}")
            
        except Exception as e:
            print(f"재생목록 영상 업데이트 중 오류 발생: {str(e)}")
            raise

    def _write_log_entries(self, changes: List[Dict[str, str]], log_file_path: str) -> None:
        """로그 항목들을 파일에 작성"""
        from datetime import datetime
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        log_entries = [
            f"{timestamp} - 총 {len(changes)}개 영상 제목 변경됨:"
        ]
        
        for change in changes:
            log_entries.append(
                f"({change['video_id']}): {change['old_title']} --(변경)--> {change['new_title']}"
            )
        
        log_path = Path(log_file_path)
        self.file_manager.ensure_directory(log_path.parent)
        
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write('\n'.join(log_entries) + '\n\n')
        
        print(f"변경사항이 로그에 기록됨: {log_file_path}")

    def _get_v2_log_path(self, original_path: str) -> str:
        """v2 버전 로그 파일 경로 생성"""
        path = Path(original_path)
        # 경로를 POSIX 스타일(/)로 통일
        return str(path.parent / f"{path.stem}_v2{path.suffix}").replace('\\', '/')

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