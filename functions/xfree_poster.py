"""
Модуль для публикации контента на xfree.com
Использует Playwright для автоматизации браузера
"""

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from firebase_admin import storage
import tempfile
import os
import logging
import time

logger = logging.getLogger(__name__)


def publish_to_xfree(post_id: str, post_data: dict, db) -> None:
    """
    Публикует пост на xfree.com используя Playwright
    
    Args:
        post_id: ID документа поста в Firestore
        post_data: Данные поста (title, description, video_url, account_id)
        db: Firestore client
    
    Raises:
        Exception: Если публикация не удалась
    """
    logger.info(f"🚀 Starting publication for post {post_id}")
    
    # Получаем данные аккаунта
    account_ref = db.collection('accounts').document(post_data['account_id'])
    account_doc = account_ref.get()
    
    if not account_doc.exists:
        raise Exception(f"Account {post_data['account_id']} not found")
    
    account = account_doc.to_dict()
    logger.info(f"📧 Using account: {account['email']}")
    
    # Скачиваем видео из Storage
    video_path = download_video_from_storage(post_data['video_url'])
    logger.info(f"📥 Video downloaded to: {video_path}")
    
    try:
        # Запускаем браузер и публикуем
        with sync_playwright() as p:
            browser = launch_browser(p, account.get('proxy'))
            context = browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            )
            page = context.new_page()
            
            try:
                # Логин
                login_to_xfree(page, account['email'], account['password'])
                logger.info("✅ Logged in successfully")
                
                # Публикация
                publish_video(page, video_path, post_data['title'], post_data['description'])
                logger.info("✅ Video published successfully")
                
            finally:
                browser.close()
                
    finally:
        # Удаляем временный файл
        if os.path.exists(video_path):
            os.unlink(video_path)
            logger.info("🗑️ Temporary video file deleted")


def download_video_from_storage(video_url: str) -> str:
    """
    Скачивает видео из Firebase Storage во временный файл
    
    Args:
        video_url: URL видео в формате gs://bucket/path/to/video.mp4
    
    Returns:
        Путь к временному файлу
    """
    bucket = storage.bucket()
    
    # Извлекаем путь к файлу из URL
    blob_path = video_url.replace(f'gs://{bucket.name}/', '')
    blob = bucket.blob(blob_path)
    
    # Создаем временный файл
    suffix = os.path.splitext(blob_path)[1] or '.mp4'
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
        blob.download_to_filename(tmp_file.name)
        return tmp_file.name


def launch_browser(playwright, proxy=None):
    """
    Запускает браузер Chromium с необходимыми настройками
    
    Args:
        playwright: Playwright instance
        proxy: Прокси-сервер (опционально)
    
    Returns:
        Browser instance
    """
    browser_args = {
        "headless": True,
        "args": [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled"
        ]
    }
    
    if proxy:
        browser_args["proxy"] = {"server": proxy}
        logger.info(f"🌐 Using proxy: {proxy}")
    
    return playwright.chromium.launch(**browser_args)


def login_to_xfree(page, email: str, password: str) -> None:
    """
    Выполняет вход на xfree.com
    
    Args:
        page: Playwright Page instance
        email: Email аккаунта
        password: Пароль аккаунта
    
    Raises:
        Exception: Если вход не удался
    """
    logger.info("🔐 Logging in to xfree.com...")
    
    try:
        # Переходим на страницу логина
        page.goto("https://www.xfree.com/login", wait_until="domcontentloaded", timeout=30000)
        time.sleep(2)
        
        # Ждем форму логина
        page.wait_for_selector('input[name="email"], input[type="email"]', timeout=10000)
        
        # Заполняем форму
        page.fill('input[name="email"], input[type="email"]', email)
        page.fill('input[name="password"], input[type="password"]', password)
        
        # Нажимаем кнопку входа
        page.click('button[type="submit"]')
        
        # Ждем загрузки после логина
        page.wait_for_load_state('networkidle', timeout=30000)
        time.sleep(3)
        
        # Проверяем, что вход выполнен успешно
        if "login" in page.url.lower():
            raise Exception("Login failed - still on login page")
        
        logger.info("✅ Login successful")
        
    except PlaywrightTimeout as e:
        raise Exception(f"Login timeout: {str(e)}")
    except Exception as e:
        raise Exception(f"Login error: {str(e)}")


def publish_video(page, video_path: str, title: str, description: str) -> None:
    """
    Публикует видео на xfree.com
    
    Args:
        page: Playwright Page instance
        video_path: Путь к видео файлу
        title: Заголовок поста
        description: Описание поста
    
    Raises:
        Exception: Если публикация не удалась
    """
    logger.info("📤 Publishing video...")
    
    try:
        # Переходим на страницу загрузки
        # ВНИМАНИЕ: URL может отличаться, нужно проверить на реальном сайте
        page.goto("https://www.xfree.com/upload", wait_until="domcontentloaded", timeout=30000)
        time.sleep(2)
        
        # Ждем форму загрузки
        page.wait_for_selector('input[type="file"]', timeout=10000)
        
        # Загружаем видео
        logger.info(f"📁 Uploading video file: {video_path}")
        page.set_input_files('input[type="file"]', video_path)
        
        # Ждем завершения загрузки (может занять время)
        time.sleep(5)
        
        # Заполняем форму
        # ВНИМАНИЕ: Селекторы могут отличаться, нужно проверить на реальном сайте
        title_selector = 'input[name="title"], input[placeholder*="title" i]'
        if page.locator(title_selector).count() > 0:
            page.fill(title_selector, title)
            logger.info(f"✏️ Title filled: {title}")
        
        desc_selector = 'textarea[name="description"], textarea[placeholder*="description" i]'
        if page.locator(desc_selector).count() > 0:
            page.fill(desc_selector, description)
            logger.info(f"✏️ Description filled")
        
        # Ждем завершения обработки видео
        time.sleep(10)
        
        # Публикуем
        submit_selector = 'button[type="submit"], button:has-text("Publish"), button:has-text("Post")'
        page.click(submit_selector)
        
        # Ждем подтверждения публикации
        page.wait_for_load_state('networkidle', timeout=60000)
        time.sleep(5)
        
        logger.info("✅ Video published successfully")
        
    except PlaywrightTimeout as e:
        raise Exception(f"Publish timeout: {str(e)}")
    except Exception as e:
        raise Exception(f"Publish error: {str(e)}")


# Альтернативная функция для публикации через API (если xfree.com предоставляет API)
def publish_via_api(post_data: dict, account: dict) -> None:
    """
    Публикует пост через API xfree.com (если доступно)
    
    ВНИМАНИЕ: Эта функция является примером и требует реального API endpoint
    """
    import requests
    
    # Пример запроса (нужно заменить на реальный API)
    api_url = "https://api.xfree.com/v1/posts"
    
    headers = {
        "Authorization": f"Bearer {account.get('api_token')}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "title": post_data['title'],
        "description": post_data['description'],
        "video_url": post_data['video_url']
    }
    
    response = requests.post(api_url, json=payload, headers=headers)
    
    if response.status_code != 201:
        raise Exception(f"API error: {response.status_code} - {response.text}")
    
    logger.info("✅ Published via API")
