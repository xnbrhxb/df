import logging
import os
import asyncio
from io import BytesIO
from PIL import Image, ImageOps
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.error import NetworkError, BadRequest, TimedOut, RetryAfter
import time
from typing import Optional

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", 
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(os.name)

# Store user images temporarily (unlimited)
user_images = {}
user_processing = {}  # Track users currently processing

# Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN", "7669240379:AAF1clhRBcd-yPHbo-_O_x6Yv0t1ggMIjbI")  # Replace with your bot token
PDF_QUALITY = 95  # High quality JPEG for images in PDF (1-100)
PDF_MARGIN = 30   # Optimized margin in points
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB max file size
MAX_IMAGE_DIMENSION = 4096  # Max image dimension to prevent memory issues


def safe_execute(func):
    """Decorator to safely execute functions with comprehensive error handling"""
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except RetryAfter as e:
            logger.warning(f"Rate limited, waiting {e.retry_after} seconds")
            await asyncio.sleep(e.retry_after)
            return await func(*args, **kwargs)
        except TimedOut:
            logger.warning("Request timed out, retrying...")
            await asyncio.sleep(1)
            return await func(*args, **kwargs)
        except NetworkError as e:
            logger.error(f"Network error: {e}")
            return None
        except BadRequest as e:
            logger.error(f"Bad request: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error in {func._name_}: {e}")
            return None
    return wrapper


def optimize_image(image: Image.Image) -> Image.Image:
    """Optimize image for PDF conversion"""
    try:
        # Fix orientation based on EXIF data
        image = ImageOps.exif_transpose(image)
        
        # Convert to RGB if necessary
        if image.mode not in ('RGB', 'L'):
            image = image.convert('RGB')
        
        # Resize if too large
        width, height = image.size
        if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
            image.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION), Image.Resampling.LANCZOS)
            logger.info(f"Resized image from {width}x{height} to {image.size}")
        
        return image
    except Exception as e:
        logger.error(f"Error optimizing image: {e}")
        return image


@safe_execute
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a message on /start"""
    user = update.effective_user
    welcome_message = (
        f"🎉 مرحباً {user.mention_html()}!\n\n"
        "🤖 أنا بوت تحويل الصور إلى PDF المطور\n\n"
        "✨ المميزات:\n"
        "• تجميع عدد غير محدود من الصور\n"
        "• تحويل عالي الجودة إلى PDF\n"
        "• معالجة ذكية للصور\n"
        "• سرعة فائقة في التحويل\n\n"
        "📋 الأوامر المتاحة:\n"
        "• أرسل صوراً وسأقوم بجمعها تلقائياً\n"
        "• /convert - تحويل الصور إلى PDF\n"
        "• /clear - مسح جميع الصور\n"
        "• /count - عرض عدد الصور\n"
        "• /help - عرض المساعدة\n"
        "• /status - حالة البوت\n\n"
        "🚀 ابدأ بإرسال صورك الآن!"
    )
    
    await update.message.reply_html(welcome_message)


@safe_execute
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send detailed help message"""
    help_text = (
        "📖 دليل استخدام البوت:\n\n"
        "🔸 /start - بدء المحادثة\n"
        "🔸 /help - عرض هذه المساعدة\n"
        "🔸 /convert - تحويل الصور المجمعة إلى PDF\n"
        "🔸 /clear - مسح جميع الصور المجمعة\n"
        "🔸 /count - عرض عدد الصور المجمعة\n"
        "🔸 /status - عرض حالة البوت\n\n"
        "📷 كيفية الاستخدام:\n"
        "1️⃣ أرسل صورة أو عدة صور\n"
        "2️⃣ استخدم /convert لتحويلها إلى PDF\n"
        "3️⃣ احصل على ملف PDF عالي الجودة\n\n"
        "💡 نصائح:\n"
        "• يمكنك إرسال عدد غير محدود من الصور\n"
        "• البوت يحسن جودة الصور تلقائياً\n"
        "• يدعم جميع تنسيقات الصور الشائعة\n"
        "• معالجة سريعة وآمنة"
    )
    
    await update.message.reply_text(help_text)


@safe_execute
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show bot status"""
    user_id = update.effective_user.id
    total_users = len(user_images)
    user_image_count = len(user_images.get(user_id, []))
    is_processing = user_id in user_processing
    
    status_text = (
        "📊 حالة البوت:\n\n"
        f"👤 المستخدمون النشطون: {total_users}\n"
        f"📷 صورك المجمعة: {user_image_count}\n"
        f"⚡ حالة المعالجة: {'جاري المعالجة...' if is_processing else 'جاهز'}\n"
        f"🔧 إصدار البوت: v2.0 المطور\n"
        f"✅ الحالة: متصل ويعمل بكفاءة"
    )
    
    await update.message.reply_text(status_text)


@safe_execute
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming photos with comprehensive error handling"""
    user_id = update.effective_user.id
    
    # Check if user is currently processing
    if user_id in user_processing:
        await update.message.reply_text(
            "⏳ يرجى انتظار انتهاء العملية الحالية قبل إضافة صور جديدة."
        )
        return
    
    # Initialize user's image list if not exists
    if user_id not in user_images:
        user_images[user_id] = []
    
    try:
        # Get the largest photo size
        photo = update.message.photo[-1]
        
        # Check file size
        if photo.file_size and photo.file_size > MAX_FILE_SIZE:
            await update.message.reply_text(
                "❌ حجم الصورة كبير جداً. الحد الأقصى المسموح: 50 ميجابايت."
            )
            return
        
        # Show downloading message for large files
        download_msg = None
        if photo.file_size and photo.file_size > 5 * 1024 * 1024:  # 5MB
            download_msg = await update.message.reply_text("📥 جاري تحميل الصورة...")
        
        # Download the photo with retry mechanism
        max_retries = 3
        photo_bytes = None
        
        for attempt in range(max_retries):
            try:
                photo_file = await photo.get_file()
                photo_bytes = await photo_file.download_as_bytearray()
                break
            except Exception as e:
                if attempt == max_retries - 1:
                    raise e
                await asyncio.sleep(1)
        
        if photo_bytes is None:
            await update.message.reply_text("❌ فشل في تحميل الصورة. يرجى المحاولة مرة أخرى.")
            return
        
        # Validate image
        try:
            test_image = Image.open(BytesIO(photo_bytes))
            test_image.verify()
        except Exception:
            await update.message.reply_text("❌ الملف المرسل ليس صورة صالحة.")
            return
        
        # Store the image
        user_images[user_id].append(photo_bytes)
        
        # Delete download message if exists
        if download_msg:
            try:
                await download_msg.delete()
            except:
                pass
        
        # Success message with enhanced info
        count = len(user_images[user_id])
        size_mb = len(photo_bytes) / 1024 / 1024
        
        success_text = (
            f"✅ تم إضافة الصورة بنجاح!\n\n"
            f"📊 الإحصائيات:\n"
            f"• العدد الإجمالي: {count} صورة\n"
            f"• حجم هذه الصورة: {size_mb:.1f} ميجابايت\n\n"
            f"💡 يمكنك:\n"
            f"• إرسال المزيد من الصور\n"
            f"• استخدام /convert للتحويل إلى PDF\n"
            f"• استخدام /clear لمسح الصور"
        )
        
        await update.message.reply_text(success_text)
        
    except Exception as e:
        logger.error(f"Error in handle_photo: {e}")
        await update.message.reply_text(
            "❌ حدث خطأ أثناء معالجة الصورة. يرجى المحاولة مرة أخرى.\n"
            "إذا استمرت المشكلة، تأكد من أن الملف المرسل صورة صالحة."
        )


@safe_execute
async def convert_to_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Convert stored images to PDF with advanced processing"""
    user_id = update.effective_user.id
    
    # Check if user has images
    if user_id not in user_images or not user_images[user_id]:
        await update.message.reply_text(
            "📷 لا توجد صور لتحويلها!\n\n"
            "💡 أرسل بعض الصور أولاً ثم استخدم /convert"
        )
        return
    
    # Check if already processing
    if user_id in user_processing:
        await update.message.reply_text("⏳ عملية تحويل أخرى قيد التنفيذ. يرجى الانتظار.")
        return
    
    # Mark user as processing
    user_processing[user_id] = True
    
    try:
        total_images = len(user_images[user_id])
        
        # Send initial processing message
        processing_message = await update.message.reply_text(
            f"🔄 بدء تحويل {total_images} صورة إلى PDF...\n"
            f"⏱ الوقت المتوقع: {total_images * 2} ثانية\n"
            f"📊 جودة عالية مضمونة!"
        )
        
        # Create PDF with optimized settings
        pdf_buffer = BytesIO()
        c = canvas.Canvas(pdf_buffer, pagesize=A4)
        page_width, page_height = A4
        
        successful_images = 0
        failed_images = 0
        
        for i, image_bytes in enumerate(user_images[user_id]):
            try:
                # Update progress for every 5 images or if total > 10
                if (total_images > 10 and (i + 1) % 5 == 0) or (total_images <= 10 and (i + 1) % 2 == 0):
                    progress = int((i + 1) / total_images * 100)
                    await processing_message.edit_text(
                        f"🔄 معالجة الصور... {progress}%\n"
                        f"📷 الصورة {i + 1} من {total_images}\n"
                        f"✅ تم بنجاح: {successful_images}\n"
                        f"⚡ جاري التحسين والمعالجة..."
                    )
                
                # Open and optimize image
                image = Image.open(BytesIO(image_bytes))
                image = optimize_image(image)
                
                # Calculate optimal dimensions
                img_width, img_height = image.size
                aspect_ratio = img_width / img_height
                
                # Calculate new dimensions to fit page with margins
                margin = PDF_MARGIN
                max_width = page_width - 2 * margin
                max_height = page_height - 2 * margin
                
                if aspect_ratio > 1:  # Landscape
                    new_width = min(max_width, img_width)
                    new_height = new_width / aspect_ratio
                    if new_height > max_height:
                        new_height = max_height
                        new_width = new_height * aspect_ratio
                else:  # Portrait
                    new_height = min(max_height, img_height)
                    new_width = new_height * aspect_ratio
                    if new_width > max_width:
                        new_width = max_width
                        new_height = new_width / aspect_ratio
                
                # Center the image on the page
                x = (page_width - new_width) / 2
                y = (page_height - new_height) / 2
                
                # Create high-quality ImageReader object
                image_buffer = BytesIO()
                image.save(image_buffer, format='JPEG', quality=PDF_QUALITY, optimize=True)
                image_buffer.seek(0)
                img_reader = ImageReader(image_buffer)
                
                # Draw image on PDF
                c.drawImage(img_reader, x, y, width=new_width, height=new_height)
                
                # Add new page if not the last image
                if i < len(user_images[user_id]) - 1:
                    c.showPage()
                
                successful_images += 1
                
            except Exception as e:
                logger.error(f"Error processing image {i + 1}: {e}")
                failed_images += 1
                # Continue with next image instead of failing completely
                continue
        
        # Finalize PDF
        c.save()
        pdf_buffer.seek(0)
        
        # Update final processing message
        await processing_message.edit_text(
            f"✅ اكتمل التحويل!\n"
            f"📄 تم إنشاء ملف PDF بنجاح\n"
            f"📊 {successful_images} صورة تم تحويلها\n"
            f"📤 جاري الإرسال..."
        )
        
        # Calculate file size
        file_size_mb = len(pdf_buffer.getvalue()) / 1024 / 1024
        
        # Send PDF file with comprehensive caption
        caption = (
            f"🎉 تم التحويل بنجاح!\n\n"
            f"📊 تفاصيل الملف:\n"
            f"• عدد الصور: {successful_images}\n"
            f"• حجم الملف: {file_size_mb:.2f} ميجابايت\n"
            f"• الجودة: عالية ({PDF_QUALITY}%)\n"
            f"• التاريخ: {time.strftime('%Y-%m-%d %H:%M')}\n\n"
            f"✨ ملف PDF عالي الجودة جاهز للاستخدام!"
        )
        
        if failed_images > 0:
            caption += f"\n⚠ تعذر معالجة {failed_images} صورة"
        
        await update.message.reply_document(
            document=pdf_buffer,
            filename=f"converted_images_{user_id}_{int(time.time())}.pdf",
            caption=caption
        )
        
        # Delete processing message
        try:
            await processing_message.delete()
        except:
            pass
        
        # Clear user images after successful conversion
        del user_images[user_id]
        
        # Send completion message
        await update.message.reply_text(
            "🎊 العملية مكتملة!\n\n"
            "💡 يمكنك الآن:\n"
            "• إرسال صور جديدة لتحويل آخر\n"
            "• استخدام /help للمساعدة\n"
            "• مشاركة البوت مع الأصدقاء!"
        )
        
    except Exception as e:
        logger.error(f"Error in convert_to_pdf: {e}")
        await update.message.reply_text(
            "❌ حدث خطأ أثناء تحويل الصور إلى PDF.\n\n"
            "🔧 الحلول المقترحة:\n"
            "• تأكد من صحة الصور المرسلة\n"
            "• قلل عدد الصور إذا كان كبيراً جداً\n"
            "• أعد المحاولة بعد قليل\n"
            "• استخدم /clear ثم أرسل الصور مرة أخرى"
        )
    finally:
        # Remove user from processing
        if user_id in user_processing:
            del user_processing[user_id]


@safe_execute
async def clear_images(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear user's stored images"""
    user_id = update.effective_user.id
    
    if user_id in user_processing:
        await update.message.reply_text("⏳ لا يمكن مسح الصور أثناء المعالجة. يرجى الانتظار.")
        return
    
    if user_id in user_images and user_images[user_id]:
        count = len(user_images[user_id])
        del user_images[user_id]
        await update.message.reply_text(
            f"🗑 تم مسح {count} صورة بنجاح!\n\n"
            f"✨ يمكنك الآن إرسال صور جديدة."
        )
    else:
        await update.message.reply_text(
            "📷 لا توجد صور مجمعة لمسحها.\n\n"
            "💡 أرسل بعض الصور لتبدأ!"
        )


@safe_execute
async def count_images(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show count of stored images with details"""
    user_id = update.effective_user.id
    
    if user_id in user_images and user_images[user_id]:
        count = len(user_images[user_id])
        total_size = sum(len(img) for img in user_images[user_id]) / 1024 / 1024
        
        count_text = (
            f"📊 إحصائيات صورك:\n\n"
            f"📷 عدد الصور: {count}\n"
            f"💾 الحجم الإجمالي: {total_size:.1f} ميجابايت\n"
            f"📄 صفحات PDF متوقعة: {count}\n\n"
            f"⚡ جاهز للتحويل باستخدام /convert"
        )
    else:
        count_text = (
            "📷 لا توجد صور مجمعة حالياً.\n\n"
            "🚀 ابدأ بإرسال صورك الآن!"
        )
    
    await update.message.reply_text(count_text)


@safe_execute
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Comprehensive error handler"""
    logger.error(f"Exception while handling an update: {context.error}", exc_info=context.error)
    
    # Send user-friendly error message
    if isinstance(update, Update) and update.effective_message:
        error_text = (
            "🔧 حدث خطأ تقني مؤقت.\n\n"
            "✅ تم تسجيل المشكلة وسيتم حلها\n"
            "🔄 يرجى إعادة المحاولة\n\n"
            "💡 إذا استمرت المشكلة:\n"
            "• استخدم /clear لمسح البيانات\n"
            "• أعد تشغيل المحادثة بـ /start"
        )
        
        try:
            await update.effective_message.reply_text(error_text)
        except Exception as e:
            logger.error(f"Failed to send error message: {e}")


def main() -> None:
    """Start the bot with enhanced configuration"""
    if BOT_TOKEN == 'YOUR_BOT_TOKEN':
        print("❌ يرجى تعيين BOT_TOKEN في متغيرات البيئة")
        return
    
    # Create application with optimized settings
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)
        .build()
    )

    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("convert", convert_to_pdf))
    application.add_handler(CommandHandler("clear", clear_images))
    application.add_handler(CommandHandler("count", count_images))
    application.add_handler(CommandHandler("status", status_command))
    
    # Handle photos
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    
    # Add comprehensive error handler
    application.add_error_handler(error_handler)

    # Start the bot
    logger.info("🚀 Bot started successfully!")
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()