from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import MessageNotModified
from aria2p import API, Client as ariaClient
import os
import asyncio
import time
from pathlib import Path
import mimetypes
import subprocess

DOWNLOAD_DIR = Path("Downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

app = Client(
    "my_bot",
    api_id="your_api_id",
    api_hash="your_api_hash",
    bot_token="your_bot_token"
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

def get_mime_type(file_path):
    return mimetypes.guess_type(file_path)[0] or "application/octet-stream"

def create_progress_bar(percentage):
    completed = int(percentage / 5)
    return "█" * completed + "░" * (20 - completed)

@app.on_message(filters.command("start"))
async def start_command(client, message):
    await message.reply_text("Send me a URL to download and upload as media.")

@app.on_callback_query(filters.regex("^cancel"))
async def cancel_process(client, callback_query: CallbackQuery):
    try:
        data = callback_query.data.split("_")
        if len(data) > 1:
            gid = data[1]
            download = aria_api.get_download(gid)
            if download:
                download.remove(force=True, files=True)
        await callback_query.message.edit_text("❌ Process cancelled!")
    except:
        pass

@app.on_message(filters.text & filters.regex(r'https?://[^\s]+'))
async def handle_url(client, message):
    url = message.text
    cancel_button = InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="cancel")]])
    progress_msg = await message.reply_text("⬇️ Starting download...", reply_markup=cancel_button)
    
    try:
        download = aria_api.add_uris([url], {'dir': str(DOWNLOAD_DIR)})
        cancel_button = InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{download.gid}")]])
        
        last_progress = None
        while not download.is_complete:
            download.update()
            current_progress = f"⬇️ Downloading:\n" \
                             f"[{create_progress_bar(download.progress)}] {download.progress:.1f}%\n" \
                             f"Speed: {download.download_speed_string()}\n" \
                             f"ETA: {download.eta_string()}"
            
            if current_progress != last_progress:
                try:
                    await progress_msg.edit_text(current_progress, reply_markup=cancel_button)
                    last_progress = current_progress
                except MessageNotModified:
                    pass  # Ignore if message content hasn't changed
                
            await asyncio.sleep(3)
            
            if not aria_api.get_download(download.gid):
                return
        
        file_path = download.files[0].path
        mime_type = get_mime_type(file_path)
        
        await progress_msg.edit_text("⬆️ Starting upload...", reply_markup=cancel_button)
        start_time = time.time()
        last_update_time = start_time
        last_uploaded = 0
        last_progress_text = None
        upload_cancelled = False
        
        async def progress(current, total):
            nonlocal last_update_time, last_uploaded, last_progress_text
            now = time.time()
            
            if now - last_update_time > 2:
                speed = (current - last_uploaded) / (now - last_update_time)
                percentage = (current * 100) / total
                progress_bar = create_progress_bar(percentage)
                
                current_progress = f"⬆️ Uploading:\n" \
                                 f"[{progress_bar}] {percentage:.1f}%\n" \
                                 f"Speed: {format_speed(speed)}\n" \
                                 f"Uploaded: {format_size(current)} / {format_size(total)}"
                
                if current_progress != last_progress_text:
                    try:
                        await progress_msg.edit_text(current_progress, reply_markup=cancel_button)
                        last_progress_text = current_progress
                    except MessageNotModified:
                        pass  # Ignore if message content hasn't changed
                
                last_update_time = now
                last_uploaded = current
        
        try:
            await message.reply_document(
                document=file_path, 
                thumb=None,
                force_document=False,
                progress=progress
            )
        except Exception as e:
            upload_cancelled = True
            await progress_msg.edit_text("❌ Upload cancelled!")
        
        os.remove(file_path)
        if not upload_cancelled:
            await progress_msg.delete()
            await message.reply_text("✅ Complete!")
        
    except Exception as e:
        await progress_msg.edit_text(f"❌ Error: {str(e)}")

if __name__ == "__main__":
    subprocess.Popen(["aria2c", "--enable-rpc", "--rpc-listen-all=true", "--rpc-allow-origin-all", "--rpc-listen-port=6800", "--disable-ipv6"])
    app.run()
