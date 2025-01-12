from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import MessageNotModified
from aria2p import API, Client as ariaClient
import os
import asyncio
import time
import pathlib
from pathlib import Path
import mimetypes
import subprocess
import configparser
import logging
import re
from yt_dlp import YoutubeDL
from urllib.parse import urlparse
import uuid
import ffmpeg
import math
import platform
from datetime import datetime
import psutil


# Simple logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    filename='bot.log'
)

# Global storage
downloads_db = {}
pending_rclone_users = set()  # Store users waiting for rclone.conf

DOWNLOAD_DIR = Path("Downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

RCLONE_CONFIGS_DIR = Path("UserConfigs")
RCLONE_CONFIGS_DIR.mkdir(exist_ok=True)

app = Client(
    "my_bot",
    api_id="2",
    api_hash="95",
    bot_token="7"
)

aria2 = ariaClient(
    host="http://localhost",
    port=6800,
    secret=""
)
aria_api = API(aria2)

def format_size(size):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"

def format_speed(speed):
    return format_size(speed) + "/s"

def create_progress_bar(percentage):
    completed = int(percentage / 10)
    return "█" * completed + "░" * (10 - completed)

def get_rclone_config_path(user_id):
    return RCLONE_CONFIGS_DIR / str(user_id) / "rclone.conf"

def get_available_remotes(config_path):
    config = configparser.ConfigParser()
    config.read(config_path)
    return [section for section in config.sections()]

def list_folder_contents(user_id, remote, path=""):
    config_path = get_rclone_config_path(user_id)
    try:
        command = [
            "rclone", 
            "lsf", 
            "--config", 
            str(config_path),
            f"{remote}:{path}", 
            "-R",
            "--dirs-only"
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        folders = result.stdout.strip().split('\n')
        return [f for f in folders if f]
    except Exception as e:
        logging.error(f"Error listing folders: {str(e)}")
        return []


 
   
@app.on_message(filters.command("start"))
async def start_command(client, message):
    try:
        await message.reply_text(
            "Hi! Send me a URL to download and upload. "
            
        )
    except Exception as e:
        logging.error(f"Error in start command: {str(e)}")

@app.on_message(filters.command("stats"))
async def stats_command(client, message):
    try:
        # OS Information
        uname = platform.uname()
        
        # Boot time info and uptime calculation
        try:
            boot_time = psutil.boot_time()
            boot_time_date = datetime.fromtimestamp(boot_time)
            uptime = datetime.now() - boot_time_date
            uptime_str = f"{uptime.days}d {uptime.seconds // 3600}h {(uptime.seconds // 60) % 60}m {uptime.seconds % 60}s"
        except PermissionError:
            uptime_str = "Permission denied"
        
        # CPU Information
        cpu_physical = psutil.cpu_count(logical=False)
        cpu_logical = psutil.cpu_count(logical=True)
        
        # Memory Information
        try:
            memory = psutil.virtual_memory()
            total_mem = memory.total / (1024 ** 3)  # Convert bytes to GB
            used_mem = memory.used / (1024 ** 3)
            available_mem = memory.available / (1024 ** 3)
            percentage_mem = memory.percent
        except PermissionError:
            total_mem = used_mem = available_mem = percentage_mem = 0

        # Disk Information
        disk_usage = psutil.disk_usage('/')
        total_disk = disk_usage.total / (1024 ** 3)  # Convert bytes to GB
        used_disk = disk_usage.used / (1024 ** 3)
        free_disk = disk_usage.free / (1024 ** 3)
        percentage_disk = disk_usage.percent

        # Constructing the message with system information
        info_message = (
            f"🖥️ **OS SYSTEM:**\n"
            f"┠ **OS Uptime:** {uptime_str}\n"
            f"┠ **OS Version:** {uname.version}\n"
            f"┖ **OS Arch:** {platform.platform()}\n\n"

            f"🖥️ **CPU Information:**\n"
            f"┠ **Physical Cores:** {cpu_physical}\n"
            f"┠ **Logical Cores:** {cpu_logical}\n\n"

            f"💾 **RAM (MEMORY):**\n"
            f"┃ [{('■' * (int(percentage_mem) // 10))}{('□' * (10 - (int(percentage_mem) // 10)))}] {percentage_mem}%\n"
            f"┖ **Used:** {used_mem:.2f}GB | **Free:** {available_mem:.2f}GB | **Total:** {total_mem:.2f}GB\n\n"

            f"💽 **DISK STORAGE:**\n"
            f"┃ [{('■' * (int(percentage_disk) // 10))}{('□' * (10 - (int(percentage_disk) // 10)))}] {percentage_disk}%\n"
            f"┖ **Used:** {used_disk:.2f}GB | **Free:** {free_disk:.2f}GB | **Total:** {total_disk:.2f}GB"
        )

        
        await message.reply_text(info_message)

    except Exception as e:
        logging.error(f"Error in stats command: {str(e)}")
        await message.reply_text("An error occurred while retrieving system stats.")
        
@app.on_message(filters.document)
async def handle_document(client, message):
    try:
        user_id = message.from_user.id
        
        # Check if user is waiting for rclone.conf
        if user_id in pending_rclone_users and message.document.file_name.endswith('.conf'):
            # Create user directory
            user_config_dir = RCLONE_CONFIGS_DIR / str(user_id)
            user_config_dir.mkdir(exist_ok=True)
            
            config_path = get_rclone_config_path(user_id)
            
            # Download the config file
            await message.download(file_name=str(config_path))
            
            # Remove user from pending list
            pending_rclone_users.remove(user_id)
            
            # Verify the config by listing remotes
            remotes = get_available_remotes(config_path)
            if remotes:
                # Show available remotes
                buttons = []
                for remote in remotes:
                    buttons.append([InlineKeyboardButton(
                        f"📁 {remote}", 
                        callback_data=f"remote_{remote}_"
                    )])
                buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
                
                await message.reply_text(
                    "✅ Rclone config saved! Select a remote:",
                    reply_markup=InlineKeyboardMarkup(buttons)
                )
            else:
                await message.reply_text("❌ No remotes found in config file!")
            
            logging.info(f"Rclone config saved for user {user_id}")
        else:
            # Forward to general download handler for non-rclone documents
            await handle_telegram_download(client, message)
            
    except Exception as e:
        logging.error(f"Error handling document: {str(e)}")
        await message.reply_text("❌ Error processing file")
        await message.reply_text("❌ No remotes found in config file!")

@app.on_message(filters.document | filters.video | filters.audio | filters.photo)
async def handle_telegram_download(client, message):
    try:
        user_id = message.from_user.id
        
        # Get file name and size based on message type
        if message.document:
            file_name = message.document.file_name
            file_size = message.document.file_size
        elif message.video:
            file_name = message.video.file_name
            file_size = message.video.file_size
        elif message.audio:
            file_name = message.audio.file_name
            file_size = message.audio.file_size
        elif message.photo:
            file_name = f"photo_{message.photo.file_unique_id}.jpg"
            file_size = message.photo.file_size
        else:
            file_name = f"file_{message.id}"
            file_size = 0
            
        progress_msg = await message.reply_text(
            f"🔽 **Starting download...**\n"
            f"📄 **File:** {file_name}\n"
            f"📏 **Size:** {format_size(file_size)}"
        )
            
        # Generate unique file path
        file_path = DOWNLOAD_DIR / file_name
        
        # Track download in database
        downloads_db[progress_msg.id] = {
            'file_path': str(file_path),
            'file_name': file_name,
            'file_size': file_size
        }
        
        # Progress callback for download
        start_time = time.time()
        last_update_time = start_time
        last_downloaded = 0
        
        async def progress(current, total):
            nonlocal last_update_time, last_downloaded
            now = time.time()
            
            if now - last_update_time >= 3:
                time_diff = now - last_update_time
                size_diff = current - last_downloaded
                speed = size_diff / time_diff if time_diff > 0 else 0
                
                percentage = (current * 100) / total
                progress_text = (
                    f"🔽 **Downloading**\n"
                    f"📄 **File:** {file_name}\n"
                    f"{create_progress_bar(percentage)} {percentage:.1f}%\n"
                    f"⚡ **Speed:** {format_speed(speed)}\n"
                    f"📥 **Downloaded:** {format_size(current)} / {format_size(total)}"
                )
                
                try:
                    await progress_msg.edit_text(progress_text)
                except MessageNotModified:
                    pass
                    
                last_update_time = now
                last_downloaded = current
        
        # Download the file
        await message.download(
            file_name=str(file_path),
            progress=progress
        )
        
        # Show upload options with file info
        buttons = [
                [
                    InlineKeyboardButton("📤 Telegram", callback_data=f"telegram_{progress_msg.id}"),
                    InlineKeyboardButton("☁️ Cloud", callback_data=f"rclone_{progress_msg.id}")
                ],
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
            ]
        
        complete_text = (
            f"✅ **Download complete!**\n"
            f"📄 **File:** {file_name}\n"
            f"📏 **Size:** {format_size(file_size)}\n"
            f"💾 **Choose upload destination:**"
        )
        
        await progress_msg.edit_text(
            complete_text,
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        
    except Exception as e:
        logging.error(f"Error in telegram download: {str(e)}")
        await message.reply_text("❌ **Download failed**")
        

@app.on_message(filters.command("l"))
async def handle_url(client, message):
    try:
        # Extract URL and filename from command
        command_parts = message.text.split()
        url = None
        custom_filename = None
        
        # Handle reply to URL message
        if message.reply_to_message and message.reply_to_message.text:
            # Check if the replied message contains a URL
            urls = re.findall(r'https?://[^\s]+', message.reply_to_message.text)
            if urls:
                url = urls[0]
                # Check if filename was provided with command
                if len(command_parts) > 1:
                    if command_parts[1] == '-n' and len(command_parts) > 2:
                        custom_filename = command_parts[2]
                    else:
                        custom_filename = command_parts[1]
        
        # Handle direct command with URL
        else:
            if len(command_parts) > 1:
                url = command_parts[1]
                # Check for filename after URL
                if len(command_parts) > 2:
                    if command_parts[2] == '-n' and len(command_parts) > 3:
                        custom_filename = command_parts[3]
                    elif '-n' not in command_parts:
                        custom_filename = command_parts[2]
        
        if not url:
            await message.reply_text(
                "❌ **Invalid usage!**\n"
                "**Usage:**\n"
                "• `/l <url> [-n filename.ext]`\n"
                "• Reply to a URL with `/l [filename.ext]`"
            )
            return
        
        # Check if URL is a Telegram message URL
        url_pattern = r"https://t.me/(.+?)/(\d+)(\?single)?"
        match = re.match(url_pattern, url)
        
        if match:
            # Handle Telegram URL
            from_chat_id = match.group(1)  # Extract the chat username
            message_id = int(match.group(2))  # Extract the message ID
            
            try:
                # Copy the message to the user's chat
                copied_message = await client.copy_message(
                    chat_id=message.chat.id,  # Send back to the user
                    from_chat_id=from_chat_id,
                    message_id=message_id,
                    disable_notification=True  # Optional: send silently
                )
                await message.reply_text(
                    f"✅ **Message copied successfully!**\n"
                    f"📝 **Copied message ID:** {copied_message.id}\n"
                    f"📍 **To chat:** {message.chat.id}"
                )
                return
            except Exception as e:
                await message.reply_text(f"❌ **Error copying message:** {str(e)}")
                return
            
        user_id = message.from_user.id
        
        # Initial download message
        progress_msg = await message.reply_text(
            f"🚀 **Initiating download...**\n"
            f"🔗 **URL:** {url[:50]}..." if len(url) > 50 else url
        )
        logging.info(f"Starting download for user {user_id}")
        
        # Rest of the original function remains the same
        try:
            # Set download options
            options = {'dir': str(DOWNLOAD_DIR)}
            if custom_filename:
                options['out'] = custom_filename
                
            # Start download
            download = aria_api.add_uris([url], options)
            if not download or not download.gid:
                raise Exception("Failed to start download")
                
            downloads_db[progress_msg.id] = {
                'gid': download.gid,
                'file_path': None
            }
        except Exception as aria_error:
            error_message = str(aria_error).lower()
            if "403" in error_message:
                await progress_msg.edit_text(
                    "❌ **Download failed: Access Forbidden (HTTP 403)**\n"
                )
            elif "400" in error_message:
                await progress_msg.edit_text(
                    "❌ **Download failed: Bad Request (HTTP 400)**\n"
                )
            else:
                await progress_msg.edit_text(
                    f"❌ **Download failed**\n"
                    f"**Error:** {str(aria_error)}\n"
                    "Please try again with a different URL."
                )
            logging.error(f"Aria2c error for user {user_id}: {str(aria_error)}")
            return
            
        # Monitor download progress
        last_update = 0
        stall_count = 0
        last_progress = 0
        error_count = 0  # Track consecutive errors
        
        while True:
            try:
                # Get fresh download status
                download = aria_api.get_download(download.gid)
                
                # Check if download object is valid
                if not download:
                    await progress_msg.edit_text("❌ **Download failed: Lost connection to download**")
                    return
                    
                # Check download status
                if download.is_complete:
                    break
                elif download.has_failed:
                    error_msg = download.error_message or "Unknown error"
                    await progress_msg.edit_text(
                        f"❌ **Download failed**\n"
                        f"**Error:** {error_msg}"
                    )
                    return
                
                now = time.time()
                
                # Check if download is stuck
                if download.progress == last_progress:
                    stall_count += 1
                else:
                    stall_count = 0
                    last_progress = download.progress
                
                # If download is stuck for too long (30 seconds), abort
                if stall_count >= 30:
                    await progress_msg.edit_text(
                        "❌ **Download failed: Connection timed out**\n"
                    )
                    try:
                        aria_api.remove([download.gid])
                    except:
                        pass
                    return
                
                if now - last_update >= 3:  # Update every 3 seconds
                    file_name = download.name or "Downloading..."
                    percentage = download.progress
                    speed = download.download_speed
                    current = download.completed_length
                    total = download.total_length
                    
                    progress_text = (
                        f"🔽 **Downloading**\n"
                        f"📄 **File:** {file_name}\n"
                        f"{create_progress_bar(percentage)} {percentage:.1f}%\n"
                        f"⚡ **Speed:** {format_speed(speed)}\n"
                        f"📥 **Downloaded:** {format_size(current)} / {format_size(total)}"
                    )
                    
                    await progress_msg.edit_text(progress_text)
                    last_update = now
                    error_count = 0  # Reset error count on successful update
                
                await asyncio.sleep(1)
                
            except MessageNotModified:
                pass
            except Exception as e:
                error_count += 1
                logging.error(f"Error updating progress: {str(e)}")
                
                # If we get too many consecutive errors, abort
                if error_count >= 5:
                    await progress_msg.edit_text(
                        "❌ **Download failed: Too many errors**\n"
                        "The download may continue in background."
                    )
                    return
                    
                await asyncio.sleep(1)
        
        # Download complete, process the file
        if download.is_complete:
            if not download.files or not download.files[0].path:
                await progress_msg.edit_text("❌ **Download failed: Could not locate downloaded file**")
                return
                
            file_path = download.files[0].path
            file_name = os.path.basename(file_path)
            file_size = os.path.getsize(file_path)
            
            downloads_db[progress_msg.id]['file_path'] = file_path
            downloads_db[progress_msg.id]['file_name'] = file_name
            downloads_db[progress_msg.id]['file_size'] = file_size
            
            buttons = [
                [
                    InlineKeyboardButton("📤 Telegram", callback_data=f"telegram_{progress_msg.id}"),
                    InlineKeyboardButton("☁️ Cloud", callback_data=f"rclone_{progress_msg.id}")
                ],
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
            ]
            
            complete_text = (
                f"✅ **Download complete!**\n"
                f"📄 **File:** {file_name}\n"
                f"📏 **Size:** {format_size(file_size)}\n"
                f"🔽 **Choose upload destination:**"
            )
            
            await progress_msg.edit_text(
                complete_text,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        
    except Exception as e:
        logging.error(f"Error in handle_url: {str(e)}")
        await message.reply_text("❌ **Error processing URL**")
        
@app.on_message(filters.command("yl"))
async def handle_ytdl(client, message):
    try:
        # Extract URL and filename from command
        command_parts = message.text.split()
        url = None
        custom_filename = None
        
        # Handle reply to URL message
        if message.reply_to_message and message.reply_to_message.text:
            # Check if the replied message contains a URL
            urls = re.findall(r'https?://[^\s]+', message.reply_to_message.text)
            if urls:
                url = urls[0]
                # Check if filename was provided with command
                if len(command_parts) > 1:
                    if command_parts[1] == '-n' and len(command_parts) > 2:
                        custom_filename = command_parts[2]
                    else:
                        custom_filename = command_parts[1]
        
        # Handle direct command with URL
        else:
            if len(command_parts) > 1:
                url = command_parts[1]
                # Check for filename after URL
                if len(command_parts) > 2:
                    if command_parts[2] == '-n' and len(command_parts) > 3:
                        custom_filename = command_parts[3]
                    elif '-n' not in command_parts:
                        custom_filename = command_parts[2]
        
        if not url:
            await message.reply_text(
                "❌ **Invalid usage!**\n"
                "**Usage:**\n"
                "• `/yl <url> [-n filename.ext]`\n"
                "• Reply to a URL with `/yl [filename.ext]`"
            )
            return

        # Initial download message
        progress_msg = await message.reply_text(
            f"🚀 **Initiating download...**\n"
            f"🔗 **URL:** {url[:50]}..." if len(url) > 50 else url
        )
        
        # Configure base yt-dlp options
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'nooverwrites': True,
            'outtmpl': os.path.join(DOWNLOAD_DIR, '%(title)s.%(ext)s'),
        }
        
        # Update format based on URL type
        if url.startswith("https://drive.google.com"):
            ydl_opts.update({'format': 'source'})
        else:
            ydl_opts.update({'format': 'best'})
            
        # If custom filename is provided, set the output template
        if custom_filename:
            base, ext = os.path.splitext(custom_filename)
            if not ext:  # If no extension provided, let yt-dlp handle it
                ydl_opts['outtmpl'] = os.path.join(DOWNLOAD_DIR, base + '.%(ext)s')
            else:
                ydl_opts['outtmpl'] = os.path.join(DOWNLOAD_DIR, custom_filename)

        try:
            # Download using yt-dlp
            with YoutubeDL(ydl_opts) as ydl:
                await progress_msg.edit_text("⏳ **Extracting information...**")
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
                
            if not filename or not os.path.exists(filename):
                await progress_msg.edit_text("❌ **Download failed: Could not locate downloaded file**")
                return
                
            file_size = os.path.getsize(filename)
            
            # Store download information
            downloads_db[progress_msg.id] = {
                'file_path': filename,
                'file_name': os.path.basename(filename),
                'file_size': file_size
            }
            
            # Create upload buttons
            buttons = [
                [
                    InlineKeyboardButton("📤 Telegram", callback_data=f"telegram_{progress_msg.id}"),
                    InlineKeyboardButton("☁️ Cloud", callback_data=f"rclone_{progress_msg.id}")
                ],
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
            ]
            
            complete_text = (
                f"✅ **Download complete!**\n"
                f"📄 **File:** {os.path.basename(filename)}\n"
                f"📏 **Size:** {format_size(file_size)}\n"
                f"🔽 **Choose upload destination:**"
            )
            
            await progress_msg.edit_text(
                complete_text,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            
        except Exception as ydl_error:
            error_message = str(ydl_error).lower()
            if "copyright" in error_message:
                await progress_msg.edit_text("❌ **Download failed: Content is copyright protected**")
            elif "private" in error_message:
                await progress_msg.edit_text("❌ **Download failed: Content is private or unavailable**")
            else:
                await progress_msg.edit_text(
                    f"❌ **Download failed**\n"
                    f"**Error:** {str(ydl_error)}"
                )
            logging.error(f"YT-DLP error: {str(ydl_error)}")
            return
            
    except Exception as e:
        logging.error(f"Error in handle_ytdl: {str(e)}")
        await message.reply_text("❌ **Error processing URL**")

        
def get_metadata(video_path):
    width, height, duration = 1280, 720, 0
    try:
        video_streams = ffmpeg.probe(video_path, select_streams="v")
        for item in video_streams.get("streams", []):
            height = item["height"]
            width = item["width"]
        duration = int(float(video_streams["format"]["duration"]))
    except Exception as e:
        logging.error(e)
    try:
        thumb = pathlib.Path(video_path).parent.joinpath(f"{uuid.uuid4().hex}-thunmnail.png").as_posix()
        ffmpeg.input(video_path, ss=duration / 2).filter("scale", width, -1).output(thumb, vframes=1).run()
    except ffmpeg._run.Error:
        thumb = None
    return dict(height=height, width=width, duration=duration, thumb=thumb)
        
@app.on_callback_query(filters.regex("^telegram_"))
async def handle_telegram_upload(client, callback_query: CallbackQuery):
    try:
        msg_id = int(callback_query.data.split('_')[1])
        download_info = downloads_db.get(msg_id)
        
        if not download_info or not download_info['file_path']:
            await callback_query.message.edit_text("❌ **Download information not found**")
            return
            
        file_path = download_info['file_path']
        file_name = download_info['file_name']
        file_size = download_info['file_size']
        file_type = download_info.get('file_type', 'document')
        message = callback_query.message
        
        # Initialize upload progress
        start_time = time.time()
        last_update_time = start_time
        last_uploaded = 0
        
        async def progress(current, total):
            nonlocal last_update_time, last_uploaded
            now = time.time()
            
            if now - last_update_time >= 3:
                time_diff = now - last_update_time
                size_diff = current - last_uploaded
                speed = size_diff / time_diff if time_diff > 0 else 0
                
                percentage = (current * 100) / total
                progress_text = (
                    f"📤 **Uploading to Telegram**\n"
                    f"📄 **File:** {file_name}\n"
                    f"{create_progress_bar(percentage)} {percentage:.1f}%\n"
                    f"⚡ **Speed:** {format_speed(speed)}\n"
                    f"📤 **Uploaded:** {format_size(current)} / {format_size(total)}"
                )
                try:
                    await message.edit_text(progress_text)
                except MessageNotModified:
                    pass
                
                last_update_time = now
                last_uploaded = current
        
        initial_text = (
            f"📤 **Starting upload to Telegram...**\n"
            f"📄 **File:** {file_name}\n"
            f"📏 **Size:** {format_size(file_size)}"
        )
        await message.edit_text(initial_text)
        
        # Determine file type and use appropriate upload method
        file_ext = os.path.splitext(file_name)[1].lower()
        
        try:
            if file_type == 'video' or file_ext in ['.mp4', '.mkv', '.avi', '.mov', '.flv']:
                # Get video metadata including thumbnail
                meta = get_metadata(file_path)
                thumb_path = meta.pop('thumb', None)
                
                try:
                    # Upload video with metadata
                    await callback_query.message.reply_video(
                        video=file_path,
                        progress=progress,
                        file_name=file_name,
                        thumb=thumb_path,
                        supports_streaming=True,
                        caption=file_name,
                        **meta  # Includes height, width, duration
                    )
                finally:
                    # Clean up thumbnail if it was created
                    if thumb_path and os.path.exists(thumb_path):
                        try:
                            os.remove(thumb_path)
                        except Exception as e:
                            logging.error(f"Error removing thumbnail: {str(e)}")
                            
            elif file_type == 'audio' or file_ext in ['.mp3', '.m4a', '.wav', '.ogg', '.flac']:
                await callback_query.message.reply_audio(
                    audio=file_path,
                    progress=progress,
                    file_name=file_name
                )
            elif file_type == 'photo' or file_ext in ['.jpg', '.jpeg', '.png', '.webp']:
                await callback_query.message.reply_photo(
                    photo=file_path,
                    progress=progress,
                    file_name=file_name
                )
            else:
                await callback_query.message.reply_document(
                    document=file_path,
                    progress=progress,
                    file_name=file_name
                )
        except Exception as upload_error:
            logging.error(f"Error during specific upload type, falling back to document: {str(upload_error)}")
            # Fallback to document upload if specific media upload fails
            await callback_query.message.reply_document(
                document=file_path,
                progress=progress,
                file_name=file_name
            )
        
        os.remove(file_path)
        del downloads_db[msg_id]
        
        complete_text = (
            f"✅ **Upload complete!**\n"
            f"📄 **File:** {file_name}\n"
            f"📏 **Size:** {format_size(file_size)}"
        )
        await message.edit_text(complete_text)
        logging.info(f"Telegram upload completed for file: {file_name}")
        
    except Exception as e:
        logging.error(f"Error in telegram upload: {str(e)}")
        await callback_query.message.edit_text("❌ **Upload failed**")

@app.on_callback_query(filters.regex("^rclone_"))
async def handle_rclone_selection(client, callback_query: CallbackQuery):
    try:
        user_id = callback_query.from_user.id
        config_path = get_rclone_config_path(user_id)
        
        if not config_path.exists():
            # Add user to pending list
            pending_rclone_users.add(user_id)
            await callback_query.message.edit_text(
                "Please send your rclone.conf file to start using cloud storage."
            )
            return
            
        remotes = get_available_remotes(config_path)
        if not remotes:
            await callback_query.message.edit_text("No remotes found in your config!")
            return
            
        buttons = []
        for remote in remotes:
            buttons.append([InlineKeyboardButton(
                f"📁 {remote}", 
                callback_data=f"remote_{remote}_"
            )])
        buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
        
        await callback_query.message.edit_text(
            "Select a remote:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        
    except Exception as e:
        logging.error(f"Error in rclone selection: {str(e)}")
        await callback_query.message.edit_text("❌ Error showing remotes")

@app.on_callback_query(filters.regex("^remote_"))
async def handle_remote_navigation(client, callback_query: CallbackQuery):
    try:
        user_id = callback_query.from_user.id
        data = callback_query.data.split('_')
        remote = data[1]
        current_path = data[2] if len(data) > 2 else ""
        
        folders = list_folder_contents(user_id, remote, current_path)
        buttons = []
        
        # Add folder buttons
        for folder in folders:
            folder_name = os.path.basename(folder.rstrip('/'))
            folder_path = folder.rstrip('/')
            buttons.append([InlineKeyboardButton(
                f"📁 {folder_name}",
                callback_data=f"remote_{remote}_{folder_path}"
            )])
        
        # Add upload here button
        buttons.append([InlineKeyboardButton(
            "📤 Upload Here",
            callback_data=f"upload_{remote}_{current_path}"
        )])
        
        # Add navigation buttons
        nav_buttons = []
        if current_path:  # Add back button if not in root
            parent_path = str(Path(current_path).parent)
            nav_buttons.append(InlineKeyboardButton(
                "⬅️ Back",
                callback_data=f"remote_{remote}_{parent_path}"
            ))
        nav_buttons.append(InlineKeyboardButton("❌ Cancel", callback_data="cancel"))
        buttons.append(nav_buttons)
        
        await callback_query.message.edit_text(
            f"Current location: {remote}:{current_path or '/'}\nSelect a folder:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        
    except Exception as e:
        logging.error(f"Error in remote navigation: {str(e)}")
        await callback_query.message.edit_text("❌ Error browsing folders")

@app.on_callback_query(filters.regex("^upload_"))
async def handle_rclone_upload(client, callback_query: CallbackQuery):
    try:
        data = callback_query.data.split('_')
        remote = data[1]
        path = data[2] if len(data) > 2 else ""
        user_id = callback_query.from_user.id
        message = callback_query.message
        
        # Get the file path from downloads_db
        msg_id = message.id
        if msg_id not in downloads_db or not downloads_db[msg_id]['file_path']:
            await message.edit_text("❌ Download information not found")
            return
        
        file_path = downloads_db[msg_id]['file_path']
        config_path = get_rclone_config_path(user_id)
        
        await message.edit_text("⬆️ Starting upload to cloud storage...")
        
        # Start rclone upload with progress monitoring
        process = subprocess.Popen(
            [
                "rclone",
                "copy",
                "--progress",
                "--config",
                str(config_path),
                file_path,
                f"{remote}:{path}"
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True
        )
        
        last_update = 0
        while True:
            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break
                
            if time.time() - last_update >= 3:
                if "Transferred:" in line:
                    try:
                        await message.edit_text(
                            f"⬆️ Uploading to cloud storage:\n{line}"
                        )
                    except MessageNotModified:
                        pass
                    last_update = time.time()
        
        if process.returncode == 0:
            await message.edit_text("✅ Upload to cloud storage complete!")
        else:
            await message.edit_text("❌ Upload to cloud storage failed!")
        
        # Clean up
        os.remove(file_path)
        del downloads_db[msg_id]
        
    except Exception as e:
        logging.error(f"Error in rclone upload: {str(e)}")
        await callback_query.message.edit_text("❌ Error during upload")

@app.on_callback_query(filters.regex("^cancel"))
async def handle_cancel(client, callback_query: CallbackQuery):
    try:
        # Check if there's a download to cancel
        msg_id = callback_query.message.id
        if msg_id in downloads_db:
            file_path = downloads_db[msg_id].get('file_path')
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
            del downloads_db[msg_id]
        
        await callback_query.message.edit_text("❌ Operation cancelled")
        logging.info(f"Operation cancelled by user {callback_query.from_user.id}")
    except Exception as e:
        logging.error(f"Error in cancel handler: {str(e)}")
        await callback_query.message.edit_text("❌ Error cancelling operation")

if __name__ == "__main__":
    logging.info("Bot starting...")
    # Start aria2
    subprocess.Popen([
        "aria2c",
        "--enable-rpc",
        "--rpc-listen-all=true",
        "--rpc-allow-origin-all",
        "--rpc-listen-port=6800",
        "--disable-ipv6"
    ])
    # Start bot
    app.run()
