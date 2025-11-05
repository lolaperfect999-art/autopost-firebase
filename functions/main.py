"""
Firebase Cloud Functions для системы автопостинга
Автоматически публикует контент на xfree.com по расписанию
"""

from firebase_functions import scheduler_fn, https_fn
from firebase_admin import initialize_app, firestore, storage
from datetime import datetime
import logging
import os

# Инициализация Firebase Admin
initialize_app()
db = firestore.client()

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@scheduler_fn.on_schedule(schedule="every 1 minutes")
def check_scheduled_posts(event: scheduler_fn.ScheduledEvent) -> None:
    """
    Проверяет запланированные посты каждую минуту
    и публикует те, у которых наступило время
    """
    logger.info("🔍 Checking for scheduled posts...")
    
    now = datetime.utcnow()
    
    # Получаем посты со статусом 'pending' и временем <= текущего
    posts_ref = db.collection('posts')
    query = posts_ref.where('status', '==', 'pending')\
                     .where('scheduled_time', '<=', now)\
                     .limit(10)
    
    posts = query.stream()
    
    count = 0
    for post in posts:
        count += 1
        post_data = post.to_dict()
        logger.info(f"📝 Found post to publish: {post.id}")
        
        # Обновляем статус на 'processing'
        post.reference.update({
            'status': 'processing',
            'processing_started_at': datetime.utcnow()
        })
        
        # Публикуем пост
        try:
            from xfree_poster import publish_to_xfree
            publish_to_xfree(post.id, post_data, db)
            
            # Обновляем статус на 'published'
            post.reference.update({
                'status': 'published',
                'published_at': datetime.utcnow(),
                'error': None
            })
            logger.info(f"✅ Post {post.id} published successfully")
            
        except Exception as e:
            logger.error(f"❌ Error publishing post {post.id}: {str(e)}")
            
            # Обновляем статус на 'failed'
            post.reference.update({
                'status': 'failed',
                'error': str(e),
                'failed_at': datetime.utcnow()
            })
    
    if count == 0:
        logger.info("ℹ️ No posts to publish at this time")
    else:
        logger.info(f"✅ Processed {count} posts")


@https_fn.on_request()
def create_post(req: https_fn.Request) -> https_fn.Response:
    """
    HTTP endpoint для создания нового поста
    
    POST /create_post
    Body: {
        "account_id": "account_doc_id",
        "title": "Post title",
        "description": "Post description",
        "video_url": "gs://bucket/video.mp4",
        "scheduled_time": "2025-11-03T15:00:00"
    }
    """
    # CORS headers
    if req.method == 'OPTIONS':
        headers = {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST',
            'Access-Control-Allow-Headers': 'Content-Type',
        }
        return https_fn.Response('', status=204, headers=headers)
    
    if req.method != 'POST':
        return https_fn.Response("Method not allowed", status=405)
    
    try:
        data = req.get_json()
        
        # Валидация данных
        required_fields = ['account_id', 'title', 'description', 'video_url', 'scheduled_time']
        if not all(field in data for field in required_fields):
            return https_fn.Response(
                f"Missing required fields. Required: {', '.join(required_fields)}", 
                status=400
            )
        
        # Создаем документ в Firestore
        post_ref = db.collection('posts').document()
        post_ref.set({
            'account_id': data['account_id'],
            'title': data['title'],
            'description': data['description'],
            'video_url': data['video_url'],
            'scheduled_time': datetime.fromisoformat(data['scheduled_time'].replace('Z', '+00:00')),
            'status': 'pending',
            'platform': data.get('platform', 'xfree'),
            'created_at': datetime.utcnow(),
            'published_at': None,
            'error': None
        })
        
        logger.info(f"✅ Post created: {post_ref.id}")
        
        headers = {'Access-Control-Allow-Origin': '*'}
        return https_fn.Response(
            f'{{"success": true, "post_id": "{post_ref.id}"}}',
            status=201,
            headers=headers,
            mimetype='application/json'
        )
        
    except Exception as e:
        logger.error(f"❌ Error creating post: {str(e)}")
        headers = {'Access-Control-Allow-Origin': '*'}
        return https_fn.Response(
            f'{{"success": false, "error": "{str(e)}"}}',
            status=500,
            headers=headers,
            mimetype='application/json'
        )


@https_fn.on_request()
def get_posts(req: https_fn.Request) -> https_fn.Response:
    """
    HTTP endpoint для получения списка постов
    
    GET /get_posts?status=pending&limit=10
    """
    # CORS headers
    if req.method == 'OPTIONS':
        headers = {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET',
            'Access-Control-Allow-Headers': 'Content-Type',
        }
        return https_fn.Response('', status=204, headers=headers)
    
    if req.method != 'GET':
        return https_fn.Response("Method not allowed", status=405)
    
    try:
        # Параметры запроса
        status = req.args.get('status', 'all')
        limit = int(req.args.get('limit', 50))
        
        # Запрос к Firestore
        posts_ref = db.collection('posts')
        
        if status != 'all':
            query = posts_ref.where('status', '==', status).limit(limit)
        else:
            query = posts_ref.limit(limit)
        
        # Сортировка по времени создания
        query = query.order_by('created_at', direction=firestore.Query.DESCENDING)
        
        posts = query.stream()
        
        # Формируем ответ
        result = []
        for post in posts:
            post_data = post.to_dict()
            post_data['id'] = post.id
            
            # Конвертируем datetime в строку
            for field in ['created_at', 'scheduled_time', 'published_at', 'failed_at', 'processing_started_at']:
                if field in post_data and post_data[field]:
                    post_data[field] = post_data[field].isoformat()
            
            result.append(post_data)
        
        headers = {'Access-Control-Allow-Origin': '*'}
        import json
        return https_fn.Response(
            json.dumps({'success': True, 'posts': result, 'count': len(result)}),
            status=200,
            headers=headers,
            mimetype='application/json'
        )
        
    except Exception as e:
        logger.error(f"❌ Error getting posts: {str(e)}")
        headers = {'Access-Control-Allow-Origin': '*'}
        import json
        return https_fn.Response(
            json.dumps({'success': False, 'error': str(e)}),
            status=500,
            headers=headers,
            mimetype='application/json'
        )


@https_fn.on_request()
def retry_failed_post(req: https_fn.Request) -> https_fn.Response:
    """
    HTTP endpoint для повторной попытки публикации неудавшегося поста
    
    POST /retry_failed_post
    Body: {"post_id": "post_doc_id"}
    """
    # CORS headers
    if req.method == 'OPTIONS':
        headers = {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST',
            'Access-Control-Allow-Headers': 'Content-Type',
        }
        return https_fn.Response('', status=204, headers=headers)
    
    if req.method != 'POST':
        return https_fn.Response("Method not allowed", status=405)
    
    try:
        data = req.get_json()
        
        if 'post_id' not in data:
            return https_fn.Response("Missing post_id", status=400)
        
        post_id = data['post_id']
        
        # Получаем пост
        post_ref = db.collection('posts').document(post_id)
        post = post_ref.get()
        
        if not post.exists:
            return https_fn.Response("Post not found", status=404)
        
        # Сбрасываем статус на pending
        post_ref.update({
            'status': 'pending',
            'error': None,
            'scheduled_time': datetime.utcnow()  # Публикуем немедленно
        })
        
        logger.info(f"🔄 Post {post_id} reset to pending for retry")
        
        headers = {'Access-Control-Allow-Origin': '*'}
        return https_fn.Response(
            f'{{"success": true, "message": "Post reset to pending"}}',
            status=200,
            headers=headers,
            mimetype='application/json'
        )
        
    except Exception as e:
        logger.error(f"❌ Error retrying post: {str(e)}")
        headers = {'Access-Control-Allow-Origin': '*'}
        return https_fn.Response(
            f'{{"success": false, "error": "{str(e)}"}}',
            status=500,
            headers=headers,
            mimetype='application/json'
        )
