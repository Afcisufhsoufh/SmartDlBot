import os
import requests
import logging
import time
import yt_dlp
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import Message
from typing import Optional
import asyncio
import aiofiles
from concurrent.futures import ThreadPoolExecutor
from config import SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET
# Spotify API Config
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_TRACK_API_URL = "https://api.spotify.com/v1/tracks/"

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Initialize Spotipy client
spotify = spotipy.Spotify(auth_manager=SpotifyClientCredentials(client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_CLIENT_SECRET))

YT_COOKIES_PATH = "./cookies/ItsSmartToolBot.txt"

# ThreadPoolExecutor for blocking I/O operations
executor = ThreadPoolExecutor(max_workers=10)

async def sanitize_filename(title: str) -> str:
    """Sanitize file name by removing invalid characters."""
    import re
    title = re.sub(r'[<>:"/\\|?*]', '', title)
    title = title.replace(' ', '_')
    return f"{title[:50]}_{int(time.time())}"

async def format_duration(ms: int) -> str:
    """Format duration from milliseconds to human-readable string."""
    seconds = ms // 1000
    minutes = (seconds % 3600) // 60
    seconds = seconds % 60
    return f"{minutes}m {seconds}s"

async def download_image(url: str, output_path: str) -> Optional[str]:
    """Download image from a URL."""
    try:
        response = requests.get(url, stream=True)
        if response.status_code == 200:
            async with aiofiles.open(output_path, 'wb') as file:
                for chunk in response.iter_content(1024):
                    await file.write(chunk)
            return output_path
    except Exception as e:
        logger.error(f"Failed to download image: {e}")
    return None

async def get_audio_opts(output_filename: str) -> dict:
    """Return yt-dlp options for audio download."""
    return {
        'format': 'bestaudio/best',
        'outtmpl': f'{output_filename}.%(ext)s',
        'cookiefile': YT_COOKIES_PATH,
        'quiet': True,
        'noprogress': True,
        'no_warnings': True,
        'nocheckcertificate': True,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
    }

async def download_audio(url: str, output_filename: str) -> Optional[str]:
    """Download audio using yt-dlp."""
    opts = await get_audio_opts(output_filename)
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(executor, lambda: yt_dlp.YoutubeDL(opts).download([url]))
        output_path = f"{output_filename}.mp3"
        if os.path.exists(output_path):
            return output_path
        return None
    except Exception as e:
        logger.error(f"Error downloading audio: {e}")
        return None

async def get_spotify_access_token() -> Optional[str]:
    """Fetch Spotify Access Token."""
    try:
        response = requests.post(SPOTIFY_TOKEN_URL, data={
            "grant_type": "client_credentials",
            "client_id": SPOTIFY_CLIENT_ID,
            "client_secret": SPOTIFY_CLIENT_SECRET
        })
        response.raise_for_status()
        return response.json().get("access_token")
    except Exception as e:
        logger.error(f"Failed to fetch Spotify token: {e}")
        return None

async def get_spotify_track(track_id: str) -> Optional[dict]:
    """Fetch Spotify Track Details."""
    token = await get_spotify_access_token()
    if not token:
        return None
    try:
        headers = {"Authorization": f"Bearer {token}"}
        response = requests.get(SPOTIFY_TRACK_API_URL + track_id, headers=headers)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Failed to fetch track details: {e}")
        return None

async def handle_spotify_request(client, message, url):
    if not url:
        await client.send_message(
            chat_id=message.chat.id,
            text="**Please provide a track Spotify URL ❌**",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    status_message = await client.send_message(
        chat_id=message.chat.id,
        text="`Processing your request...`",
        parse_mode=ParseMode.MARKDOWN
    )

    try:
        # Extract track ID from the Spotify URL
        track_id = url.split("/")[-1]
        track = await get_spotify_track(track_id)

        if not track:
            await status_message.edit("**❌ Could not fetch Spotify track details.**")
            return

        title = track['name']
        artists = ", ".join([artist['name'] for artist in track['artists']])
        duration = await format_duration(track['duration_ms'])

        # Fetch cover image
        cover_url = track['album']['images'][0]['url'] if track['album']['images'] else None
        cover_path = None
        if cover_url:
            os.makedirs("temp_media", exist_ok=True)
            cover_path = f"temp_media/{await sanitize_filename(title)}.jpg"
            await download_image(cover_url, cover_path)

        # Use YouTube search to find the track
        search_query = f"{title} {artists}"
        ydl_opts = {
            'format': 'bestaudio/best',
            'default_search': 'ytsearch1:',
            'quiet': True,
            'cookiefile': YT_COOKIES_PATH,
            'no_warnings': True,
            'nocheckcertificate': True,
            'noplaylist': True,
            'simulate': True
        }

        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(executor, lambda: yt_dlp.YoutubeDL(ydl_opts).extract_info(search_query, download=False))

        if 'entries' in info and info['entries']:
            yt_url = info['entries'][0]['webpage_url']
        else:
            await status_message.edit("**Please Provide A Valid Spotify URL ❌**")
            return

        # Download audio using yt-dlp
        safe_title = await sanitize_filename(title)
        output_filename = f"temp_media/{safe_title}"
        os.makedirs("temp_media", exist_ok=True)
        audio_path = await download_audio(yt_url, output_filename)

        if not audio_path:
            await status_message.edit("**❌ An Error Occurred**")
            return

        await status_message.edit("**Found ☑️ Downloading...**")

        if message.from_user:
            user_full_name = f"{message.from_user.first_name} {message.from_user.last_name or ''}".strip()
            user_info = f"[{user_full_name}](tg://user?id={message.from_user.id})"
        else:
            group_name = message.chat.title or "this group"
            group_url = f"https://t.me/{message.chat.username}" if message.chat.username else "this group"
            user_info = f"[{group_name}]({group_url})"

        audio_caption = (
            f"🎵 **Title:** `{title}`\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"👤 **Artist:** `{artists}`\n"
            f"⏱️ **Duration:** `{duration}`\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"**Downloaded By** {user_info}"
        )

        last_update_time = [0]
        start_time = time.time()

        await client.send_audio(
            chat_id=message.chat.id,
            audio=audio_path,
            caption=audio_caption,
            title=title,
            performer=artists,
            parse_mode=ParseMode.MARKDOWN,
            thumb=cover_path if cover_path else None,
            progress=progress_bar,
            progress_args=(status_message, start_time, last_update_time)
        )

        os.remove(audio_path)
        if cover_path:
            os.remove(cover_path)

        await status_message.delete()  # Delete the progress message after completion
    except Exception as e:
        await status_message.edit(f"**❌ An error occurred: {e}**")

async def progress_bar(current, total, status_message, start_time, last_update_time):
    """Display a progress bar for uploads."""
    elapsed_time = time.time() - start_time
    percentage = (current / total) * 100
    progress = "▓" * int(percentage // 5) + "░" * (20 - int(percentage // 5))
    speed = current / elapsed_time / 1024 / 1024  # Speed in MB/s
    uploaded = current / 1024 / 1024  # Uploaded size in MB
    total_size = total / 1024 / 1024  # Total size in MB

    # Throttle updates: Only update if at least 2 seconds have passed since the last update
    if time.time() - last_update_time[0] < 2:
        return
    last_update_time[0] = time.time()  # Update the last update time

    text = (
        f"📥 Upload Progress 📥\n\n"
        f"{progress}\n\n"
        f"🚧 Percentage: {percentage:.2f}%\n"
        f"⚡️ Speed: {speed:.2f} MB/s\n"
        f"📶 Uploaded: {uploaded:.2f} MB of {total_size:.2f} MB"
    )
    try:
        await status_message.edit(text)
    except Exception as e:
        logger.error(f"Error updating progress: {e}")

def setup_spotify_handler(app: Client):
    @app.on_message(filters.regex(r"^[/.]sp(\s+\S+)?$") & (filters.private | filters.group))
    async def spotify_command(client, message):
        # Check if the message contains a Spotify URL
        command_parts = message.text.split(maxsplit=1)
        url = command_parts[1] if len(command_parts) > 1 else None
        await handle_spotify_request(client, message, url)

    # Attach the downloader to the client for access in handlers
    app.downloader = yt_dlp.YoutubeDL()
    
