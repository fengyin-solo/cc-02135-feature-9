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
    """文件目录，附带当前用户在各文件上的分享状态（用于前端同步展示）"""
    username = None
    token = get_token_from_request()
    if token:
        username = get_username_from_token(token)

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT f.id, f.name, f.path, f.size,
               (SELECT COUNT(*) FROM share_links s
                 WHERE s.file_id = f.id AND s.created_by = ? AND s.status = 'active') AS my_active_shares,
               (SELECT COUNT(*) FROM share_links s
                 WHERE s.file_id = f.id AND s.created_by = ? AND s.status = 'disabled') AS my_disabled_shares
        FROM files f
    ''', (username or '', username or ''))
    files = [dict(row) for row in cursor.fetchall()]
    conn.close()

    for f in files:
        if f['my_active_shares'] > 0:
            f['my_share_status'] = 'active'
        elif f['my_disabled_shares'] > 0:
            f['my_share_status'] = 'disabled'
        else:
            f['my_share_status'] = None

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
    """获取分享链接信息，包含文件信息和有效性检查"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT s.id, s.file_id, s.created_by, s.expires_at, s.max_downloads, s.download_count,
               s.status, s.created_at,
               f.name as filename, f.size as filesize
        FROM share_links s
        JOIN files f ON s.file_id = f.id
        WHERE s.id = ?
    ''', (share_id,))
    share = cursor.fetchone()
    conn.close()
    return share


def is_share_valid(share):
    """检查分享链接是否有效（状态 + 有效期 + 下载次数）"""
    if not share:
        return False, '分享链接不存在'

    if share['status'] == 'deleted':
        return False, '分享链接已被删除'

    if share['status'] == 'disabled':
        return False, '分享链接已被停用'

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
    """获取分享链接信息（公开访问，已删除的分享对外不可见）"""
    share = get_share_link_info(share_id)

    if not share or share['status'] == 'deleted':
        return jsonify({'error': '分享链接不存在或已被删除'}), 404

    valid, error_msg = is_share_valid(share)

    share_data = {
        'share_id': share['id'],
        'filename': share['filename'],
        'filesize': share['filesize'],
        'created_by': share['created_by'],
        'expires_at': share['expires_at'],
        'max_downloads': share['max_downloads'],
        'download_count': share['download_count'],
        'created_at': share['created_at'],
        'status': share['status'],
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


SHARE_STATUS_TEXT = {
    'active': '有效',
    'disabled': '已停用',
    'deleted': '已删除',
}


def serialize_share_for_list(share, username):
    """序列化分享记录，附带归属判断结果（本人可管理 / 他人只读）"""
    valid, error_msg = is_share_valid(share)
    is_own = share['created_by'] == username
    return {
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
        'status_text': SHARE_STATUS_TEXT.get(share['status'], share['status']),
        'ownership': 'own' if is_own else 'other',
        'can_manage': is_own and share['status'] != 'deleted',
        'is_valid': valid,
        'error_msg': error_msg
    }


@files_bp.route('/api/shares', methods=['GET'])
@login_required
def list_shares():
    """分享管理列表：本人记录（含已删除历史）可管理，他人有效记录只读"""
    token = get_token_from_request()
    username = get_username_from_token(token)

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT s.id, s.file_id, s.created_by, s.expires_at, s.max_downloads, s.download_count,
               s.status, s.created_at,
               f.name as filename, f.size as filesize
        FROM share_links s
        JOIN files f ON s.file_id = f.id
        WHERE s.created_by = ? OR s.status != 'deleted'
        ORDER BY s.created_at DESC
    ''', (username,))
    shares = cursor.fetchall()
    conn.close()

    return jsonify([serialize_share_for_list(share, username) for share in shares])


def transition_share_status(share_id, username, from_statuses, to_status):
    """原子化状态迁移：仅当记录归属当前用户且处于允许的原状态时才改动。

    返回 (True, None) 表示成功；否则 (False, reason)，
    reason 为 'not_found' / 'forbidden' / 'conflict'。
    越权操作在此中止（forbidden），不会产生任何改动；
    并发下状态已被他人变更时返回 conflict。
    """
    conn = get_db()
    cursor = conn.cursor()
    placeholders = ', '.join('?' for _ in from_statuses)
    cursor.execute(
        f'UPDATE share_links SET status = ? WHERE id = ? AND created_by = ? AND status IN ({placeholders})',
        (to_status, share_id, username, *from_statuses)
    )

    if cursor.rowcount > 0:
        conn.commit()
        conn.close()
        return True, None

    # 未命中：区分 不存在 / 越权 / 状态冲突（如并发删除）
    cursor.execute('SELECT created_by, status FROM share_links WHERE id = ?', (share_id,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return False, 'not_found'
    if row['created_by'] != username:
        return False, 'forbidden'
    return False, 'conflict'


def share_status_transition_response(share_id, action, from_statuses, to_status):
    """执行状态迁移并把结果映射为 HTTP 响应（越权中止路径统一在此）"""
    token = get_token_from_request()
    username = get_username_from_token(token)

    ok, reason = transition_share_status(share_id, username, from_statuses, to_status)
    if ok:
        logger.info(f"分享{action}: 分享ID {share_id}, 操作者 {username}")
        return None

    if reason == 'not_found':
        return jsonify({'error': '分享链接不存在', 'reason': 'not_found'}), 404
    if reason == 'forbidden':
        # 越权操作：明确中止，当前用户只能改动自己的分享记录
        return jsonify({'error': f'无权限{action}此分享链接，只能管理自己的分享', 'reason': 'forbidden'}), 403
    return jsonify({'error': f'分享状态已变化，无法{action}（可能已被并发修改）', 'reason': 'conflict'}), 409


@files_bp.route('/api/share/<share_id>/disable', methods=['POST'])
@login_required
def disable_share(share_id):
    """停用分享链接（仅本人，active -> disabled）"""
    error = share_status_transition_response(share_id, '停用', ('active',), 'disabled')
    if error:
        return error
    return jsonify({'success': True, 'status': 'disabled', 'message': '分享链接已停用'})


@files_bp.route('/api/share/<share_id>/restore', methods=['POST'])
@login_required
def restore_share(share_id):
    """恢复分享链接（仅本人，disabled -> active）"""
    error = share_status_transition_response(share_id, '恢复', ('disabled',), 'active')
    if error:
        return error
    return jsonify({'success': True, 'status': 'active', 'message': '分享链接已恢复'})


@files_bp.route('/api/share/<share_id>', methods=['DELETE'])
@login_required
def delete_share(share_id):
    """删除分享链接（仅本人，软删除为已删除状态，保留历史记录）"""
    error = share_status_transition_response(share_id, '删除', ('active', 'disabled'), 'deleted')
    if error:
        return error
    return jsonify({'success': True, 'status': 'deleted', 'message': '分享链接已删除'})


@files_bp.route('/api/shares/cleanup', methods=['POST'])
@login_required
def cleanup_shares():
    """清理历史：彻底移除当前用户已删除的分享记录（仅本人）"""
    token = get_token_from_request()
    username = get_username_from_token(token)

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM share_links WHERE created_by = ? AND status = 'deleted'",
        (username,)
    )
    cleaned = cursor.rowcount
    conn.commit()
    conn.close()

    logger.info(f"分享历史清理: 操作者 {username}, 清理 {cleaned} 条")
    return jsonify({'success': True, 'cleaned': cleaned, 'message': f'已清理 {cleaned} 条历史记录'})
