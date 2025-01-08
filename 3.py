# bot.py
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import logging
from pathlib import Path
from download_manager import DownloadManager
from upload_manager import UploadManager
from progress_tracker import ProgressTracker

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    filename='bot.log'
)

class TelegramBot:
    def __init__(self):
        self.app = Client(
            "my_bot",
            api_id="27",
            api_hash="916",
            bot_token="702E"
        )
        
        # Initialize managers
        self.download_manager = DownloadManager()
        self.upload_manager = UploadManager()
        self.progress_tracker = ProgressTracker()
        
        # Setup handlers
        self._setup_handlers()
        
    def _setup_handlers(self):
        @self.app.on_message(filters.command("start"))
        async def start_command(client, message):
            await message.reply_text(
                "Hi! Send me a URL to download and upload as media. "
                "For cloud storage uploads, send me your rclone.conf file first."
            )

        @self.app.on_message(filters.text & filters.regex(r'https?://[^\s]+'))
        async def handle_url(client, message):
            await self.download_manager.start_download(client, message)

        @self.app.on_callback_query(filters.regex("^cancel_download_"))
        async def handle_download_cancel(client, callback_query):
            await self.download_manager.cancel_download(callback_query)

        @self.app.on_callback_query(filters.regex("^cancel_upload_"))
        async def handle_upload_cancel(client, callback_query):
            await self.upload_manager.cancel_upload(callback_query)

    def run(self):
        self.app.run()

if __name__ == "__main__":
    bot = TelegramBot()
    bot.run()

# download_manager.py
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aria2p import API, Client as ariaClient
import asyncio
import logging
from progress_tracker import ProgressTracker

class DownloadManager:
    def __init__(self):
        self.aria2 = ariaClient(
            host="http://localhost",
            port=6800,
            secret=""
        )
        self.aria_api = API(self.aria2)
        self.downloads = {}
        self.progress_tracker = ProgressTracker()
        self.active_downloads = {}

    async def start_download(self, client, message):
        try:
            url = message.text
            progress_msg = await message.reply_text(
                "⬇️ Starting download...",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "❌ Cancel Download",
                        callback_data=f"cancel_download_{message.id}"
                    )
                ]])
            )

            download = self.aria_api.add_uris([url])
            self.active_downloads[message.id] = {
                'gid': download.gid,
                'cancelled': False
            }

            await self._monitor_download(download, progress_msg, message.id)

        except Exception as e:
            logging.error(f"Download error: {str(e)}")
            await message.reply_text("❌ Download failed")

    async def _monitor_download(self, download, progress_msg, msg_id):
        try:
            while not download.is_complete and not self.active_downloads[msg_id]['cancelled']:
                download.update()
                progress_text = self.progress_tracker.get_download_progress(download)
                
                try:
                    await progress_msg.edit_text(
                        progress_text,
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton(
                                "❌ Cancel Download",
                                callback_data=f"cancel_download_{msg_id}"
                            )
                        ]])
                    )
                except Exception:
                    pass
                
                await asyncio.sleep(3)

            if not self.active_downloads[msg_id]['cancelled']:
                self.downloads[progress_msg.id] = {
                    'file_path': download.files[0].path
                }
                await self._show_upload_options(progress_msg)

        except Exception as e:
            logging.error(f"Download monitoring error: {str(e)}")

    async def _show_upload_options(self, progress_msg):
        buttons = [[
            InlineKeyboardButton("📤 Telegram", callback_data=f"telegram_{progress_msg.id}"),
            InlineKeyboardButton("☁️ Cloud", callback_data=f"rclone_{progress_msg.id}")
        ]]
        
        await progress_msg.edit_text(
            "✅ Download complete! Choose upload destination:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    async def cancel_download(self, callback_query):
        msg_id = int(callback_query.data.split('_')[2])
        if msg_id in self.active_downloads:
            self.active_downloads[msg_id]['cancelled'] = True
            gid = self.active_downloads[msg_id]['gid']
            self.aria_api.remove([gid])
            await callback_query.message.edit_text("❌ Download cancelled")
            del self.active_downloads[msg_id]

# upload_manager.py
import os
import subprocess
import logging
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from progress_tracker import ProgressTracker
from pathlib import Path

class UploadManager:
    def __init__(self):
        self.progress_tracker = ProgressTracker()
        self.active_uploads = {}
        self.RCLONE_CONFIGS_DIR = Path("UserConfigs")
        self.RCLONE_CONFIGS_DIR.mkdir(exist_ok=True)

    async def upload_to_telegram(self, client, callback_query, file_path):
        try:
            msg_id = callback_query.message.id
            self.active_uploads[msg_id] = {'cancelled': False}

            progress_msg = await callback_query.message.edit_text(
                "⬆️ Starting upload to Telegram...",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "❌ Cancel Upload",
                        callback_data=f"cancel_upload_{msg_id}"
                    )
                ]])
            )

            async def progress(current, total):
                if not self.active_uploads[msg_id]['cancelled']:
                    progress_text = self.progress_tracker.get_upload_progress(current, total)
                    try:
                        await progress_msg.edit_text(
                            progress_text,
                            reply_markup=InlineKeyboardMarkup([[
                                InlineKeyboardButton(
                                    "❌ Cancel Upload",
                                    callback_data=f"cancel_upload_{msg_id}"
                                )
                            ]])
                        )
                    except Exception:
                        pass

            await callback_query.message.reply_document(
                document=file_path,
                progress=progress
            )

            if not self.active_uploads[msg_id]['cancelled']:
                await progress_msg.edit_text("✅ Upload complete!")
            
            os.remove(file_path)
            del self.active_uploads[msg_id]

        except Exception as e:
            logging.error(f"Telegram upload error: {str(e)}")
            await callback_query.message.edit_text("❌ Upload failed")

    async def upload_to_cloud(self, callback_query, remote, path, file_path):
        try:
            msg_id = callback_query.message.id
            self.active_uploads[msg_id] = {
                'process': None,
                'cancelled': False
            }

            config_path = self.RCLONE_CONFIGS_DIR / str(callback_query.from_user.id) / "rclone.conf"
            
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
            
            self.active_uploads[msg_id]['process'] = process

            while process.poll() is None and not self.active_uploads[msg_id]['cancelled']:
                line = process.stdout.readline()
                if "Transferred:" in line:
                    progress_text = f"⬆️ Uploading to cloud storage:\n{line}"
                    try:
                        await callback_query.message.edit_text(
                            progress_text,
                            reply_markup=InlineKeyboardMarkup([[
                                InlineKeyboardButton(
                                    "❌ Cancel Upload",
                                    callback_data=f"cancel_upload_{msg_id}"
                                )
                            ]])
                        )
                    except Exception:
                        pass

            if not self.active_uploads[msg_id]['cancelled'] and process.returncode == 0:
                await callback_query.message.edit_text("✅ Upload to cloud storage complete!")
            else:
                await callback_query.message.edit_text("❌ Upload cancelled or failed")

            os.remove(file_path)
            del self.active_uploads[msg_id]

        except Exception as e:
            logging.error(f"Cloud upload error: {str(e)}")
            await callback_query.message.edit_text("❌ Upload failed")

    async def cancel_upload(self, callback_query):
        msg_id = int(callback_query.data.split('_')[2])
        if msg_id in self.active_uploads:
            self.active_uploads[msg_id]['cancelled'] = True
            if self.active_uploads[msg_id].get('process'):
                self.active_uploads[msg_id]['process'].terminate()
            await callback_query.message.edit_text("❌ Upload cancelled")
            del self.active_uploads[msg_id]

# progress_tracker.py
class ProgressTracker:
    def __init__(self):
        self.last_update_time = 0
        self.last_uploaded = 0

    def format_size(self, size):
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f"{size:.2f} {unit}"
            size /= 1024
        return f"{size:.2f} TB"

    def format_speed(self, speed):
        return self.format_size(speed) + "/s"

    def create_progress_bar(self, percentage):
        completed = int(percentage / 5)
        return "█" * completed + "░" * (20 - completed)

    def get_download_progress(self, download):
        return (
            f"⬇️ Downloading:\n"
            f"[{self.create_progress_bar(download.progress)}] {download.progress:.1f}%\n"
            f"Speed: {download.download_speed_string()}\n"
            f"ETA: {download.eta_string()}"
        )

    def get_upload_progress(self, current, total):
        percentage = (current * 100) / total if total > 0 else 0
        return (
            f"⬆️ Uploading:\n"
            f"[{self.create_progress_bar(percentage)}] {percentage:.1f}%\n"
            f"Uploaded: {self.format_size(current)} / {self.format_size(total)}"
        )
