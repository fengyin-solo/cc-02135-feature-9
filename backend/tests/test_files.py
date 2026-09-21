"""文件模块测试"""
import io
import time

import pytest

from app import app


@pytest.fixture
def other_token():
    """获取另一个用户（user）的 token"""
    from auth import rate_limit_store
    rate_limit_store.clear()

    app.config['TESTING'] = True
    with app.test_client() as test_client:
        response = test_client.post('/api/auth', json={
            'username': 'user',
            'password': 'user123'
        })
        return response.get_json()['token']


def create_share(client, token, filename='shared.txt', content=b'share content', **kwargs):
    """上传文件并创建分享，返回 share_id"""
    data = {'file': (io.BytesIO(content), filename)}
    upload_resp = client.post('/api/upload', data=data, content_type='multipart/form-data')
    file_id = upload_resp.get_json()['file_id']

    payload = {'file_id': file_id}
    payload.update(kwargs)
    create_resp = client.post(
        '/api/share',
        json=payload,
        headers={'Authorization': f'Bearer {token}'}
    )
    return create_resp.get_json()['share_id']


def test_upload_file(client):
    """测试文件上传"""
    data = {
        'file': (io.BytesIO(b'test content'), 'test.txt')
    }
    response = client.post('/api/upload', data=data, content_type='multipart/form-data')
    assert response.status_code == 200
    result = response.get_json()
    assert result['success'] is True
    assert 'file_id' in result


def test_upload_no_file(client):
    """测试无文件上传"""
    response = client.post('/api/upload', data={}, content_type='multipart/form-data')
    assert response.status_code == 400


def test_upload_invalid_extension(client):
    """测试不允许的文件类型"""
    data = {
        'file': (io.BytesIO(b'test'), 'test.exe')
    }
    response = client.post('/api/upload', data=data, content_type='multipart/form-data')
    assert response.status_code == 400


def test_list_files(client):
    """测试文件列表"""
    response = client.get('/api/files')
    assert response.status_code == 200
    assert isinstance(response.get_json(), list)


def test_download_without_token(client):
    """测试无 token 下载"""
    response = client.get('/api/download/some-id')
    assert response.status_code == 401


def test_download_file_not_found(client, auth_token):
    """测试下载不存在的文件"""
    response = client.get(f'/api/download/nonexistent-id?token={auth_token}')
    assert response.status_code == 404


def test_upload_and_download(client, auth_token):
    """测试上传后下载"""
    # 上传
    data = {
        'file': (io.BytesIO(b'hello world'), 'hello.txt')
    }
    upload_resp = client.post('/api/upload', data=data, content_type='multipart/form-data')
    file_id = upload_resp.get_json()['file_id']

    # 下载
    download_resp = client.get(f'/api/download/{file_id}?token={auth_token}')
    assert download_resp.status_code == 200
    assert download_resp.data == b'hello world'


def test_create_share_without_auth(client):
    """测试未授权创建分享链接"""
    response = client.post('/api/share', json={'file_id': 'test'})
    assert response.status_code == 401


def test_create_share_invalid_file(client, auth_token):
    """测试为不存在的文件创建分享链接"""
    response = client.post(
        '/api/share',
        json={'file_id': 'nonexistent'},
        headers={'Authorization': f'Bearer {auth_token}'}
    )
    assert response.status_code == 404


def test_create_share_success(client, auth_token):
    """测试创建分享链接成功"""
    data = {'file': (io.BytesIO(b'test content'), 'test_share.txt')}
    upload_resp = client.post('/api/upload', data=data, content_type='multipart/form-data')
    file_id = upload_resp.get_json()['file_id']

    response = client.post(
        '/api/share',
        json={'file_id': file_id, 'expire_hours': 24, 'max_downloads': 5},
        headers={'Authorization': f'Bearer {auth_token}'}
    )
    assert response.status_code == 200
    result = response.get_json()
    assert result['success'] is True
    assert 'share_id' in result
    assert result['max_downloads'] == 5
    assert result['filename'] == 'test_share.txt'


def test_create_share_default_values(client, auth_token):
    """测试使用默认值创建分享链接"""
    data = {'file': (io.BytesIO(b'test content'), 'test_default.txt')}
    upload_resp = client.post('/api/upload', data=data, content_type='multipart/form-data')
    file_id = upload_resp.get_json()['file_id']

    response = client.post(
        '/api/share',
        json={'file_id': file_id},
        headers={'Authorization': f'Bearer {auth_token}'}
    )
    assert response.status_code == 200
    result = response.get_json()
    assert result['success'] is True
    assert result['max_downloads'] == 10


def test_get_share_info(client, auth_token):
    """测试获取分享链接信息"""
    data = {'file': (io.BytesIO(b'test content'), 'test_get.txt')}
    upload_resp = client.post('/api/upload', data=data, content_type='multipart/form-data')
    file_id = upload_resp.get_json()['file_id']

    create_resp = client.post(
        '/api/share',
        json={'file_id': file_id, 'expire_hours': 24, 'max_downloads': 5},
        headers={'Authorization': f'Bearer {auth_token}'}
    )
    share_id = create_resp.get_json()['share_id']

    response = client.get(f'/api/share/{share_id}')
    assert response.status_code == 200
    result = response.get_json()
    assert result['share_id'] == share_id
    assert result['filename'] == 'test_get.txt'
    assert result['is_valid'] is True
    assert result['download_count'] == 0


def test_get_nonexistent_share(client):
    """测试获取不存在的分享链接"""
    response = client.get('/api/share/nonexistent')
    assert response.status_code == 404


def test_download_by_share_success(client, auth_token):
    """测试通过分享链接下载文件成功"""
    data = {'file': (io.BytesIO(b'share download test'), 'test_share_dl.txt')}
    upload_resp = client.post('/api/upload', data=data, content_type='multipart/form-data')
    file_id = upload_resp.get_json()['file_id']

    create_resp = client.post(
        '/api/share',
        json={'file_id': file_id, 'expire_hours': 24, 'max_downloads': 5},
        headers={'Authorization': f'Bearer {auth_token}'}
    )
    share_id = create_resp.get_json()['share_id']

    download_resp = client.get(f'/api/share/{share_id}/download')
    assert download_resp.status_code == 200
    assert download_resp.data == b'share download test'

    info_resp = client.get(f'/api/share/{share_id}')
    assert info_resp.get_json()['download_count'] == 1


def test_download_by_share_exceed_max(client, auth_token):
    """测试超过下载次数限制"""
    data = {'file': (io.BytesIO(b'limited content'), 'test_limited.txt')}
    upload_resp = client.post('/api/upload', data=data, content_type='multipart/form-data')
    file_id = upload_resp.get_json()['file_id']

    create_resp = client.post(
        '/api/share',
        json={'file_id': file_id, 'expire_hours': 24, 'max_downloads': 1},
        headers={'Authorization': f'Bearer {auth_token}'}
    )
    share_id = create_resp.get_json()['share_id']

    download_resp1 = client.get(f'/api/share/{share_id}/download')
    assert download_resp1.status_code == 200

    download_resp2 = client.get(f'/api/share/{share_id}/download')
    assert download_resp2.status_code == 404
    assert '下载次数已用完' in download_resp2.get_json()['error']


def test_download_expired_share(client, auth_token, db_conn):
    """测试下载已过期的分享链接"""
    data = {'file': (io.BytesIO(b'expired content'), 'test_expired.txt')}
    upload_resp = client.post('/api/upload', data=data, content_type='multipart/form-data')
    file_id = upload_resp.get_json()['file_id']

    create_resp = client.post(
        '/api/share',
        json={'file_id': file_id, 'expire_hours': 1, 'max_downloads': 5},
        headers={'Authorization': f'Bearer {auth_token}'}
    )
    share_id = create_resp.get_json()['share_id']

    cursor = db_conn.cursor()
    cursor.execute(
        'UPDATE share_links SET expires_at = ? WHERE id = ?',
        (time.time() - 3600, share_id)
    )
    db_conn.commit()

    download_resp = client.get(f'/api/share/{share_id}/download')
    assert download_resp.status_code == 404
    assert '已过期' in download_resp.get_json()['error']


def test_create_share_unlimited(client, auth_token):
    """测试创建无限制的分享链接"""
    data = {'file': (io.BytesIO(b'unlimited content'), 'test_unlimited.txt')}
    upload_resp = client.post('/api/upload', data=data, content_type='multipart/form-data')
    file_id = upload_resp.get_json()['file_id']

    create_resp = client.post(
        '/api/share',
        json={'file_id': file_id, 'expire_hours': -1, 'max_downloads': -1},
        headers={'Authorization': f'Bearer {auth_token}'}
    )
    result = create_resp.get_json()
    assert result['expires_at'] is None
    assert result['max_downloads'] is None

    for i in range(3):
        download_resp = client.get(f'/api/share/{result["share_id"]}/download')
        assert download_resp.status_code == 200

    info_resp = client.get(f'/api/share/{result["share_id"]}')
    assert info_resp.get_json()['download_count'] == 3
    assert info_resp.get_json()['is_valid'] is True


def test_list_shares(client, auth_token):
    """测试获取用户的分享列表"""
    for i in range(2):
        data = {'file': (io.BytesIO(f'content {i}'.encode()), f'test_list_{i}.txt')}
        upload_resp = client.post('/api/upload', data=data, content_type='multipart/form-data')
        file_id = upload_resp.get_json()['file_id']

        client.post(
            '/api/share',
            json={'file_id': file_id},
            headers={'Authorization': f'Bearer {auth_token}'}
        )

    response = client.get(
        '/api/shares',
        headers={'Authorization': f'Bearer {auth_token}'}
    )
    assert response.status_code == 200
    shares = response.get_json()
    assert len(shares) >= 2
    assert 'filename' in shares[0]
    assert 'is_valid' in shares[0]


def test_list_shares_without_auth(client):
    """测试未授权获取分享列表"""
    response = client.get('/api/shares')
    assert response.status_code == 401


def test_delete_share_success(client, auth_token):
    """测试删除分享链接成功"""
    data = {'file': (io.BytesIO(b'to delete'), 'test_delete.txt')}
    upload_resp = client.post('/api/upload', data=data, content_type='multipart/form-data')
    file_id = upload_resp.get_json()['file_id']

    create_resp = client.post(
        '/api/share',
        json={'file_id': file_id},
        headers={'Authorization': f'Bearer {auth_token}'}
    )
    share_id = create_resp.get_json()['share_id']

    delete_resp = client.delete(
        f'/api/share/{share_id}',
        headers={'Authorization': f'Bearer {auth_token}'}
    )
    assert delete_resp.status_code == 200
    assert delete_resp.get_json()['success'] is True

    get_resp = client.get(f'/api/share/{share_id}')
    assert get_resp.status_code == 404


def test_delete_share_unauthorized(client, auth_token, db_conn):
    """测试删除他人的分享链接"""
    data = {'file': (io.BytesIO(b'other content'), 'test_other.txt')}
    upload_resp = client.post('/api/upload', data=data, content_type='multipart/form-data')
    file_id = upload_resp.get_json()['file_id']

    cursor = db_conn.cursor()
    cursor.execute(
        'INSERT INTO share_links (id, file_id, created_by, expires_at, max_downloads) VALUES (?, ?, ?, ?, ?)',
        ('testshare123', file_id, 'otheruser', None, 10)
    )
    db_conn.commit()

    delete_resp = client.delete(
        '/api/share/testshare123',
        headers={'Authorization': f'Bearer {auth_token}'}
    )
    assert delete_resp.status_code == 403


def test_download_by_share_no_auth_needed(client, auth_token):
    """测试访客无需登录即可通过分享链接下载"""
    data = {'file': (io.BytesIO(b'public content'), 'test_public.txt')}
    upload_resp = client.post('/api/upload', data=data, content_type='multipart/form-data')
    file_id = upload_resp.get_json()['file_id']

    create_resp = client.post(
        '/api/share',
        json={'file_id': file_id, 'expire_hours': 24, 'max_downloads': 5},
        headers={'Authorization': f'Bearer {auth_token}'}
    )
    share_id = create_resp.get_json()['share_id']

    download_resp = client.get(f'/api/share/{share_id}/download')
    assert download_resp.status_code == 200
    assert download_resp.data == b'public content'


# ---------- 分享管理：停用 / 恢复 / 删除 / 清理 ----------

def test_disable_and_restore_share(client, auth_token):
    """测试停用与恢复分享链接"""
    share_id = create_share(client, auth_token)

    # 停用
    disable_resp = client.post(
        f'/api/share/{share_id}/disable',
        headers={'Authorization': f'Bearer {auth_token}'}
    )
    assert disable_resp.status_code == 200
    assert disable_resp.get_json()['status'] == 'disabled'

    # 停用后公开信息标记为无效
    info_resp = client.get(f'/api/share/{share_id}')
    assert info_resp.get_json()['is_valid'] is False
    assert '已停用' in info_resp.get_json()['error_msg']

    # 停用后无法通过分享链接下载
    download_resp = client.get(f'/api/share/{share_id}/download')
    assert download_resp.status_code == 404
    assert '已停用' in download_resp.get_json()['error']

    # 恢复
    restore_resp = client.post(
        f'/api/share/{share_id}/restore',
        headers={'Authorization': f'Bearer {auth_token}'}
    )
    assert restore_resp.status_code == 200
    assert restore_resp.get_json()['status'] == 'active'

    # 恢复后可正常下载
    download_resp = client.get(f'/api/share/{share_id}/download')
    assert download_resp.status_code == 200


def test_disable_share_unauthorized(client, auth_token, other_token):
    """测试停用他人的分享链接被中止（越权路径）"""
    share_id = create_share(client, auth_token)

    response = client.post(
        f'/api/share/{share_id}/disable',
        headers={'Authorization': f'Bearer {other_token}'}
    )
    assert response.status_code == 403
    assert response.get_json()['code'] == 'FORBIDDEN'

    # 越权操作不影响原分享状态
    info_resp = client.get(f'/api/share/{share_id}')
    assert info_resp.get_json()['is_valid'] is True


def test_restore_share_unauthorized(client, auth_token, other_token):
    """测试恢复他人的分享链接被中止"""
    share_id = create_share(client, auth_token)
    client.post(
        f'/api/share/{share_id}/disable',
        headers={'Authorization': f'Bearer {auth_token}'}
    )

    response = client.post(
        f'/api/share/{share_id}/restore',
        headers={'Authorization': f'Bearer {other_token}'}
    )
    assert response.status_code == 403


def test_delete_share_soft_and_concurrent(client, auth_token):
    """测试软删除及并发重复删除的明确反馈"""
    share_id = create_share(client, auth_token)

    # 第一次删除成功
    delete_resp = client.delete(
        f'/api/share/{share_id}',
        headers={'Authorization': f'Bearer {auth_token}'}
    )
    assert delete_resp.status_code == 200

    # 公开视角视为不存在
    get_resp = client.get(f'/api/share/{share_id}')
    assert get_resp.status_code == 404

    # 并发场景：重复删除返回明确结果
    delete_again = client.delete(
        f'/api/share/{share_id}',
        headers={'Authorization': f'Bearer {auth_token}'}
    )
    assert delete_again.status_code == 404
    assert delete_again.get_json()['code'] == 'SHARE_GONE'


def test_operate_deleted_share_aborted(client, auth_token):
    """测试对已删除分享的变更操作全部中止"""
    share_id = create_share(client, auth_token)
    client.delete(
        f'/api/share/{share_id}',
        headers={'Authorization': f'Bearer {auth_token}'}
    )

    for endpoint in ('disable', 'restore'):
        response = client.post(
            f'/api/share/{share_id}/{endpoint}',
            headers={'Authorization': f'Bearer {auth_token}'}
        )
        assert response.status_code == 404
        assert response.get_json()['code'] == 'SHARE_GONE'


def test_list_shares_scope_all_readonly(client, auth_token, other_token):
    """测试 scope=all 时他人分享标记为只读"""
    own_share = create_share(client, auth_token, filename='own.txt')
    other_share = create_share(client, other_token, filename='other.txt')

    response = client.get(
        '/api/shares?scope=all',
        headers={'Authorization': f'Bearer {auth_token}'}
    )
    assert response.status_code == 200
    shares = {s['share_id']: s for s in response.get_json()}

    assert shares[own_share]['can_manage'] is True
    assert shares[own_share]['is_own'] is True
    assert shares[other_share]['can_manage'] is False
    assert shares[other_share]['is_own'] is False


def test_list_shares_include_deleted(client, auth_token):
    """测试列表包含已删除分享并标记状态"""
    share_id = create_share(client, auth_token)
    client.delete(
        f'/api/share/{share_id}',
        headers={'Authorization': f'Bearer {auth_token}'}
    )

    # 默认不返回已删除
    default_resp = client.get(
        '/api/shares',
        headers={'Authorization': f'Bearer {auth_token}'}
    )
    assert share_id not in [s['share_id'] for s in default_resp.get_json()]

    # include_deleted=1 返回已删除记录
    resp = client.get(
        '/api/shares?include_deleted=1',
        headers={'Authorization': f'Bearer {auth_token}'}
    )
    shares = {s['share_id']: s for s in resp.get_json()}
    assert share_id in shares
    assert shares[share_id]['deleted_at'] is not None
    assert shares[share_id]['can_manage'] is False
    assert shares[share_id]['is_valid'] is False


def test_cleanup_shares(client, auth_token, db_conn):
    """测试清理历史：清除已删除与已失效的本人分享"""
    # 有效分享（保留）
    valid_share = create_share(client, auth_token, filename='valid.txt')
    # 已删除分享（应清理）
    deleted_share = create_share(client, auth_token, filename='deleted.txt')
    client.delete(
        f'/api/share/{deleted_share}',
        headers={'Authorization': f'Bearer {auth_token}'}
    )
    # 已过期分享（应清理）
    expired_share = create_share(client, auth_token, filename='expired.txt')
    cursor = db_conn.cursor()
    cursor.execute(
        'UPDATE share_links SET expires_at = ? WHERE id = ?',
        (time.time() - 3600, expired_share)
    )
    db_conn.commit()

    cleanup_resp = client.post(
        '/api/shares/cleanup',
        headers={'Authorization': f'Bearer {auth_token}'}
    )
    assert cleanup_resp.status_code == 200
    # 测试库为会话级共享，至少清理掉本次构造的 2 条失效记录
    assert cleanup_resp.get_json()['cleaned'] >= 2

    # 清理后仅剩有效分享
    resp = client.get(
        '/api/shares?include_deleted=1',
        headers={'Authorization': f'Bearer {auth_token}'}
    )
    remaining = [s['share_id'] for s in resp.get_json()]
    assert valid_share in remaining
    assert deleted_share not in remaining
    assert expired_share not in remaining


def test_cleanup_shares_only_own(client, auth_token, other_token, db_conn):
    """测试清理历史只影响本人的分享"""
    other_deleted = create_share(client, other_token, filename='other_deleted.txt')
    client.delete(
        f'/api/share/{other_deleted}',
        headers={'Authorization': f'Bearer {other_token}'}
    )

    cleanup_resp = client.post(
        '/api/shares/cleanup',
        headers={'Authorization': f'Bearer {auth_token}'}
    )
    assert cleanup_resp.status_code == 200

    # 他人的已删除分享不受影响
    cursor = db_conn.cursor()
    cursor.execute('SELECT id FROM share_links WHERE id = ?', (other_deleted,))
    assert cursor.fetchone() is not None


def test_files_include_share_summary(client, auth_token):
    """测试文件目录同步展示当前用户的分享状态"""
    data = {'file': (io.BytesIO(b'summary content'), 'summary.txt')}
    upload_resp = client.post('/api/upload', data=data, content_type='multipart/form-data')
    file_id = upload_resp.get_json()['file_id']

    share1 = client.post(
        '/api/share',
        json={'file_id': file_id},
        headers={'Authorization': f'Bearer {auth_token}'}
    ).get_json()['share_id']
    share2 = client.post(
        '/api/share',
        json={'file_id': file_id},
        headers={'Authorization': f'Bearer {auth_token}'}
    ).get_json()['share_id']

    # 停用其中一个
    client.post(
        f'/api/share/{share2}/disable',
        headers={'Authorization': f'Bearer {auth_token}'}
    )

    response = client.get(
        '/api/files',
        headers={'Authorization': f'Bearer {auth_token}'}
    )
    files = {f['id']: f for f in response.get_json()}
    assert files[file_id]['my_shares'] == {'active': 1, 'disabled': 1}

    # 删除后摘要及时同步
    client.delete(
        f'/api/share/{share1}',
        headers={'Authorization': f'Bearer {auth_token}'}
    )
    response = client.get(
        '/api/files',
        headers={'Authorization': f'Bearer {auth_token}'}
    )
    files = {f['id']: f for f in response.get_json()}
    assert files[file_id]['my_shares'] == {'active': 0, 'disabled': 1}

    # 未登录时不包含分享摘要
    response = client.get('/api/files')
    files = {f['id']: f for f in response.get_json()}
    assert files[file_id]['my_shares'] is None
