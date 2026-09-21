// 从配置文件获取API地址
const API_BASE = CONFIG.API_BASE;

let currentShareFileId = null;
let currentShareLink = null;

// Token 管理
const TokenManager = {
    TOKEN_KEY: 'auth_token',
    USER_KEY: 'auth_user',
    
    save(token, username) {
        localStorage.setItem(this.TOKEN_KEY, token);
        localStorage.setItem(this.USER_KEY, username);
    },
    
    get() {
        return localStorage.getItem(this.TOKEN_KEY);
    },
    
    getUser() {
        return localStorage.getItem(this.USER_KEY);
    },
    
    clear() {
        localStorage.removeItem(this.TOKEN_KEY);
        localStorage.removeItem(this.USER_KEY);
    },
    
    async isValid() {
        const token = this.get();
        if (!token) return false;
        
        try {
            const response = await fetch(`${API_BASE}/refresh-token?token=${token}`, {
                method: 'POST'
            });
            return response.ok;
        } catch {
            return false;
        }
    }
};

// 更新用户状态栏
async function updateUserBar() {
    const userBar = document.getElementById('userBar');
    const currentUser = document.getElementById('currentUser');
    const userAvatar = document.getElementById('userAvatar');
    const user = TokenManager.getUser();
    
    if (user && TokenManager.get() && await TokenManager.isValid()) {
        currentUser.textContent = user;
        userAvatar.textContent = user.charAt(0).toUpperCase();
        userBar.classList.remove('hidden');
        loadMyShares();
    } else {
        userBar.classList.add('hidden');
        const shareSection = document.getElementById('mySharesSection');
        if (shareSection) {
            shareSection.style.display = 'none';
        }
    }
}

// 退出登录
function logout() {
    TokenManager.clear();
    selectedShareIds.clear();
    lastSharesCache = [];
    updateUserBar();
}

// 页面加载时获取文件列表和更新用户状态
document.addEventListener('DOMContentLoaded', async () => {
    // 检查token是否有效，无效则清除
    if (TokenManager.get() && !(await TokenManager.isValid())) {
        TokenManager.clear();
    }
    await updateUserBar();
    loadFileList();
});

// 验证文件
function validateFile(file) {
    if (file.size > CONFIG.MAX_FILE_SIZE) {
        return `文件大小超过限制（最大${CONFIG.MAX_FILE_SIZE / 1024 / 1024}MB）`;
    }
    return null;
}

// 上传文件处理函数
async function uploadFile(file) {
    const validationError = validateFile(file);
    if (validationError) {
        document.getElementById('uploadStatus').textContent = `❌ ${validationError}`;
        return;
    }

    showLoading('上传中...');
    
    const formData = new FormData();
    formData.append('file', file);

    try {
        const response = await fetch(`${API_BASE}/upload`, {
            method: 'POST',
            body: formData
        });
        const result = await response.json();
        
        if (response.ok) {
            document.getElementById('uploadStatus').textContent = `✅ ${file.name} 上传成功！`;
            loadFileList();
        } else {
            document.getElementById('uploadStatus').textContent = `❌ 上传失败: ${result.error}`;
        }
    } catch (error) {
        document.getElementById('uploadStatus').textContent = `❌ 上传失败: ${error.message}`;
    } finally {
        hideLoading();
    }
}

// 文件选择上传
document.getElementById('fileInput').addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    await uploadFile(file);
    e.target.value = '';
});

// 拖拽上传
const uploadZone = document.querySelector('.upload-zone');

uploadZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    uploadZone.classList.add('drag-over');
});

uploadZone.addEventListener('dragleave', (e) => {
    e.preventDefault();
    uploadZone.classList.remove('drag-over');
});

uploadZone.addEventListener('drop', async (e) => {
    e.preventDefault();
    uploadZone.classList.remove('drag-over');
    
    const file = e.dataTransfer.files[0];
    if (file) {
        await uploadFile(file);
    }
});

// 加载文件列表
async function loadFileList() {
    showLoading('加载文件列表...');

    try {
        // 携带 token 时后端会附带每个文件本人的分享状态摘要
        const headers = {};
        if (TokenManager.get()) {
            headers['Authorization'] = `Bearer ${TokenManager.get()}`;
        }
        const response = await fetch(`${API_BASE}/files`, { headers });
        const files = await response.json();

        const fileList = document.getElementById('fileList');
        const isLoggedIn = TokenManager.get() && (await TokenManager.isValid());

        if (files.length === 0) {
            fileList.innerHTML = '<p class="empty-msg">暂无可下载文件</p>';
        } else {
            fileList.innerHTML = files.map(file => `
                <div class="file-item">
                    <div class="file-info">
                        <div class="file-icon">${getFileIcon(file.name)}</div>
                        <div class="file-details">
                            <div class="file-name">${escapeHtml(file.name)}</div>
                            <div class="file-size">${formatSize(file.size)}</div>
                            ${renderFileShareBadge(file.my_shares)}
                        </div>
                    </div>
                    <div class="file-actions">
                        ${isLoggedIn ? `<button class="share-btn" onclick="openShareModal('${escapeHtml(file.id)}', '${escapeHtml(file.name)}')">分享</button>` : ''}
                        <button class="download-btn" onclick="requestDownload('${escapeHtml(file.id)}')">
                            下载
                        </button>
                    </div>
                </div>
            `).join('');
        }
    } catch (error) {
        document.getElementById('fileList').innerHTML =
            `<p class="empty-msg">加载失败: ${escapeHtml(error.message)}</p>`;
    } finally {
        hideLoading();
    }
}

// 文件目录中的分享状态徽章（与我的分享管理状态同步）
function renderFileShareBadge(summary) {
    if (!summary) return '';
    const parts = [];
    if (summary.active > 0) parts.push(`🔗 分享中 ${summary.active}`);
    if (summary.disabled > 0) parts.push(`⏸️ 已停用 ${summary.disabled}`);
    if (parts.length === 0) return '';
    return `<div class="file-share-badge">${parts.join(' · ')}</div>`;
}

// HTML转义防止XSS
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// 请求下载 - 检查token是否有效，有效则直接下载
async function requestDownload(fileId) {
    showLoading('检查授权...');
    
    // 检查是否有有效的token
    if (await TokenManager.isValid()) {
        // token有效，使用 fetch + Authorization 头下载
        document.getElementById('loadingText').textContent = '正在下载...';
        try {
            const response = await fetch(`${API_BASE}/download/${fileId}`, {
                method: 'GET',
                headers: {
                    'Authorization': `Bearer ${TokenManager.get()}`
                }
            });
            if (response.ok) {
                const blob = await response.blob();
                const contentDisposition = response.headers.get('Content-Disposition');
                let filename = 'download';
                if (contentDisposition) {
                    const match = contentDisposition.match(/filename\*?=(?:UTF-8'')?["']?([^"';\n]+)/i);
                    if (match) filename = decodeURIComponent(match[1]);
                }
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = filename;
                document.body.appendChild(a);
                a.click();
                window.URL.revokeObjectURL(url);
                a.remove();
            } else {
                const result = await response.json();
                alert(`下载失败: ${result.error || '未知错误'}`);
            }
        } catch (error) {
            alert(`下载失败: ${error.message}`);
        } finally {
            hideLoading();
        }
        return;
    }
    
    // token无效或不存在，弹出登录框
    hideLoading();
    TokenManager.clear();
    document.getElementById('downloadFileId').value = fileId;
    document.getElementById('authModal').classList.add('active');
    document.getElementById('authError').textContent = '';
    document.getElementById('username').value = '';
    document.getElementById('password').value = '';
    document.getElementById('username').focus();
}

// 关闭验证弹窗
function closeAuthModal() {
    document.getElementById('authModal').classList.remove('active');
}

// 身份验证表单提交
document.getElementById('authForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    
    const username = document.getElementById('username').value.trim();
    const password = document.getElementById('password').value;
    const fileId = document.getElementById('downloadFileId').value;

    if (!username || !password) {
        document.getElementById('authError').textContent = '请输入用户名和密码';
        return;
    }

    showLoading('验证身份...');
    
    try {
        const response = await fetch(`${API_BASE}/auth`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password })
        });
        
        const result = await response.json();
        
        if (response.ok && result.success) {
            // 保存token和用户名到本地
            TokenManager.save(result.token, username);
            await updateUserBar();
            loadFileList();
            
            closeAuthModal();
            document.getElementById('loadingText').textContent = '验证成功，正在下载...';
            
            // 使用 fetch + Authorization 头下载
            try {
                const downloadResponse = await fetch(`${API_BASE}/download/${fileId}`, {
                    method: 'GET',
                    headers: {
                        'Authorization': `Bearer ${result.token}`
                    }
                });
                if (downloadResponse.ok) {
                    const blob = await downloadResponse.blob();
                    const contentDisposition = downloadResponse.headers.get('Content-Disposition');
                    let filename = 'download';
                    if (contentDisposition) {
                        const match = contentDisposition.match(/filename\*?=(?:UTF-8'')?["']?([^"';\n]+)/i);
                        if (match) filename = decodeURIComponent(match[1]);
                    }
                    const url = window.URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = filename;
                    document.body.appendChild(a);
                    a.click();
                    window.URL.revokeObjectURL(url);
                    a.remove();
                } else {
                    const errResult = await downloadResponse.json();
                    document.getElementById('authError').textContent = `下载失败: ${errResult.error || '未知错误'}`;
                }
            } catch (downloadError) {
                document.getElementById('authError').textContent = `下载失败: ${downloadError.message}`;
            } finally {
                hideLoading();
            }
        } else if (response.status === 429) {
            hideLoading();
            document.getElementById('authError').textContent = '请求过于频繁，请稍后再试';
        } else {
            hideLoading();
            document.getElementById('authError').textContent = result.error || '验证失败，请检查账号密码';
        }
    } catch (error) {
        hideLoading();
        document.getElementById('authError').textContent = `验证失败: ${error.message}`;
    }
});

// 显示加载动画
function showLoading(text = '加载中...') {
    document.getElementById('loadingText').textContent = text;
    document.getElementById('loadingOverlay').classList.add('active');
}

// 隐藏加载动画
function hideLoading() {
    document.getElementById('loadingOverlay').classList.remove('active');
}

// 获取文件图标
function getFileIcon(filename) {
    const ext = filename.split('.').pop().toLowerCase();
    const icons = {
        pdf: '📄', doc: '📝', docx: '📝', txt: '📃',
        jpg: '🖼️', jpeg: '🖼️', png: '🖼️', gif: '🖼️',
        mp3: '🎵', wav: '🎵', mp4: '🎬', avi: '🎬',
        zip: '📦', rar: '📦', '7z': '📦',
        js: '💻', py: '🐍', html: '🌐', css: '🎨'
    };
    return icons[ext] || '📁';
}

// 格式化文件大小
function formatSize(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

// 格式化时间戳
function formatTimestamp(timestamp) {
    if (!timestamp) return '永久有效';
    const date = new Date(timestamp * 1000);
    return date.toLocaleString('zh-CN', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit'
    });
}

// 格式化剩余时间
function formatRemainingTime(expiresAt) {
    if (!expiresAt) return '永久';
    const remaining = expiresAt - (Date.now() / 1000);
    if (remaining <= 0) return '已过期';
    
    const hours = Math.floor(remaining / 3600);
    const minutes = Math.floor((remaining % 3600) / 60);
    
    if (hours > 24) {
        const days = Math.floor(hours / 24);
        return `${days} 天 ${hours % 24} 小时`;
    } else if (hours > 0) {
        return `${hours} 小时 ${minutes} 分钟`;
    } else {
        return `${minutes} 分钟`;
    }
}

// 打开分享设置弹窗
function openShareModal(fileId, fileName) {
    currentShareFileId = fileId;
    document.getElementById('shareFileName').textContent = fileName;
    document.getElementById('shareError').textContent = '';
    document.getElementById('expireHours').value = '24';
    document.getElementById('maxDownloads').value = '10';
    document.getElementById('shareModal').classList.add('active');
}

// 关闭分享设置弹窗
function closeShareModal() {
    document.getElementById('shareModal').classList.remove('active');
    currentShareFileId = null;
}

// 确认创建分享链接
async function confirmCreateShare() {
    if (!currentShareFileId) return;
    
    const expireHours = parseInt(document.getElementById('expireHours').value);
    const maxDownloads = parseInt(document.getElementById('maxDownloads').value);
    
    showLoading('生成分享链接...');
    document.getElementById('shareError').textContent = '';
    
    try {
        const response = await fetch(`${API_BASE}/share`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${TokenManager.get()}`
            },
            body: JSON.stringify({
                file_id: currentShareFileId,
                expire_hours: expireHours,
                max_downloads: maxDownloads
            })
        });
        
        const result = await response.json();
        
        if (response.ok && result.success) {
            closeShareModal();
            showShareSuccessModal(result);
            loadMyShares();
        } else {
            document.getElementById('shareError').textContent = result.error || '生成分享链接失败';
        }
    } catch (error) {
        document.getElementById('shareError').textContent = `错误: ${error.message}`;
    } finally {
        hideLoading();
    }
}

// 显示分享成功弹窗
function showShareSuccessModal(result) {
    currentShareLink = `${window.location.origin}/share.html#${result.share_id}`;
    
    document.getElementById('shareLinkInput').value = currentShareLink;
    document.getElementById('shareInfoName').textContent = result.filename;
    document.getElementById('shareInfoExpire').textContent = formatTimestamp(result.expires_at);
    document.getElementById('shareInfoDownloads').textContent = result.max_downloads ? `${result.max_downloads} 次` : '无限制';
    document.getElementById('copyBtnText').textContent = '复制';
    
    const copyBtn = document.querySelector('.copy-btn');
    copyBtn.classList.remove('copied');
    
    document.getElementById('shareSuccessModal').classList.add('active');
}

// 关闭分享成功弹窗
function closeShareSuccessModal() {
    document.getElementById('shareSuccessModal').classList.remove('active');
    currentShareLink = null;
}

// 复制分享链接
async function copyShareLink() {
    const linkInput = document.getElementById('shareLinkInput');
    const copyBtnText = document.getElementById('copyBtnText');
    const copyBtn = document.querySelector('.copy-btn');
    
    try {
        await navigator.clipboard.writeText(linkInput.value);
        copyBtnText.textContent = '已复制';
        copyBtn.classList.add('copied');
        
        setTimeout(() => {
            copyBtnText.textContent = '复制';
            copyBtn.classList.remove('copied');
        }, 2000);
    } catch (error) {
        linkInput.select();
        document.execCommand('copy');
        copyBtnText.textContent = '已复制';
        copyBtn.classList.add('copied');
        
        setTimeout(() => {
            copyBtnText.textContent = '复制';
            copyBtn.classList.remove('copied');
        }, 2000);
    }
}

// ==================== 我的分享管理 ====================

// 勾选选择集：跨列表刷新保留原选择
const selectedShareIds = new Set();
// 状态筛选标签：跨会话保留（返回列表时恢复原选择）
let shareFilter = localStorage.getItem('share_filter') || 'all';
// 最近一次加载的分享数据缓存，用于筛选切换时本地重渲染
let lastSharesCache = [];

// 加载我的分享列表（含他人只读记录与本人已删除记录）
async function loadMyShares() {
    const section = document.getElementById('mySharesSection');
    const list = document.getElementById('mySharesList');

    if (!(await TokenManager.isValid())) {
        section.style.display = 'none';
        return;
    }

    section.style.display = 'block';
    applyShareFilterTabs();

    try {
        const response = await fetch(`${API_BASE}/shares?scope=all&include_deleted=1`, {
            headers: {
                'Authorization': `Bearer ${TokenManager.get()}`
            }
        });

        if (!response.ok) {
            const result = await response.json().catch(() => ({}));
            list.innerHTML = `<p class="empty-msg">加载失败: ${escapeHtml(result.error || response.statusText)}</p>`;
            return;
        }

        const shares = await response.json();
        lastSharesCache = shares;

        // 仅移除选择集中已不可管理的记录，保留其余原选择
        const manageableIds = new Set(shares.filter(s => s.can_manage).map(s => s.share_id));
        for (const id of [...selectedShareIds]) {
            if (!manageableIds.has(id)) {
                selectedShareIds.delete(id);
            }
        }

        renderShareList(shares);
    } catch (error) {
        list.innerHTML = `<p class="empty-msg">加载失败: ${escapeHtml(error.message)}</p>`;
    }
}

// 渲染分享列表（应用当前筛选，并恢复勾选状态）
function renderShareList(shares) {
    const list = document.getElementById('mySharesList');
    const filtered = shares.filter(matchShareFilter);

    if (filtered.length === 0) {
        list.innerHTML = shares.length === 0
            ? '<p class="empty-msg">暂无分享链接</p>'
            : '<p class="empty-msg">当前筛选条件下暂无分享记录</p>';
        updateBulkBar();
        return;
    }

    list.innerHTML = filtered.map(share => renderShareItem(share)).join('');

    // 恢复勾选状态，保留原选择
    list.querySelectorAll('.share-select').forEach(checkbox => {
        checkbox.checked = selectedShareIds.has(checkbox.dataset.shareId);
    });
    updateBulkBar();
}

// 筛选条件匹配
function matchShareFilter(share) {
    const isDeleted = share.deleted_at !== null;
    switch (shareFilter) {
        case 'valid': return !isDeleted && share.is_valid;
        case 'disabled': return !isDeleted && share.status === 'disabled';
        case 'deleted': return isDeleted;
        default: return true;
    }
}

// 渲染单条分享记录：区分本人可管理、他人只读、已删除三种状态
function renderShareItem(share) {
    const isDeleted = share.deleted_at !== null;

    let statusClass, statusText;
    if (isDeleted) {
        statusClass = 'deleted';
        statusText = '已删除';
    } else if (share.status === 'disabled') {
        statusClass = 'disabled';
        statusText = '已停用';
    } else if (share.is_valid) {
        statusClass = 'valid';
        statusText = '有效';
    } else {
        statusClass = 'invalid';
        statusText = share.error_msg || '无效';
    }

    // 归属标识
    let ownerBadge = '';
    if (!isDeleted) {
        ownerBadge = share.can_manage
            ? '<span class="share-owner-badge own">本人创建</span>'
            : `<span class="share-owner-badge readonly">👤 ${escapeHtml(share.created_by)} · 只读</span>`;
    }

    // 仅本人可管理的记录可勾选
    const checkbox = share.can_manage
        ? `<input type="checkbox" class="share-select" data-share-id="${share.share_id}"
                   onchange="toggleShareSelection('${share.share_id}', this.checked)">`
        : '';

    // 操作区：本人可管理才有操作；他人只读与已删除仅展示说明
    let actions;
    if (share.can_manage) {
        const toggleBtn = share.status === 'disabled'
            ? `<button class="restore-share-btn" onclick="toggleShareStatus('${share.share_id}', 'restore')">♻️ 恢复</button>`
            : `<button class="disable-share-btn" onclick="toggleShareStatus('${share.share_id}', 'disable')">⏸️ 停用</button>`;
        actions = `
            <button class="copy-link-btn" onclick="copyShareLinkFromList('${share.share_id}')">
                🔗 复制链接
            </button>
            ${toggleBtn}
            <button class="delete-share-btn" onclick="deleteShare('${share.share_id}')">
                🗑️ 删除
            </button>`;
    } else if (isDeleted) {
        actions = '<span class="readonly-hint">记录已删除，可通过「清理历史」彻底移除</span>';
    } else {
        actions = '<span class="readonly-hint">他人创建的分享，仅可查看</span>';
    }

    return `
        <div class="share-item ${isDeleted ? 'share-item-deleted' : ''}">
            <div class="share-item-header">
                <span class="share-item-title">
                    ${checkbox}
                    <span class="share-item-filename">${escapeHtml(share.filename)}</span>
                    ${ownerBadge}
                </span>
                <span class="share-item-status ${statusClass}">${statusText}</span>
            </div>
            <div class="share-item-details">
                <div class="share-item-detail">
                    <span class="share-item-detail-label">剩余时间</span>
                    <span class="share-item-detail-value">${formatRemainingTime(share.expires_at)}</span>
                </div>
                <div class="share-item-detail">
                    <span class="share-item-detail-label">已下载</span>
                    <span class="share-item-detail-value">${share.download_count} / ${share.max_downloads || '∞'}</span>
                </div>
                <div class="share-item-detail">
                    <span class="share-item-detail-label">创建时间</span>
                    <span class="share-item-detail-value">${new Date(share.created_at).toLocaleString('zh-CN')}</span>
                </div>
            </div>
            <div class="share-item-actions">
                ${actions}
            </div>
        </div>
    `;
}

// 越权/并发场景的明确错误描述
function describeShareError(status, result) {
    if (status === 403) {
        return '无权限操作：该分享不属于当前账号，操作已中止';
    }
    if (status === 404) {
        return '该分享不存在或已被删除（可能已被其他会话操作）';
    }
    return result.error || '未知错误';
}

// 在分享管理区展示操作结果反馈
function showShareFeedback(message, type) {
    const feedback = document.getElementById('shareManageFeedback');
    feedback.textContent = message;
    feedback.className = `share-feedback ${type}`;
    feedback.style.display = 'block';
}

// 切换勾选状态
function toggleShareSelection(shareId, checked) {
    if (checked) {
        selectedShareIds.add(shareId);
    } else {
        selectedShareIds.delete(shareId);
    }
    updateBulkBar();
}

// 更新批量操作栏
function updateBulkBar() {
    const btn = document.getElementById('deleteSelectedBtn');
    if (!btn) return;
    btn.disabled = selectedShareIds.size === 0;
    btn.textContent = selectedShareIds.size > 0
        ? `删除所选 (${selectedShareIds.size})`
        : '删除所选';
}

// 切换筛选标签（选择持久化，返回列表时保留）
function setShareFilter(filter) {
    shareFilter = filter;
    localStorage.setItem('share_filter', filter);
    applyShareFilterTabs();
    renderShareList(lastSharesCache);
}

// 同步筛选标签的激活样式
function applyShareFilterTabs() {
    document.querySelectorAll('.share-filter-tab').forEach(tab => {
        tab.classList.toggle('active', tab.dataset.filter === shareFilter);
    });
}

// 从分享列表复制链接
async function copyShareLinkFromList(shareId) {
    const link = `${window.location.origin}/share.html#${shareId}`;
    try {
        await navigator.clipboard.writeText(link);
        showShareFeedback('✅ 分享链接已复制到剪贴板', 'success');
    } catch (error) {
        prompt('请手动复制链接:', link);
    }
}

// 停用 / 恢复分享链接
async function toggleShareStatus(shareId, action) {
    const actionText = action === 'disable' ? '停用' : '恢复';
    showLoading(`${actionText}中...`);

    try {
        const response = await fetch(`${API_BASE}/share/${shareId}/${action}`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${TokenManager.get()}`
            }
        });
        const result = await response.json().catch(() => ({}));

        if (response.ok) {
            showShareFeedback(`✅ ${result.message || `分享链接已${actionText}`}`, 'success');
        } else {
            showShareFeedback(`❌ ${actionText}失败: ${describeShareError(response.status, result)}`, 'error');
        }
    } catch (error) {
        showShareFeedback(`❌ ${actionText}失败: ${error.message}`, 'error');
    } finally {
        hideLoading();
        // 刷新分享列表（保留原选择）并同步文件目录状态
        await loadMyShares();
        loadFileList();
    }
}

// 删除分享链接
async function deleteShare(shareId) {
    if (!confirm('确定要删除此分享链接吗？删除后链接将立即失效。')) {
        return;
    }

    showLoading('删除中...');

    try {
        const response = await fetch(`${API_BASE}/share/${shareId}`, {
            method: 'DELETE',
            headers: {
                'Authorization': `Bearer ${TokenManager.get()}`
            }
        });
        const result = await response.json().catch(() => ({}));

        if (response.ok) {
            selectedShareIds.delete(shareId);
            showShareFeedback('✅ 分享链接已删除，可通过「清理历史」彻底移除记录', 'success');
        } else {
            showShareFeedback(`❌ 删除失败: ${describeShareError(response.status, result)}`, 'error');
        }
    } catch (error) {
        showShareFeedback(`❌ 删除失败: ${error.message}`, 'error');
    } finally {
        hideLoading();
        await loadMyShares();
        loadFileList();
    }
}

// 批量删除所选分享（逐项执行，汇总明确结果）
async function deleteSelectedShares() {
    const ids = [...selectedShareIds];
    if (ids.length === 0) return;

    if (!confirm(`确定要删除选中的 ${ids.length} 个分享链接吗？`)) {
        return;
    }

    showLoading('批量删除中...');
    let succeeded = 0;
    const failures = [];

    for (const shareId of ids) {
        try {
            const response = await fetch(`${API_BASE}/share/${shareId}`, {
                method: 'DELETE',
                headers: {
                    'Authorization': `Bearer ${TokenManager.get()}`
                }
            });
            if (response.ok) {
                succeeded++;
                selectedShareIds.delete(shareId);
            } else {
                const result = await response.json().catch(() => ({}));
                failures.push(describeShareError(response.status, result));
            }
        } catch (error) {
            failures.push(error.message);
        }
    }

    hideLoading();

    if (failures.length === 0) {
        showShareFeedback(`✅ 已删除 ${succeeded} 个分享链接`, 'success');
    } else {
        showShareFeedback(
            `⚠️ 已删除 ${succeeded} 个，${failures.length} 个失败：${failures[0]}`,
            'error'
        );
    }

    await loadMyShares();
    loadFileList();
}

// 清理历史分享（已删除 / 已过期 / 次数用完的本人记录）
async function cleanupShares() {
    if (!confirm('清理历史将彻底移除已删除、已过期或下载次数用完的分享记录，确定继续吗？')) {
        return;
    }

    showLoading('清理历史分享...');

    try {
        const response = await fetch(`${API_BASE}/shares/cleanup`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${TokenManager.get()}`
            }
        });
        const result = await response.json().catch(() => ({}));

        if (response.ok) {
            showShareFeedback(`✅ ${result.message}`, 'success');
        } else {
            showShareFeedback(`❌ 清理失败: ${describeShareError(response.status, result)}`, 'error');
        }
    } catch (error) {
        showShareFeedback(`❌ 清理失败: ${error.message}`, 'error');
    } finally {
        hideLoading();
        await loadMyShares();
        loadFileList();
    }
}


