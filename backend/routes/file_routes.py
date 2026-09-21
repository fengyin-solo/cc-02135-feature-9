"""文件路由"""
import os
import re
import uuid
import time
import logging
from flask import request, jsonify, send_file
from werkzeug.utils import secure_filename
from routes import files_bp
from database import get_db
from auth import verify_token, get_username_from_token, login_required
from config import UPLOAD_FOLDER, MAX_FILE_SIZE, BLOCKED_EXTENSIONS, SHARE_LINK_EXPIRE_HOURS, SHARE_LINK_MAX_DOWNLOADS

logger = logging.getLogger(__name__)


def allowed_file(filename):
    """检查文件扩展名是否被禁止"""
    if '.' not in filename:
        return False
    ext = filename.rsplit('.', 1)[1].lower()
    return ext not in BLOCKED_EXTENSIONS


@files_bp.route('/api/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': '没有文件'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': '未选择文件'}), 400

    if not allowed_file(file.filename):
        return jsonify({'error': '不支持的文件类型'}), 400

    file.seek(0, 2)
    file_size = file.tell()
    file.seek(0)

    if file_size > MAX_FILE_SIZE:
        return jsonify({'error': f'文件大小超过限制（最大{MAX_FILE_SIZE // 1024 // 1024}MB）'}), 400

    file_id = str(uuid.uuid4())
    # 保留原始文件名用于显示（去掉路径分隔符防止注入）
    original_name = re.sub(r'[/\\]', '_', file.filename).strip()
    if not original_name:
        original_name = file_id

    # 磁盘上用 UUID + 扩展名存储，避免文件名编码问题
    ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
    safe_filename = f"{file_id}.{ext}" if ext else file_id
    filepath = os.path.join(UPLOAD_FOLDER, safe_filename)
    file.save(filepath)

    file_size = os.path.getsize(filepath)

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO files (id, name, path, size) VALUES (?, ?, ?, ?)',
        (file_id, original_name, filepath, file_size)
    )
    conn.commit()
    conn.close()

    logger.info(f"文件上传成功: {original_name} (ID: {file_id}, 大小: {file_size} bytes)")
    return jsonify({'success': True, 'file_id': file_id, 'filename': original_name})


@files_bp.route('/api/files', methods=['GET'])
def list_files():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT id, name, path, size FROM files')
    files = [dict(row) for row in cursor.fetchall()]

    # 登录用户额外返回每个文件本人的分享状态摘要，供文件目录同步展示
    username = get_current_username()
    share_summary = {}
    if username:
        cursor.execute('''
            SELECT file_id,
                   SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active_count,
                   SUM(CASE WHEN status = 'disabled' THEN 1 ELSE 0 END) AS disabled_count
            FROM share_links
            WHERE created_by = ? AND deleted_at IS NULL
            GROUP BY file_id
        ''', (username,))
        share_summary = {
            row['file_id']: {
                'active': row['active_count'],
                'disabled': row['disabled_count']
            }
            for row in cursor.fetchall()
        }

    conn.close()

    for file_info in files:
        file_info['my_shares'] = share_summary.get(file_info['id'])

    return jsonify(files)


@files_bp.route('/api/download/<file_id>', methods=['GET'])
def download_file(file_id):
    # 优先从 Authorization 头获取 token，兼容查询参数（已废弃）
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        token = auth_header[7:]
    else:
        token = request.args.get('token')  # 向后兼容，建议前端迁移到 Authorization 头

    if not token or not verify_token(token):
        return jsonify({'error': '未授权或token已过期'}), 401

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT name, path FROM files WHERE id = ?', (file_id,))
    file_info = cursor.fetchone()
    conn.close()

    if not file_info:
        return jsonify({'error': '文件不存在'}), 404

    if not os.path.abspath(file_info['path']).startswith(os.path.abspath(UPLOAD_FOLDER)):
        return jsonify({'error': '非法文件路径'}), 403

    if not os.path.exists(file_info['path']):
        return jsonify({'error': '文件不存在'}), 404

    logger.info(f"文件下载: {file_info['name']} (ID: {file_id})")
    return send_file(file_info['path'], as_attachment=True, download_name=file_info['name'])


def generate_short_id():
    """生成短的分享链接ID"""
    return uuid.uuid4().hex[:12]


def get_share_link_info(share_id):
    """获取分享链接信息（公开视角，已删除的分享视为不存在），包含文件信息和有效性检查"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT s.id, s.file_id, s.created_by, s.expires_at, s.max_downloads, s.download_count, s.created_at,
               s.status, s.deleted_at,
               f.name as filename, f.size as filesize
        FROM share_links s
        JOIN files f ON s.file_id = f.id
        WHERE s.id = ? AND s.deleted_at IS NULL
    ''', (share_id,))
    share = cursor.fetchone()
    conn.close()
    return share


def is_share_valid(share):
    """检查分享链接是否有效"""
    if not share:
        return False, '分享链接不存在'

    if share['deleted_at'] is not None:
        return False, '分享链接已被删除'

    if share['status'] != 'active':
        return False, '分享链接已停用'

    if share['expires_at'] is not None and share['expires_at'] < time.time():
        return False, '分享链接已过期'

    if share['max_downloads'] is not None and share['download_count'] >= share['max_downloads']:
        return False, '分享链接下载次数已用完'

    return True, None


def increment_download_count(share_id):
    """增加下载次数"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        'UPDATE share_links SET download_count = download_count + 1 WHERE id = ?',
        (share_id,)
    )
    conn.commit()
    conn.close()


def get_token_from_request():
    """从请求中获取 token"""
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        return auth_header[7:]
    return request.args.get('token')


def get_current_username():
    """从请求中解析当前登录用户名，未登录或 token 无效时返回 None"""
    token = get_token_from_request()
    if not token:
        return None
    return get_username_from_token(token)


def get_manageable_share(share_id, username):
    """
    获取当前用户可管理（本人创建且未删除）的分享记录。

    越权/失效操作的中止路径：
    - 分享不存在或已被删除（含并发删除）-> (None, 404)
    - 分享属于他人 -> (None, 403)，当前用户只能改动自己的分享记录
    """
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        'SELECT id, file_id, created_by, status, deleted_at FROM share_links WHERE id = ?',
        (share_id,)
    )
    share = cursor.fetchone()
    conn.close()

    if not share or share['deleted_at'] is not None:
        return None, (jsonify({
            'error': '分享链接不存在或已被删除',
            'code': 'SHARE_GONE'
        }), 404)

    if share['created_by'] != username:
        return None, (jsonify({
            'error': '无权限操作此分享链接，只能管理本人创建的分享',
            'code': 'FORBIDDEN'
        }), 403)

    return share, None


@files_bp.route('/api/share', methods=['POST'])
@login_required
def create_share():
    """创建分享链接"""
    data = request.get_json()
    if not data:
        return jsonify({'error': '无效的请求数据'}), 400

    file_id = data.get('file_id', '').strip()
    expire_hours = data.get('expire_hours')
    max_downloads = data.get('max_downloads')

    if not file_id:
        return jsonify({'error': '文件ID不能为空'}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT id, name FROM files WHERE id = ?', (file_id,))
    file_info = cursor.fetchone()

    if not file_info:
        conn.close()
        return jsonify({'error': '文件不存在'}), 404

    if expire_hours is None:
        expire_hours = SHARE_LINK_EXPIRE_HOURS

    if expire_hours < 0:
        expire_hours = None

    if expire_hours is not None:
        expires_at = time.time() + expire_hours * 3600
    else:
        expires_at = None

    if max_downloads is None:
        max_downloads = SHARE_LINK_MAX_DOWNLOADS

    if max_downloads < 0:
        max_downloads = None

    token = get_token_from_request()
    username = get_username_from_token(token)

    share_id = generate_short_id()

    cursor.execute('''
        INSERT INTO share_links (id, file_id, created_by, expires_at, max_downloads)
        VALUES (?, ?, ?, ?, ?)
    ''', (share_id, file_id, username, expires_at, max_downloads))

    conn.commit()
    conn.close()

    logger.info(f"分享链接创建成功: 文件 {file_info['name']}, 分享ID {share_id}, 创建者 {username}")

    return jsonify({
        'success': True,
        'share_id': share_id,
        'expires_at': expires_at,
        'max_downloads': max_downloads,
        'filename': file_info['name']
    })


@files_bp.route('/api/share/<share_id>', methods=['GET'])
def get_share(share_id):
    """获取分享链接信息（公开访问）"""
    share = get_share_link_info(share_id)
    valid, error_msg = is_share_valid(share)

    if not share:
        return jsonify({'error': '分享链接不存在'}), 404

    share_data = {
        'share_id': share['id'],
        'filename': share['filename'],
        'filesize': share['filesize'],
        'created_by': share['created_by'],
        'expires_at': share['expires_at'],
        'max_downloads': share['max_downloads'],
        'download_count': share['download_count'],
        'created_at': share['created_at'],
        'is_valid': valid,
        'error_msg': error_msg
    }

    return jsonify(share_data)


@files_bp.route('/api/share/<share_id>/download', methods=['GET'])
def download_by_share(share_id):
    """通过分享链接下载文件（公开访问）"""
    share = get_share_link_info(share_id)
    valid, error_msg = is_share_valid(share)

    if not valid:
        return jsonify({'error': error_msg}), 404

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT name, path FROM files WHERE id = ?', (share['file_id'],))
    file_info = cursor.fetchone()
    conn.close()

    if not file_info:
        return jsonify({'error': '文件不存在'}), 404

    if not os.path.abspath(file_info['path']).startswith(os.path.abspath(UPLOAD_FOLDER)):
        return jsonify({'error': '非法文件路径'}), 403

    if not os.path.exists(file_info['path']):
        return jsonify({'error': '文件不存在'}), 404

    increment_download_count(share_id)

    logger.info(f"分享下载: 文件 {file_info['name']}, 分享ID {share_id}, 下载次数 {share['download_count'] + 1}")
    return send_file(file_info['path'], as_attachment=True, download_name=file_info['name'])


@files_bp.route('/api/shares', methods=['GET'])
@login_required
def list_shares():
    """
    获取分享列表。

    - 默认返回当前用户本人的分享（可管理）
    - scope=all 时同时返回他人创建的分享（只读，can_manage=false）
    - include_deleted=1 时额外返回本人已删除的分享（标记已删除状态）
    """
    username = get_current_username()
    scope = request.args.get('scope', 'mine')
    include_deleted = request.args.get('include_deleted') == '1'

    conditions = []
    params = []

    if scope == 'all':
        # 本人记录全量可见；他人记录仅未删除的可见（只读）
        if include_deleted:
            conditions.append('(s.created_by = ? OR s.deleted_at IS NULL)')
            params.append(username)
        else:
            conditions.append('s.deleted_at IS NULL')
    else:
        conditions.append('s.created_by = ?')
        params.append(username)
        if not include_deleted:
            conditions.append('s.deleted_at IS NULL')

    where_clause = ' AND '.join(conditions)

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(f'''
        SELECT s.id, s.file_id, s.created_by, s.expires_at, s.max_downloads, s.download_count, s.created_at,
               s.status, s.deleted_at,
               f.name as filename, f.size as filesize
        FROM share_links s
        JOIN files f ON s.file_id = f.id
        WHERE {where_clause}
        ORDER BY s.created_at DESC
    ''', params)
    shares = cursor.fetchall()
    conn.close()

    result = []
    for share in shares:
        valid, error_msg = is_share_valid(share)
        is_own = share['created_by'] == username
        is_deleted = share['deleted_at'] is not None
        result.append({
            'share_id': share['id'],
            'file_id': share['file_id'],
            'filename': share['filename'],
            'filesize': share['filesize'],
            'created_by': share['created_by'],
            'expires_at': share['expires_at'],
            'max_downloads': share['max_downloads'],
            'download_count': share['download_count'],
            'created_at': share['created_at'],
            'status': share['status'],
            'deleted_at': share['deleted_at'],
            'is_own': is_own,
            # 归属判断：本人且未删除才可管理；他人一律只读
            'can_manage': is_own and not is_deleted,
            'is_valid': valid,
            'error_msg': error_msg
        })

    return jsonify(result)


@files_bp.route('/api/share/<share_id>', methods=['DELETE'])
@login_required
def delete_share(share_id):
    """删除分享链接（软删除，保留已删除状态供列表展示与清理）"""
    username = get_current_username()

    share, error = get_manageable_share(share_id, username)
    if error:
        return error

    conn = get_db()
    cursor = conn.cursor()
    # 仅更新尚未删除的记录，并发删除时影响行数为 0，返回明确结果
    cursor.execute(
        'UPDATE share_links SET deleted_at = ? WHERE id = ? AND deleted_at IS NULL',
        (time.time(), share_id)
    )
    affected = cursor.rowcount
    conn.commit()
    conn.close()

    if affected == 0:
        return jsonify({
            'error': '分享链接已被删除（可能已被其他会话操作）',
            'code': 'SHARE_GONE'
        }), 404

    logger.info(f"分享链接删除: 分享ID {share_id}, 文件ID {share['file_id']}, 操作者 {username}")
    return jsonify({'success': True, 'message': '分享链接已删除', 'share_id': share_id})


@files_bp.route('/api/share/<share_id>/disable', methods=['POST'])
@login_required
def disable_share(share_id):
    """停用分享链接（保留记录，可随时恢复）"""
    username = get_current_username()

    share, error = get_manageable_share(share_id, username)
    if error:
        return error

    if share['status'] == 'disabled':
        return jsonify({'success': True, 'status': 'disabled', 'message': '分享链接已处于停用状态'})

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE share_links SET status = 'disabled' WHERE id = ? AND deleted_at IS NULL",
        (share_id,)
    )
    conn.commit()
    conn.close()

    logger.info(f"分享链接停用: 分享ID {share_id}, 操作者 {username}")
    return jsonify({'success': True, 'status': 'disabled', 'message': '分享链接已停用'})


@files_bp.route('/api/share/<share_id>/restore', methods=['POST'])
@login_required
def restore_share(share_id):
    """恢复已停用的分享链接"""
    username = get_current_username()

    share, error = get_manageable_share(share_id, username)
    if error:
        return error

    if share['status'] == 'active':
        return jsonify({'success': True, 'status': 'active', 'message': '分享链接已处于启用状态'})

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE share_links SET status = 'active' WHERE id = ? AND deleted_at IS NULL",
        (share_id,)
    )
    conn.commit()
    conn.close()

    logger.info(f"分享链接恢复: 分享ID {share_id}, 操作者 {username}")
    return jsonify({'success': True, 'status': 'active', 'message': '分享链接已恢复启用'})


@files_bp.route('/api/shares/cleanup', methods=['POST'])
@login_required
def cleanup_shares():
    """
    清理当前用户的历史分享：物理删除已删除、已过期或下载次数用完的记录。
    只清理本人记录，不影响他人分享。
    """
    username = get_current_username()
    now = time.time()

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        DELETE FROM share_links
        WHERE created_by = ?
          AND (
              deleted_at IS NOT NULL
              OR (expires_at IS NOT NULL AND expires_at < ?)
              OR (max_downloads IS NOT NULL AND download_count >= max_downloads)
          )
    ''', (username, now))
    cleaned = cursor.rowcount
    conn.commit()
    conn.close()

    logger.info(f"分享历史清理: 操作者 {username}, 清理数量 {cleaned}")
    return jsonify({
        'success': True,
        'cleaned': cleaned,
        'message': f'已清理 {cleaned} 条历史分享' if cleaned else '没有需要清空的历史分享'
    })
