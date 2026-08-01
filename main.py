"""
Netease Music Enhanced Plugin for AstrBot
- Author: BB0813 (based on NachoCrazy)
- Repo: https://github.com/bb0813/astrbot-plugin-neteasemusic
- Backend: Netease-CDN-Bypass
- QQ Official: download audio -> File(file_type=4) / Record(silk) via AstrBot adapter
- Third-party: Record voice
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.parse
from typing import Any, Dict, List, Optional

import aiohttp

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import File, Image, Plain, Record
from astrbot.core.message.message_event_result import MessageChain

# ---------------------------------------------------------------------------
# 诊断日志（便于排查：消息是否到达插件、搜索是否成功、发送走哪条分支）
# ---------------------------------------------------------------------------
def _log(msg: str, level: str = "info"):
    """Unified plugin log line with a fixed prefix so we can grep it in logs."""
    text = f"[NeteaseMusicPlugin] {msg}"
    getattr(logger, level)(text)
    # 同时打到 stdout，保证一定可见
    print(f"{time.strftime('%H:%M:%S')} {text}", flush=True)


def _find_ffmpeg() -> Optional[str]:
    path = shutil.which("ffmpeg")
    if path:
        return path
    candidates = [
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
        r"C:\Users\Administrator\ffmpeg\bin\ffmpeg.exe",
        r"C:\Windows\ffmpeg\bin\ffmpeg.exe",
        os.path.join(os.getcwd(), "ffmpeg", "bin", "ffmpeg.exe"),
    ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    try:
        proc = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        if proc.returncode == 0:
            return "ffmpeg"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _normalize_message(text: str) -> str:
    """Strip @mentions / leading slashes so command matching works everywhere."""
    if not text:
        return ""
    text = re.sub(r"@\S+\s*", "", text)
    text = re.sub(r"^[/／!\s]+", "", text)
    return text.strip()


def _safe_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "song"
    return name[:80]


class NeteaseMusicAPI:
    """Wrapper for Netease-CDN-Bypass."""

    def __init__(self, api_url: str, session: aiohttp.ClientSession):
        self.base_url = api_url.rstrip("/")
        self.session = session

    async def search_songs(self, keyword: str, limit: int) -> List[Dict[str, Any]]:
        url = (
            f"{self.base_url}/search"
            f"?keywords={urllib.parse.quote(keyword)}&limit={limit}&type=1"
        )
        async with self.session.get(url) as r:
            r.raise_for_status()
            data = await r.json(content_type=None)

        raw_songs = data.get("result", {}).get("songs") or data.get("songs") or []
        normalized: List[Dict[str, Any]] = []
        for song in raw_songs:
            artists = song.get("artists") or song.get("ar") or []
            album = song.get("album") or song.get("al") or {}
            duration = song.get("duration")
            if duration is None:
                duration = song.get("dt", 0)
            if not isinstance(album, dict):
                album = {"name": str(album)}
            normalized.append(
                {
                    "id": song.get("id"),
                    "name": song.get("name", "未知歌曲"),
                    "artists": artists if isinstance(artists, list) else [],
                    "album": album,
                    "duration": duration or 0,
                    "fee": song.get("fee", 0),
                }
            )
        return normalized

    async def get_song_details(self, song_id: int) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/song/detail?ids={song_id}"
        async with self.session.get(url) as r:
            r.raise_for_status()
            data = await r.json(content_type=None)
        songs = data.get("songs") or data.get("data") or []
        return songs[0] if songs else None

    def build_proxy_url(self, song_id: int, quality: str) -> str:
        """CDN Bypass multiplies br by 1000, so pass kbps (320/192/128)."""
        br = self._quality_to_br(quality)
        return f"{self.base_url}/song/proxy?id={song_id}&br={br}"

    def build_outer_proxy_url(self, song_id: int) -> str:
        """Fallback: route Netease's public outer-play URL through /proxy."""
        outer = f"https://music.163.com/song/media/outer/url?id={song_id}.mp3"
        return f"{self.base_url}/proxy?url={urllib.parse.quote(outer, safe='')}"

    def _quality_to_br(self, quality: str) -> int:
        mapping = {"lossless": 999, "exhigh": 320, "higher": 192, "standard": 128}
        return mapping.get(quality.lower(), 320)

    async def _fetch_audio_to_file(self, url: str, path: str) -> bool:
        """Download one audio URL to path. Returns True on success."""
        try:
            async with self.session.get(url) as r:
                if r.status != 200:
                    _log(f"audio HTTP {r.status} for {url}", "warning")
                    return False

                content_type = (r.headers.get("Content-Type") or "").lower()
                if "json" in content_type or "text/html" in content_type:
                    body = await r.text()
                    _log(f"non-audio response: {body[:160]}", "warning")
                    return False

                with open(path, "wb") as f:
                    async for chunk in r.content.iter_chunked(64 * 1024):
                        f.write(chunk)
        except Exception as e:
            _log(f"fetch audio failed: {e!s}", "warning")
            return False

        size = os.path.getsize(path) if os.path.exists(path) else 0
        if size < 10240:  # 小于 10KB 基本是错误页/试听残片
            _log(f"audio too small ({size} bytes)", "warning")
            try:
                os.remove(path)
            except OSError:
                pass
            return False

        _log(f"audio saved {path} ({size} bytes)")
        return True

    async def download_audio(self, song_id: int, quality: str) -> Optional[str]:
        """
        Download audio to a local temp mp3, trying multiple sources:
        1. /song/proxy at requested quality
        2. /song/proxy at lower bitrates (VIP songs sometimes allow lower)
        3. /proxy with Netease public outer-play URL
        Local file is required for QQ Official upload (base64 file_data).
        """
        tmp_dir = tempfile.gettempdir()
        path = os.path.join(tmp_dir, f"netease_{song_id}_{int(time.time() * 1000)}.mp3")

        # 依次降级尝试的码率
        primary = self._quality_to_br(quality)
        candidates = [primary] + [b for b in (320, 192, 128) if b != primary]

        for br in candidates:
            url = f"{self.base_url}/song/proxy?id={song_id}&br={br}"
            _log(f"try /song/proxy br={br}")
            if await self._fetch_audio_to_file(url, path):
                return path

        # 最后回退：网易公开外链走 /proxy 转发
        outer_url = self.build_outer_proxy_url(song_id)
        _log("try /proxy with outer url")
        if await self._fetch_audio_to_file(outer_url, path):
            return path

        _log(f"all audio sources failed for {song_id}", "error")
        return None

    async def download_image(self, url: str) -> Optional[bytes]:
        if not url:
            return None
        try:
            async with self.session.get(url) as r:
                if r.status == 200:
                    return await r.read()
        except Exception as e:
            _log(f"cover download failed: {e!s}", "warning")
        return None


class Main(star.Star):
    def __init__(self, context, config: Optional[Dict[str, Any]] = None):
        super().__init__(context)
        self.config = config or {}
        self.config.setdefault("api_url", "https://meting.binbim.top:3002")
        self.config.setdefault("quality", "exhigh")
        self.config.setdefault("search_limit", 5)

        self.waiting_users: Dict[str, Dict[str, Any]] = {}
        self.song_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._temp_files: List[str] = []

        self.http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60))
        self.api = NeteaseMusicAPI(self.config["api_url"], self.http_session)

        self._ffmpeg_path = _find_ffmpeg()
        if self._ffmpeg_path:
            ffmpeg_dir = os.path.dirname(self._ffmpeg_path)
            current_path = os.environ.get("PATH", "")
            if ffmpeg_dir and ffmpeg_dir not in current_path:
                os.environ["PATH"] = ffmpeg_dir + os.pathsep + current_path
                _log(f"Added ffmpeg to PATH: {ffmpeg_dir}")
        else:
            _log("ffmpeg not found. silk/Record may fail.", "warning")

        self.cleanup_task: Optional[asyncio.Task] = None
        _log(
            f"initialized. api_url={self.config['api_url']}, quality={self.config['quality']}"
        )

    async def initialize(self):
        self.cleanup_task = asyncio.create_task(self._periodic_cleanup())
        _log("Background cleanup task started.")

    async def terminate(self):
        if self.cleanup_task and not self.cleanup_task.done():
            self.cleanup_task.cancel()
            try:
                await self.cleanup_task
            except asyncio.CancelledError:
                _log("Background cleanup task cancelled.")
        self._cleanup_temp_files(force=True)
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()
            _log("HTTP session closed.")

    async def _periodic_cleanup(self):
        while True:
            await asyncio.sleep(60)
            now = time.time()
            expired = [
                (sid, s["key"])
                for sid, s in self.waiting_users.items()
                if s["expire"] < now
            ]
            for sid, key in expired:
                self.waiting_users.pop(sid, None)
                self.song_cache.pop(key, None)
            self._cleanup_temp_files()

    def _track_temp(self, path: str):
        if path and path not in self._temp_files:
            self._temp_files.append(path)

    def _cleanup_temp_files(self, force: bool = False):
        remain = []
        now = time.time()
        for path in self._temp_files:
            try:
                if not os.path.exists(path):
                    continue
                age = now - os.path.getmtime(path)
                if force or age > 300:
                    os.remove(path)
                else:
                    remain.append(path)
            except OSError:
                remain.append(path)
        self._temp_files = remain

    # --- Handlers ---

    @filter.event_message_type(filter.EventMessageType.ALL, priority=-1)
    async def _probe_all_messages(self, event: AstrMessageEvent):
        """诊断探针：确认插件是否能收到事件。不拦截、不回复。"""
        try:
            raw = event.message_str or ""
            _log(
                f"PROBE msg='{raw}' platform={self._platform_name(event)} "
                f"session={event.get_session_id()} "
                f"is_wake={getattr(event, 'is_at_or_wake_command', None)} "
                f"chain={[type(c).__name__ for c in (event.get_messages() or [])]}"
            )
        except Exception as e:
            _log(f"PROBE error: {e!s}", "error")

    @filter.command("点歌", alias={"music", "听歌", "网易云"})
    async def cmd_handler(self, event: AstrMessageEvent, keyword: str = ""):
        raw = event.message_str or ""
        _log(
            f"cmd_handler fired. raw='{raw}', keyword='{keyword}', "
            f"platform={self._platform_name(event)}, session={event.get_session_id()}"
        )
        keyword = _normalize_message(keyword or "")
        if not keyword:
            text = _normalize_message(raw)
            m = re.match(r"^(?:点歌|music|听歌|网易云)\s*(.+)$", text, re.I)
            keyword = m.group(1).strip() if m else ""

        if not keyword:
            await event.send(
                MessageChain([Plain("主人，请告诉我您想听什么歌喵~ 例如：/点歌 Lemon")])
            )
            event.stop_event()
            return
        await self.search_and_show(event, keyword)
        # 阻止 natural_language_handler 对同一条消息重复响应
        event.stop_event()

    @filter.regex(r"(?i)(来.?一首|播放|听.?听|点歌|唱.?一首|来.?首|music|听歌|网易云)\s*.+")
    async def natural_language_handler(self, event: AstrMessageEvent):
        text = _normalize_message(event.message_str or "")
        _log(
            f"natural_language_handler fired. text='{text}', "
            f"platform={self._platform_name(event)}"
        )
        match = re.search(
            r"(?i)(?:来.?一首|播放|听.?听|点歌|唱.?一首|来.?首|music|听歌|网易云)\s*(.+?)(?:的歌|的歌曲|的音乐|歌|曲)?$",
            text,
        )
        if match:
            keyword = match.group(1).strip()
            if keyword:
                event.stop_event()
                await self.search_and_show(event, keyword)

    @filter.regex(r"^\d+$", priority=999)
    async def number_selection_handler(self, event: AstrMessageEvent):
        session_id = event.get_session_id()
        if session_id not in self.waiting_users:
            return
        user_session = self.waiting_users[session_id]
        if time.time() > user_session["expire"]:
            return
        try:
            num = int((event.message_str or "").strip())
        except ValueError:
            return
        limit = int(self.config.get("search_limit", 5))
        if not (1 <= num <= limit):
            return
        event.stop_event()
        del self.waiting_users[session_id]
        _log(f"number_selection_handler: chose #{num}, session={session_id}")
        await self.play_selected_song(event, user_session["key"], num)

    # --- Core ---

    def _platform_name(self, event: AstrMessageEvent) -> str:
        try:
            return event.get_platform_name() or "?"
        except Exception:
            return "?"

    async def search_and_show(self, event: AstrMessageEvent, keyword: str):
        _log(
            f"search_and_show: keyword='{keyword}' via {self.config['api_url']}"
        )
        try:
            songs = await self.api.search_songs(
                keyword, int(self.config.get("search_limit", 5))
            )
            _log(f"search returned {len(songs)} songs")
        except Exception as e:
            _log(f"search failed: {e!s}", "error")
            try:
                await event.send(
                    MessageChain(
                        [
                            Plain(
                                "呜喵...和音乐服务器的连接断掉了...\n"
                                f"当前 API：{self.config.get('api_url')}\n"
                                "请确认 Netease-CDN-Bypass 已启动"
                            )
                        ]
                    )
                )
            except Exception as e2:
                _log(f"send error msg failed: {e2!s}", "error")
            return

        if not songs:
            _log("no songs found")
            try:
                await event.send(
                    MessageChain(
                        [Plain(f"对不起主人...没能找到「{keyword}」这首歌喵... T_T")]
                    )
                )
            except Exception as e2:
                _log(f"send no-result failed: {e2!s}", "error")
            return

        cache_key = f"{event.get_session_id()}_{int(time.time())}"
        self.song_cache[cache_key] = songs

        lines = [f"主人，我为您找到了 {len(songs)} 首歌曲喵！请回复数字告诉我您想听哪一首~"]
        for i, song in enumerate(songs, 1):
            artists = " / ".join(
                a.get("name", "") for a in song.get("artists", []) if isinstance(a, dict)
            ) or "未知歌手"
            album = (
                song.get("album", {}).get("name", "未知专辑")
                if isinstance(song.get("album"), dict)
                else "未知专辑"
            )
            duration_ms = int(song.get("duration", 0) or 0)
            dur = f"{duration_ms // 60000}:{(duration_ms % 60000) // 1000:02d}"
            lines.append(f"{i}. {song.get('name', '未知')} - {artists} 《{album}》 [{dur}]")

        try:
            await event.send(MessageChain([Plain("\n".join(lines))]))
            _log("search list sent")
        except Exception as e:
            _log(f"send search list failed: {e!s}", "error")

        self.waiting_users[event.get_session_id()] = {
            "key": cache_key,
            "expire": time.time() + 60,
        }
        _log(f"showed {len(songs)} results")

    async def play_selected_song(self, event: AstrMessageEvent, cache_key: str, num: int):
        songs = self.song_cache.get(cache_key)
        if not songs:
            await event.send(MessageChain([Plain("喵呜~ 选择超时了，请重新点歌吧~")]))
            return
        if not (1 <= num <= len(songs)):
            await event.send(MessageChain([Plain("主人，请选择列表里的歌曲编号喵~")]))
            return

        selected = songs[num - 1]
        song_id = selected["id"]
        _log(f"play_selected_song: song_id={song_id}, name={selected.get('name')}")

        try:
            details = await self.api.get_song_details(song_id)
            if details:
                title = details.get("name", selected.get("name", ""))
                artists = " / ".join(
                    a.get("name", "")
                    for a in (details.get("ar") or details.get("artists") or [])
                    if isinstance(a, dict)
                )
                al = details.get("al") or details.get("album") or {}
                album = al.get("name", "未知专辑") if isinstance(al, dict) else "未知专辑"
                cover_url = al.get("picUrl", "") if isinstance(al, dict) else ""
                duration_ms = int(
                    details.get("dt")
                    or details.get("duration")
                    or selected.get("duration")
                    or 0
                )
            else:
                title = selected.get("name", "")
                artists = " / ".join(
                    a.get("name", "")
                    for a in selected.get("artists", [])
                    if isinstance(a, dict)
                )
                album = "未知专辑"
                cover_url = ""
                duration_ms = int(selected.get("duration", 0))

            if not artists:
                artists = "未知歌手"

            # Download audio locally first (critical for QQ Official upload)
            audio_path = await self.api.download_audio(song_id, self.config["quality"])
            if not audio_path:
                _log(f"audio download returned None for {song_id}", "error")
                try:
                    await event.send(
                        MessageChain(
                            [
                                Plain(
                                    f"喵~ 《{title}》暂时播放不了呢...\n"
                                    "这首歌可能是 VIP / 无版权曲目。\n"
                                    "如需播放 VIP 歌曲，请在 Netease-CDN-Bypass 的 .env 中配置 NETEASE_COOKIE 并重启服务喵~"
                                )
                            ]
                        )
                    )
                except Exception as e2:
                    _log(f"send download-fail msg failed: {e2!s}", "error")
                return

            self._track_temp(audio_path)
            dur = f"{duration_ms // 60000}:{(duration_ms % 60000) // 1000:02d}"
            await self._send_song_messages(
                event,
                num=num,
                title=title,
                artists=artists,
                album=album,
                dur_str=dur,
                cover_url=cover_url,
                audio_path=audio_path,
            )
        except Exception as e:
            _log(f"play_selected_song exception: {e!s}", "error")
            import traceback

            traceback.print_exc()
            try:
                await event.send(MessageChain([Plain("呜...发送失败了喵...")]))
            except Exception as e2:
                _log(f"send fail msg failed: {e2!s}", "error")
        finally:
            self.song_cache.pop(cache_key, None)

    def _quality_display(self, quality: str) -> str:
        return {
            "lossless": "无损",
            "exhigh": "极高",
            "higher": "高",
            "standard": "标准",
        }.get(quality.lower(), quality)

    def _is_official_qq(self, event: AstrMessageEvent) -> bool:
        bits = []
        for getter in (
            lambda: event.get_platform_name(),
            lambda: getattr(event, "get_platform_id", lambda: "")(),
            lambda: getattr(getattr(event, "platform_meta", None), "name", ""),
            lambda: getattr(getattr(event, "platform_meta", None), "type", ""),
            lambda: event.get_session_id(),
        ):
            try:
                bits.append(str(getter() or ""))
            except Exception:
                pass
        blob = " ".join(bits).lower()
        return any(
            k in blob
            for k in (
                "qq_official",
                "qqofficial",
                "official_webhook",
                "官机",
                "qqbot",
            )
        )

    async def _send_song_messages(
        self,
        event: AstrMessageEvent,
        num: int,
        title: str,
        artists: str,
        album: str,
        dur_str: str,
        cover_url: str,
        audio_path: str,
    ):
        detail_text = f"""遵命，主人！为您播放第 {num} 首歌曲~

♪ 歌名：{title}
🎤 歌手：{artists}
💿 专辑：{album}
⏳ 时长：{dur_str}
✨ 音质：{self._quality_display(self.config['quality'])}

请主人享用喵~
"""
        info = [Plain(detail_text)]

        image_data = await self.api.download_image(cover_url)
        if image_data:
            info.append(Image.fromBase64(base64.b64encode(image_data).decode()))

        try:
            await event.send(MessageChain(info))
            _log("info card sent")
        except Exception as e:
            _log(f"send info card failed: {e!s}", "error")

        filename = f"{_safe_filename(title)}.mp3"
        is_official = self._is_official_qq(event)
        _log(f"send audio: official={is_official}, file={audio_path}")

        # QQ Official flow: File card (file_type=4) first, then Record, then link
        if is_official:
            sent = False

            try:
                await event.send(MessageChain([File(name=filename, file=audio_path)]))
                _log("official File card sent")
                sent = True
            except Exception as e:
                _log(f"official File failed: {e!s}", "warning")

            if not sent:
                try:
                    await event.send(MessageChain([Record(file=audio_path)]))
                    _log("official Record sent")
                    sent = True
                except Exception as e:
                    _log(f"official Record failed: {e!s}", "warning")

            if not sent:
                try:
                    await event.send(
                        MessageChain([Plain("官方机器人富媒体发送失败了喵~ 请稍后再试")])
                    )
                except Exception as e2:
                    _log(f"send fallback text failed: {e2!s}", "error")
            return

        # Third-party bots: prefer Record voice
        try:
            await event.send(MessageChain([Record(file=audio_path)]))
            _log("third-party Record sent")
            return
        except Exception as e:
            _log(f"Record failed: {e!s}", "warning")

        try:
            await event.send(MessageChain([File(name=filename, file=audio_path)]))
            _log("third-party File sent")
            return
        except Exception as e:
            _log(f"File failed: {e!s}", "warning")

        try:
            await event.send(MessageChain([Plain("发送音频失败了喵~ 请稍后再试")]))
        except Exception as e:
            _log(f"send fallback failed: {e!s}", "error")
