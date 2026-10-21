import os, json, time, re, logging, shutil, glob
from logging.handlers import TimedRotatingFileHandler
from flask import Flask, request, jsonify, render_template_string
import requests

# ==========================================
# 9KGwash 终极洗板网关 (v9.15.0 智能全端清退与一键复制版)
# ==========================================

app = Flask(__name__)

# --- 1. 配置加载与基础设定 ---
CONFIG_PATH = '/config/config.json'
WASH_HISTORY_FILE = '/config/wash_history.json'

MOVIE_AUX_DIRS = {'extrafanart', 'extrathumbs', '.actors', 'theme-videos', 'theme-music', 'trailers', 'sample', 'subs', 'subtitles'}

def load_config():
    default_config = {
        "MOUNT_BASE_PATH": "/media/CloudDrive",
        "METADATA_BASE_PATH": "/metadata",
        "WATCHED_DIRS": ["/115open/9KG/STRM"],
        "DRY_RUN": True,
        "DB_ONLINE_WEBHOOK": "",
        "STRM_WEBHOOK": "",
        "IGNORE_LEADING_DIGITS": True
    }
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
                default_config.update(loaded)
        except Exception as e:
            print(f"配置文件加载失败: {e}，使用默认安全配置。")
    return default_config

def save_config(new_config_data):
    current = load_config()
    current.update(new_config_data)
    try:
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(current, f, ensure_ascii=False, indent=4)
        return True, current
    except Exception as e:
        return False, str(e)

config = load_config()
MOUNT_BASE_PATH = config.get("MOUNT_BASE_PATH", "/media")
METADATA_BASE_PATH = config.get("METADATA_BASE_PATH", "/metadata")
WATCHED_DIRS = config.get("WATCHED_DIRS", [])
DRY_RUN = config.get("DRY_RUN", True)
DB_ONLINE_WEBHOOK = config.get("DB_ONLINE_WEBHOOK", "")
STRM_WEBHOOK = config.get("STRM_WEBHOOK", "")
IGNORE_LEADING_DIGITS = config.get("IGNORE_LEADING_DIGITS", True)

def load_wash_history():
    if os.path.exists(WASH_HISTORY_FILE):
        try:
            with open(WASH_HISTORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except: pass
    return {}

def save_wash_history(history):
    try:
        with open(WASH_HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=4)
    except: pass

# --- 2. 日志系统 ---
LOG_DIR = '/config/logs'
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)
LOG_FILE = os.path.join(LOG_DIR, 'xiban.log')

class CleanRotatingFileHandler(TimedRotatingFileHandler):
    def __init__(self, filename, **kwargs):
        super().__init__(filename, **kwargs)
        self.namer = lambda name: name.replace(".log.", "-") + ".log" if ".log." in name else name

    def getFilesToDelete(self):
        dirName, baseName = os.path.split(self.baseFilename)
        fileNames = os.listdir(dirName)
        result = []
        prefix = baseName.replace(".log", "-")
        for fileName in fileNames:
            if fileName.startswith(prefix) and fileName.endswith(".log"):
                suffix = fileName[len(prefix):-4]
                if re.compile(r"^\d{4}-\d{2}-\d{2}$").match(suffix):
                    result.append(os.path.join(dirName, fileName))
        if len(result) < self.backupCount:
            result = []
        else:
            result.sort()
            result = result[:len(result) - self.backupCount]
        return result

logger = logging.getLogger('9KGwash')
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

file_handler = CleanRotatingFileHandler(LOG_FILE, when='midnight', interval=1, backupCount=3, encoding='utf-8')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

logging.getLogger('werkzeug').setLevel(logging.ERROR)

# --- 3. 安全删除与路径推导 ---
def safe_delete_dir(dir_path):
    protected_paths = [MOUNT_BASE_PATH.rstrip('/'), METADATA_BASE_PATH.rstrip('/')]
    for w in WATCHED_DIRS:
        protected_paths.append((MOUNT_BASE_PATH.rstrip('/') + w).rstrip('/'))
        protected_paths.append((METADATA_BASE_PATH.rstrip('/') + w.replace('/115open/', '/')).rstrip('/'))
    
    is_safe = False
    for p in protected_paths:
        if dir_path.startswith(p) and dir_path.rstrip('/') != p:
            is_safe = True
            break
            
    if not is_safe or dir_path.rstrip('/') in protected_paths:
        logger.warning(f"⚠️ [安全锁] 试图删除越界或受保护的根目录 {dir_path}，已强制拦截！")
        return False
        
    if os.path.exists(dir_path):
        try:
            shutil.rmtree(dir_path)
            return True
        except Exception as e:
            logger.error(f"❌ [删除失败] 物理删除目录异常 [{dir_path}]: {e}")
            return False
    return False

def fast_check_folder_exists(base_path, keyword, max_depth=3):
    if not os.path.exists(base_path): 
        return False
    keyword = keyword.upper()
    dirs_to_scan = [(base_path, 0)]
    
    while dirs_to_scan:
        current_path, depth = dirs_to_scan.pop(0)
        if depth > max_depth:
            continue
        try:
            with os.scandir(current_path) as it:
                for entry in it:
                    if entry.is_dir():
                        if keyword in entry.name.upper():
                            return True
                        dirs_to_scan.append((entry.path, depth + 1))
        except Exception:
            continue
    return False

def meta_to_mount_dir(meta_dir):
    """根据本地元数据目录推导对应的网盘挂载目录，支持自适应容错"""
    if not meta_dir or not meta_dir.startswith(METADATA_BASE_PATH.rstrip('/')):
        return None
    rel = os.path.relpath(meta_dir, METADATA_BASE_PATH.rstrip('/'))
    for w in WATCHED_DIRS:
        w_meta_sub = w.replace('/115open/', '/').strip('/')
        if rel == w_meta_sub or rel.startswith(w_meta_sub + os.sep) or rel.startswith(w_meta_sub + '/'):
            w_mount_sub = w.strip('/')
            mount_rel = rel.replace(w_meta_sub, w_mount_sub, 1)
            mount_path = os.path.join(MOUNT_BASE_PATH, mount_rel)
            if os.path.exists(mount_path):
                return mount_path
            # 兼容大小写或网盘端轻微命名差异
            parent_mount = os.path.dirname(mount_path)
            if os.path.exists(parent_mount):
                target_name = os.path.basename(meta_dir).upper()
                try:
                    for entry in os.scandir(parent_mount):
                        if entry.is_dir() and entry.name.upper() == target_name:
                            return entry.path
                except Exception:
                    pass
            return mount_path
    return os.path.join(MOUNT_BASE_PATH, rel)

# --- 4. 核心番号解析与双轨比对逻辑 ---
def is_jav(text):
    if not text: return False
    return bool(re.search(r'[a-zA-Z0-9]+-\d+', text))

def parse_fanhao_info(text):
    if not text:
        return None, None, False
    match = re.search(r'([a-zA-Z0-9]+-\d+)', text)
    if match:
        raw = match.group(1).upper()
        stripped = re.sub(r'^\d+([a-zA-Z])', r'\1', raw)
        has_leading = (stripped != raw)
        canonical = stripped if has_leading else raw
        return canonical, raw, has_leading
    return None, None, False

def extract_pure_fanhao(text, apply_ignore=False):
    canonical, raw, _ = parse_fanhao_info(text)
    if not canonical:
        return text.strip()
    if apply_ignore and IGNORE_LEADING_DIGITS:
        return canonical
    return raw

def get_weight_and_fanhao(filename):
    basename = os.path.splitext(filename)[0]
    canonical, raw, _ = parse_fanhao_info(basename)
    
    if canonical:
        upper_base = basename.upper()
        is_part = bool(re.search(r'(CD[1-9]|PART[A-E]|[-_][AB](?![A-Z0-9]))', upper_base))
        has_c = bool(re.search(r'([-_]C(?![A-Z0-9])|[-_]UC(?![A-Z0-9])|[-_]CU(?![A-Z0-9])|中字|字幕)', upper_base))
        has_u = bool(re.search(r'([-_]U(?![A-Z0-9])|[-_]UC(?![A-Z0-9])|[-_]CU(?![A-Z0-9])|无码|破解|流出)', upper_base))
        
        weight = 0
        if has_c and has_u: weight = 3
        elif has_c: weight = 2
        elif has_u: weight = 1
        
        root_fanhao = canonical if IGNORE_LEADING_DIGITS else raw
        return root_fanhao, weight, is_part
    else:
        return basename.strip(), 0, False

def clean_exact_metadata(loser_basename, folder_path, only_delete_json=False):
    deleted_basenames = set()
    if DRY_RUN: return deleted_basenames
    if not folder_path or not os.path.exists(folder_path): return deleted_basenames

    try:
        with os.scandir(folder_path) as it:
            for entry in it:
                if entry.is_file():
                    clean_f = entry.name[2:] if entry.name.startswith("._") else entry.name
                    f_base, f_ext = os.path.splitext(clean_f)
                    f_ext = f_ext.lower()

                    if f_base == loser_basename:
                        if only_delete_json:
                            if f_ext == '.json':
                                os.remove(entry.path)
                                deleted_basenames.add(loser_basename)
                                logger.info(f"   [-] ✅ 成功清理关联文件 (保留覆盖件): {entry.name}")
                        else:
                            if f_ext in ['.strm', '.nfo', '.json', '.srt', '.ass', '.vtt']:
                                os.remove(entry.path)
                                deleted_basenames.add(loser_basename)
                                logger.info(f"   [-] ✅ 成功连带清理关联文件: {entry.name}")
    except Exception as e:
        logger.error(f"   [-] ❌ 扫描文件夹清理失败 [{folder_path}]: {e}")
        
    return deleted_basenames

def trigger_cms(reason="触发 STRM 生成"):
    if not STRM_WEBHOOK:
        return
    if DRY_RUN:
        logger.info(f"   [🔔 主动唤醒] [Dry Run] 模拟 {reason}，跳过真实请求发送。")
        return
        
    logger.info(f"   [🔔 主动唤醒] {reason}，等待 2 秒落盘缓冲后向 CMS 发送指令...")
    time.sleep(2) 
    try:
        r = requests.get(STRM_WEBHOOK, timeout=5)
        logger.info(f"   [-] ✅ CMS 唤醒成功，状态码: {r.status_code}")
    except Exception as e:
        logger.error(f"   [-] ❌ CMS 唤醒失败: {e}")

def process_xiban(file_path):
    target_canonical, target_raw, _ = parse_fanhao_info(os.path.basename(file_path))
    if not target_canonical or not is_jav(target_canonical):
        logger.info(f"ℹ️ [跳过] 目标 [{os.path.basename(file_path)}] 非 JAV 番号，不参与洗板。")
        return

    _, target_weight, target_is_part = get_weight_and_fanhao(os.path.basename(file_path))
    parent_dir = os.path.dirname(os.path.dirname(file_path))
    
    suspect_dirs = []
    if os.path.exists(parent_dir):
        try:
            with os.scandir(parent_dir) as it:
                for entry in it:
                    if entry.is_dir():
                        d_canonical, d_raw, _ = parse_fanhao_info(entry.name)
                        if d_canonical == target_canonical or \
                           (target_canonical in entry.name.upper()) or \
                           (target_raw in entry.name.upper()):
                            suspect_dirs.append(entry.path)
        except Exception:
            pass
    
    if len(suspect_dirs) > 1:
        logger.info(f"📡 [雷达] 发现标的物核心特征 [{target_canonical}] 分散在 {len(suspect_dirs)} 个文件夹中，双向跨目录雷达启动。")
        
    all_files = []
    video_exts = ('.mp4', '.mkv', '.avi', '.ts', '.wmv', '.iso', '.rmvb', '.flv', '.m2ts')
    for d in suspect_dirs:
        try:
            with os.scandir(d) as it:
                for entry in it:
                    if entry.is_file() and entry.name.lower().endswith(video_exts):
                        all_files.append(entry.path)
        except Exception:
            pass
                
    competitors = []
    for f in all_files:
        c_canonical, c_raw, _ = parse_fanhao_info(os.path.basename(f))
        if c_canonical == target_canonical:
            _, weight, is_part = get_weight_and_fanhao(os.path.basename(f))
            competitors.append({
                'path': f,
                'weight': weight,
                'is_part': is_part,
                'folder_mtime': os.path.getmtime(os.path.dirname(f)),
                'file_mtime': os.path.getmtime(f),
                'basename': os.path.splitext(os.path.basename(f))[0],
                'raw_fanhao': c_raw
            })

    version_groups = {}
    for c in competitors:
        ext = os.path.splitext(c['path'])[1].lower()
        if not c['is_part']:
            stripped = c['basename']
            display_name = c['basename']
        else:
            stripped = re.sub(r'[-_]?(CD[1-9]|PART[A-E])(?![a-zA-Z0-9])$|[-_][AB](?![a-zA-Z0-9])$', '', c['basename'], flags=re.IGNORECASE)
            display_name = f"{stripped} (分盘编队)"
            
        v_hash = f"{stripped}|{ext}|{c['weight']}|{c['is_part']}"
        
        if v_hash not in version_groups:
            version_groups[v_hash] = {
                'weight': c['weight'],
                'is_part': c['is_part'],
                'files': [],
                'folder_mtime': c['folder_mtime'],
                'file_mtime': c['file_mtime'],
                'v_hash': v_hash,
                'display_name': display_name
            }
        version_groups[v_hash]['files'].append(c)
        version_groups[v_hash]['folder_mtime'] = max(version_groups[v_hash]['folder_mtime'], c['folder_mtime'])
        version_groups[v_hash]['file_mtime'] = max(version_groups[v_hash]['file_mtime'], c['file_mtime'])
        
    versions = list(version_groups.values())

    if len(versions) <= 1:
        logger.info(f"ℹ️ [跳过] 库中 JAV 目标 [{target_canonical}] 仅有 1 个版本队列，无需洗板。")
        trigger_cms("发现新文件落盘(仅单队列)")
        return

    actually_killed_metadata = set()
    washed_anything = False 
    
    versions.sort(key=lambda x: (x['weight'], x['folder_mtime'], x['file_mtime']), reverse=True)
    king_version = versions[0]
    
    logger.info(f"🎯 [执行] [{target_canonical}] 锁定 {len(versions)} 个不同版本阵列，触发公平决斗！保留王者: {king_version['display_name']} (权重:{king_version['weight']})")
    
    for loser_version in versions[1:]:
        if loser_version['weight'] < king_version['weight']:
            reason = "低优画质"
            only_delete_json = False
        else:
            if loser_version['is_part']:
                reason = "同质碎片冗余"
                only_delete_json = False
            else:
                reason = "同质单体冗余"
                only_delete_json = True

        for loser in loser_version['files']:
            loser_basename = loser['basename']
            loser_dir = os.path.dirname(loser['path'])
            meta_dir_path = loser_dir.replace(MOUNT_BASE_PATH, METADATA_BASE_PATH).replace('/115open/', '/')
            
            if not DRY_RUN:
                actually_killed_metadata.add(loser_basename)
                
                if os.path.exists(loser['path']):
                    try:
                        os.remove(loser['path'])
                        logger.info(f"   [-] ✅ 成功降维删除 {reason}: {os.path.basename(loser['path'])}")
                        washed_anything = True
                    except Exception as e:
                        logger.error(f"   [-] ❌ 删除视频失败: {e}")

                d1 = clean_exact_metadata(loser_basename, loser_dir, only_delete_json)
                d2 = clean_exact_metadata(loser_basename, meta_dir_path, only_delete_json)
                actually_killed_metadata.update(d1)
                actually_killed_metadata.update(d2)

                if os.path.exists(meta_dir_path) and not os.listdir(meta_dir_path):
                    safe_delete_dir(meta_dir_path)
                if os.path.exists(loser_dir) and not os.listdir(loser_dir):
                    safe_delete_dir(loser_dir)
            else:
                logger.info(f"   [-] [Dry Run] 准备删除 {reason}: {os.path.basename(loser['path'])}")

    if actually_killed_metadata:
        history = load_wash_history()
        now = time.time()
        for k in actually_killed_metadata:
            history[k] = now
        history = {k: v for k, v in history.items() if now - v < 604800}
        save_wash_history(history)
        logger.info(f"   [🛡️ 拦截器就绪] 准确记录了 {len(actually_killed_metadata)} 项清理特征进入档案，等待防御 Emby 延迟通知。")

    if washed_anything or len(versions) > 1:
        trigger_cms("洗板判定与清理流程结束")

# --- 5. Web/API 路由层 ---

WEBUI_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>9KGwash | 自动洗板终端</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body { background-color: #0f172a; color: #e2e8f0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
        .pink-glow { color: #ec4899; text-shadow: 0 0 10px rgba(236, 72, 153, 0.7); }
        .border-pink { border-color: #ec4899; }
        .log-container { background-color: #1e293b; font-family: 'Courier New', Courier, monospace; overflow-y: auto; }
        .scrollbar-hide::-webkit-scrollbar { display: none; }
        .scrollbar-hide { -ms-overflow-style: none; scrollbar-width: none; }
    </style>
    <script>
        let isLogPaused = false;
        let lastFetchedLogs = [];
        let scannedEmptyFolders = [];
        let scannedDuplicates = [];
        let currentTab = 'hound';

        function showToast(msg) {
            const toast = document.getElementById('toast');
            const toastMsg = document.getElementById('toast-msg');
            if (!toast || !toastMsg) return;
            toastMsg.innerText = msg;
            toast.classList.remove('opacity-0', 'pointer-events-none', '-translate-y-2');
            toast.classList.add('opacity-100', 'translate-y-0');
            setTimeout(() => {
                toast.classList.add('opacity-0', 'pointer-events-none', '-translate-y-2');
                toast.classList.remove('opacity-100', 'translate-y-0');
            }, 1600);
        }

        function copyFanhao(text, event, el) {
            if (event) event.stopPropagation();
            const doCopySuccess = () => {
                if (el) {
                    const originalHtml = el.innerHTML;
                    el.innerHTML = '✅ 已复制';
                    el.classList.add('bg-green-600', 'text-white');
                    setTimeout(() => {
                        el.innerHTML = originalHtml;
                        el.classList.remove('bg-green-600', 'text-white');
                    }, 1200);
                }
                showToast(`已复制番号: ${text}`);
            };

            if (navigator.clipboard && window.isSecureContext) {
                navigator.clipboard.writeText(text).then(doCopySuccess).catch(() => fallbackCopy(text, doCopySuccess));
            } else {
                fallbackCopy(text, doCopySuccess);
            }
        }

        function fallbackCopy(text, cb) {
            const ta = document.createElement('textarea');
            ta.value = text;
            ta.style.position = 'fixed';
            ta.style.opacity = '0';
            document.body.appendChild(ta);
            ta.focus();
            ta.select();
            try {
                document.execCommand('copy');
                if (cb) cb();
            } catch (err) {
                alert('复制失败: ' + text);
            }
            document.body.removeChild(ta);
        }

        function switchTab(tab) {
            currentTab = tab;
            const houndTab = document.getElementById('tab-hound');
            const dedupTab = document.getElementById('tab-dedup');
            const houndView = document.getElementById('view-hound');
            const dedupView = document.getElementById('view-dedup');

            if (tab === 'hound') {
                houndTab.className = "flex-1 py-2 text-xs font-bold rounded bg-pink-600 text-white shadow";
                dedupTab.className = "flex-1 py-2 text-xs font-bold rounded bg-slate-900 text-slate-400 hover:text-white";
                houndView.classList.remove('hidden');
                dedupView.classList.add('hidden');
            } else {
                dedupTab.className = "flex-1 py-2 text-xs font-bold rounded bg-pink-600 text-white shadow";
                houndTab.className = "flex-1 py-2 text-xs font-bold rounded bg-slate-900 text-slate-400 hover:text-white";
                dedupView.classList.remove('hidden');
                houndView.classList.add('hidden');
            }
        }

        function toggleLogPause() {
            isLogPaused = !isLogPaused;
            const btn = document.getElementById('pause-btn');
            const indicator = document.getElementById('log-status-indicator');
            
            if (isLogPaused) {
                btn.innerHTML = '▶️ 恢复刷新';
                btn.className = 'text-xs bg-pink-700 hover:bg-pink-600 text-white px-3 py-1.5 rounded transition-colors shadow-lg active:scale-95';
                indicator.innerHTML = '<span class="mr-1 h-2 w-2 bg-yellow-400 rounded-full inline-block"></span> 已暂停';
                indicator.className = 'text-xs text-yellow-400 flex items-center font-bold';
            } else {
                btn.innerHTML = '⏸️ 暂停刷新';
                btn.className = 'text-xs bg-slate-700 hover:bg-slate-600 text-slate-300 px-3 py-1.5 rounded transition-colors shadow-lg active:scale-95';
                indicator.innerHTML = '<span class="animate-pulse mr-1 h-2 w-2 bg-green-400 rounded-full inline-block"></span> 实时监控中';
                indicator.className = 'text-xs text-green-400 flex items-center font-bold';
                fetchLogs(); 
            }
        }

        async function fetchLogs() {
            if (isLogPaused) return; 
            try {
                const response = await fetch('/api/logs');
                const data = await response.json();
                const newLogs = data.logs;
                lastFetchedLogs = newLogs;

                const logBox = document.getElementById('log-box');
                const isScrolledToBottom = logBox.scrollHeight - logBox.clientHeight <= logBox.scrollTop + 5;
                logBox.innerHTML = newLogs.map(line => `<div>${line}</div>`).join('');
                if (isScrolledToBottom) logBox.scrollTop = logBox.scrollHeight;
            } catch (e) {}
        }

        async function saveConfig(event) {
            event.preventDefault();
            const webhook = document.getElementById('db_webhook').value.trim();
            const strmWebhook = document.getElementById('strm_webhook').value.trim();
            const dryRun = document.getElementById('dry_run').value === 'true';
            const ignoreDigits = document.getElementById('ignore_digits').value === 'true';
            const statusMsg = document.getElementById('status-msg');

            statusMsg.innerText = "正在保存...";
            statusMsg.className = "text-xs text-yellow-400 font-bold";

            try {
                const response = await fetch('/api/config', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ DB_ONLINE_WEBHOOK: webhook, STRM_WEBHOOK: strmWebhook, DRY_RUN: dryRun, IGNORE_LEADING_DIGITS: ignoreDigits })
                });
                const res = await response.json();
                if (res.success) {
                    statusMsg.innerText = "✅ 已保存并实时生效！";
                    statusMsg.className = "text-xs text-green-400 font-bold";
                    setTimeout(() => location.reload(), 1200);
                } else {
                    statusMsg.innerText = "❌ 保存失败";
                    statusMsg.className = "text-xs text-red-400 font-bold";
                }
            } catch(e) {
                statusMsg.innerText = "❌ 网络异常";
                statusMsg.className = "text-xs text-red-400 font-bold";
            }
        }

        async function scanEmptyFolders() {
            const btn = document.getElementById('scan-btn');
            const resDiv = document.getElementById('scan-results');
            const placeholder = document.getElementById('scan-placeholder');
            const listUl = document.getElementById('scan-list');

            btn.innerText = "⏳ 扫描本地目录中(请看右侧日志进度)...";
            btn.disabled = true;

            try {
                const res = await fetch('/api/scan_empty_folders');
                const data = await res.json();
                scannedEmptyFolders = [...data.empty_meta];

                listUl.innerHTML = '';
                if (scannedEmptyFolders.length === 0) {
                    listUl.innerHTML = '<li class="text-green-400 text-center mt-8 font-bold">✅ 完美！未发现任何本地无STRM的空壳目录。</li>';
                    document.getElementById('del-scan-btn').style.display = 'none';
                } else {
                    scannedEmptyFolders.forEach(f => {
                        listUl.innerHTML += `<li class="truncate hover:text-pink-400 transition-colors border-b border-slate-700 pb-1 pt-1" title="${f}">${f}</li>`;
                    });
                    document.getElementById('del-scan-btn').style.display = 'block';
                }
                placeholder.classList.add('hidden');
                resDiv.classList.remove('hidden');
                btn.style.display = 'none';
            } catch (e) {
                alert("扫描请求失败！请检查系统状态。");
            } finally {
                btn.innerText = "🔍 查找本地空壳文件夹";
                btn.disabled = false;
            }
        }

        function cancelScan() {
            scannedEmptyFolders = [];
            document.getElementById('scan-results').classList.add('hidden');
            document.getElementById('scan-placeholder').classList.remove('hidden');
            document.getElementById('scan-btn').style.display = 'block';
        }

        async function deleteScannedFolders() {
            if (scannedEmptyFolders.length === 0) return;
            if (!confirm(`⚠️ 高能警告：\\n即将永久物理删除列表中找到的 ${scannedEmptyFolders.length} 个本地空壳文件夹。\\n操作不可逆，确定执行吗？`)) return;

            const btn = document.getElementById('del-scan-btn');
            btn.innerText = "🗑️ 正在抹除...";
            btn.disabled = true;

            try {
                const res = await fetch('/api/delete_empty_folders', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ folders: scannedEmptyFolders })
                });
                const data = await res.json();
                if (data.success) {
                    alert(`✅ 清理战报：\\n成功抹除: ${data.deleted} 个\\n受保护跳过: ${data.failed} 个`);
                    cancelScan();
                } else {
                    alert("删除过程中出现异常，请查看后台日志。");
                }
            } catch (e) {
                alert("网络请求失败！");
            } finally {
                btn.innerText = "🗑️ 确认删除全部";
                btn.disabled = false;
            }
        }

        async function scanDuplicates() {
            const btn = document.getElementById('dedup-scan-btn');
            const resDiv = document.getElementById('dedup-results');
            const placeholder = document.getElementById('dedup-placeholder');
            const listUl = document.getElementById('dedup-list');

            btn.innerText = "⏳ 扫描本地库中(请看右侧日志进度)...";
            btn.disabled = true;

            try {
                const res = await fetch('/api/scan_duplicates');
                const data = await res.json();
                if (!data.success) {
                    alert("查重失败: " + data.error);
                    return;
                }
                scannedDuplicates = data.duplicates;
                listUl.innerHTML = '';

                if (scannedDuplicates.length === 0) {
                    listUl.innerHTML = '<li class="text-green-400 text-center mt-8 font-bold">✅ 媒体库干干净净！未发现任何本地重复影片。</li>';
                    document.getElementById('dedup-wash-btn').style.display = 'none';
                } else {
                    scannedDuplicates.forEach((item, idx) => {
                        let filesHtml = item.files.map(f => `
                            <div class="flex justify-between items-center text-xs py-1 border-b border-slate-800 ${f.is_winner ? 'text-green-400 font-semibold' : 'text-slate-400'}">
                                <span class="truncate flex-1" title="${f.path}">${f.is_winner ? '👑 ' : '🗑️ '}${f.name}</span>
                                <span class="ml-2 px-1.5 py-0.5 rounded bg-slate-800 text-[10px] text-pink-300">权:${f.weight}</span>
                            </div>
                        `).join('');

                        let rawBadges = item.raw_fanhaos.map(rf => `
                            <span onclick="copyFanhao('${rf}', event, this)" 
                                  class="cursor-pointer bg-slate-800 hover:bg-pink-600 hover:text-white text-pink-300 px-1.5 py-0.5 rounded transition-all duration-150 active:scale-95 inline-flex items-center gap-1 text-[11px] font-mono border border-slate-700/60"
                                  title="点击一键复制番号 ${rf}">
                                📋 ${rf}
                            </span>
                        `).join(' ');

                        listUl.innerHTML += `
                            <li class="bg-slate-950 p-2.5 rounded border border-slate-700/80 space-y-1.5">
                                <div class="flex justify-between items-center border-b border-slate-800 pb-1">
                                    <span onclick="copyFanhao('${item.canonical_fanhao}', event, this)" 
                                          class="font-bold text-pink-400 hover:text-pink-300 cursor-pointer text-xs flex items-center gap-1"
                                          title="点击复制归一化主番号">
                                        #${idx+1} 【${item.canonical_fanhao}】
                                    </span>
                                    <div class="flex items-center gap-1 text-[11px] text-slate-400">
                                        <span>同源番号:</span>
                                        <div class="flex flex-wrap gap-1">${rawBadges}</div>
                                    </div>
                                </div>
                                <div class="space-y-0.5">${filesHtml}</div>
                            </li>
                        `;
                    });
                    document.getElementById('dedup-wash-btn').style.display = 'block';
                }
                placeholder.classList.add('hidden');
                resDiv.classList.remove('hidden');
                btn.style.display = 'none';
            } catch (e) {
                alert("查重请求异常，请检查后端网络状态。");
            } finally {
                btn.innerText = "🔍 启动本地媒体查重";
                btn.disabled = false;
            }
        }

        function cancelDedupScan() {
            scannedDuplicates = [];
            document.getElementById('dedup-results').classList.add('hidden');
            document.getElementById('dedup-placeholder').classList.remove('hidden');
            document.getElementById('dedup-scan-btn').style.display = 'block';
        }

        async function resolveDuplicates() {
            if (scannedDuplicates.length === 0) return;
            if (!confirm(`⚠️ 终极洗板警告：\\n即将清理选定的 ${scannedDuplicates.length} 组重复影片！\\n\\n【本操作将直接删除被淘汰影片所在的整个本地文件夹以及网盘上对应的整个文件夹】\\n(若当前为 DRY RUN 模式则仅记录日志，不实施真删)\\n确定继续执行吗？`)) return;

            const btn = document.getElementById('dedup-wash-btn');
            btn.innerText = "🧹 执行全端清退中...";
            btn.disabled = true;

            try {
                const res = await fetch('/api/resolve_duplicates', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ duplicates: scannedDuplicates })
                });
                const data = await res.json();
                if (data.success) {
                    alert(`✅ 查重洗板圆满结束！\\n成功清退冗余目录/文件: ${data.deleted} 处`);
                    cancelDedupScan();
                } else {
                    alert("执行洗板出现异常，请参阅活动日志。");
                }
            } catch (e) {
                alert("网络请求失败！");
            } finally {
                btn.innerText = "⚡ 一键洗板清理";
                btn.disabled = false;
            }
        }

        setInterval(fetchLogs, 2000); 
        window.onload = () => { fetchLogs(); };
    </script>
</head>
<body class="h-screen flex flex-col p-6 overflow-hidden">
    <!-- 复制轻提示 Toast -->
    <div id="toast" class="fixed top-5 right-5 z-50 transform -translate-y-2 transition-all duration-300 opacity-0 pointer-events-none bg-pink-600 text-white font-bold text-xs px-4 py-2 rounded-lg shadow-xl flex items-center gap-2 border border-pink-400">
        <span>📋</span>
        <span id="toast-msg">已复制</span>
    </div>

    <header class="flex-shrink-0 flex items-center justify-between border-b border-pink pb-4 mb-6">
        <h1 class="text-3xl font-bold pink-glow tracking-wider">🌸 9KGwash 控制台</h1>
        <div class="text-sm flex items-center gap-4">
            <span>版本: <span class="font-mono text-pink-400">v9.15.0 智能全端清退与一键复制版</span></span>
            <span>状态: 
                {% if dry_run %}
                    <span class="text-yellow-400 font-bold bg-yellow-900/50 px-2 py-1 rounded">DRY RUN (模拟)</span>
                {% else %}
                    <span class="text-red-500 font-bold bg-red-900/50 px-2 py-1 rounded">实弹武装 (危险)</span>
                {% endif %}
            </span>
        </div>
    </header>

    <div class="flex flex-col lg:flex-row gap-6 flex-grow min-h-0">
        <div class="lg:w-1/3 flex flex-col gap-6 min-h-0">
            <div class="flex-shrink-0 bg-slate-800 p-5 rounded-lg shadow-lg border border-slate-700">
                <div class="flex justify-between items-center mb-4">
                    <h2 class="text-xl font-semibold text-pink-400">⚙️ 运行时动态配置</h2>
                    <span id="status-msg" class="text-xs"></span>
                </div>
                <form onsubmit="saveConfig(event)" class="space-y-4">
                    <div>
                        <label class="block text-sm font-medium text-slate-300 mb-1">DB 目标 Webhook 地址 (POST)</label>
                        <input type="text" id="db_webhook" value="{{ db_webhook }}" placeholder="http://192.168.x.x:port/webhook" class="w-full bg-slate-900 border border-slate-600 rounded px-3 py-2 text-sm text-slate-100 font-mono focus:outline-none focus:border-pink-500 transition-colors">
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-slate-300 mb-1">STRM 软件唤醒 Webhook (GET)</label>
                        <input type="text" id="strm_webhook" value="{{ strm_webhook }}" placeholder="http://192.168.x.x:port/api/trigger" class="w-full bg-slate-900 border border-slate-600 rounded px-3 py-2 text-sm text-slate-100 font-mono focus:outline-none focus:border-pink-500 transition-colors">
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-slate-300 mb-1">运行模式 (洗板开关)</label>
                        <select id="dry_run" class="w-full bg-slate-900 border border-slate-600 rounded px-3 py-2 text-sm text-slate-100 font-mono focus:outline-none focus:border-pink-500 transition-colors">
                            <option value="true" {% if dry_run %}selected{% endif %}>🟡 DRY RUN (仅日志模拟，不真删文件)</option>
                            <option value="false" {% if not dry_run %}selected{% endif %}>🔴 实弹武装 (删除文件与元数据)</option>
                        </select>
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-slate-300 mb-1">开启去除番号前置数字 (双向匹配通吃)</label>
                        <select id="ignore_digits" class="w-full bg-slate-900 border border-slate-600 rounded px-3 py-2 text-sm text-slate-100 font-mono focus:outline-none focus:border-pink-500 transition-colors">
                            <option value="false" {% if not ignore_digits %}selected{% endif %}>❌ 关闭 (精准匹配原名)</option>
                            <option value="true" {% if ignore_digits %}selected{% endif %}>✅ 开启 (前置数字与标准番号双向互洗，且转发去除前缀)</option>
                        </select>
                    </div>
                    <button type="submit" class="w-full bg-pink-600 hover:bg-pink-500 text-white font-bold py-2 px-4 rounded transition-all shadow-md active:scale-95">💾 保存修改 (实时生效)</button>
                </form>
            </div>

            <div class="flex-grow flex flex-col bg-slate-800 p-5 rounded-lg shadow-lg border border-slate-700 min-h-0">
                <div class="flex-shrink-0 flex gap-2 mb-3 bg-slate-900 p-1 rounded">
                    <button id="tab-hound" onclick="switchTab('hound')" class="flex-1 py-2 text-xs font-bold rounded bg-pink-600 text-white shadow">🧽 空间猎犬</button>
                    <button id="tab-dedup" onclick="switchTab('dedup')" class="flex-1 py-2 text-xs font-bold rounded bg-slate-900 text-slate-400 hover:text-white">🔍 影片查重</button>
                </div>

                <!-- 子视窗 1: 空间猎犬 -->
                <div id="view-hound" class="flex-grow flex flex-col min-h-0">
                    <button id="scan-btn" onclick="scanEmptyFolders()" class="flex-shrink-0 w-full bg-slate-700 hover:bg-slate-600 text-white px-4 py-2 rounded text-sm font-bold shadow-md transition-all active:scale-95 mb-3">🔍 查找本地空壳文件夹</button>

                    <div id="scan-placeholder" class="flex-grow flex flex-col items-center justify-center text-slate-500 text-sm border border-dashed border-slate-700/60 rounded p-4 text-center">
                        <p>暂无空壳扫描数据</p>
                        <p class="text-xs text-slate-600 mt-1">点击上方按钮排查本地无 STRM 的残留孤儿目录</p>
                    </div>

                    <div id="scan-results" class="hidden flex-grow flex flex-col gap-3 min-h-0">
                        <div class="flex-grow bg-slate-900 p-2 rounded overflow-y-auto scrollbar-hide font-mono text-xs text-slate-300 shadow-inner">
                            <ul id="scan-list" class="space-y-1"></ul>
                        </div>
                        <div class="flex-shrink-0 flex gap-2">
                            <button id="del-scan-btn" onclick="deleteScannedFolders()" class="w-2/3 bg-red-600 hover:bg-red-500 text-white px-4 py-2 rounded text-sm font-bold shadow-md transition-all active:scale-95">🗑️ 确认删除全部</button>
                            <button onclick="cancelScan()" class="w-1/3 bg-slate-600 hover:bg-slate-500 text-white px-4 py-2 rounded text-sm font-bold shadow-md transition-all active:scale-95">取消</button>
                        </div>
                    </div>
                </div>

                <!-- 子视窗 2: 影片查重 -->
                <div id="view-dedup" class="hidden flex-grow flex flex-col min-h-0">
                    <button id="dedup-scan-btn" onclick="scanDuplicates()" class="flex-shrink-0 w-full bg-pink-700 hover:bg-pink-600 text-white px-4 py-2 rounded text-sm font-bold shadow-md transition-all active:scale-95 mb-3">🔍 启动本地媒体查重</button>

                    <div id="dedup-placeholder" class="flex-grow flex flex-col items-center justify-center text-slate-500 text-sm border border-dashed border-slate-700/60 rounded p-4 text-center">
                        <p>暂无查重数据</p>
                        <p class="text-xs text-slate-600 mt-1">支持标准纯番号严格比对及前置数字双向归一化互查</p>
                    </div>

                    <div id="dedup-results" class="hidden flex-grow flex flex-col gap-3 min-h-0">
                        <div class="flex-grow bg-slate-900 p-2 rounded overflow-y-auto scrollbar-hide font-mono text-xs text-slate-300 shadow-inner">
                            <ul id="dedup-list" class="space-y-2"></ul>
                        </div>
                        <div class="flex-shrink-0 flex gap-2">
                            <button id="dedup-wash-btn" onclick="resolveDuplicates()" class="w-2/3 bg-red-600 hover:bg-red-500 text-white px-4 py-2 rounded text-sm font-bold shadow-md transition-all active:scale-95">⚡ 一键洗板清理</button>
                            <button onclick="cancelDedupScan()" class="w-1/3 bg-slate-600 hover:bg-slate-500 text-white px-4 py-2 rounded text-sm font-bold shadow-md transition-all active:scale-95">取消</button>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <div class="lg:w-2/3 flex flex-col bg-slate-800 p-5 rounded-lg shadow-lg border border-slate-700 min-h-0">
            <div class="flex-shrink-0 flex items-center justify-between mb-4">
                <div class="flex items-center gap-4">
                    <h2 class="text-xl font-semibold text-pink-400 flex items-center"><span>📡 洗板活动日志 (Live)</span></h2>
                    <button id="pause-btn" onclick="toggleLogPause()" class="text-xs bg-slate-700 hover:bg-slate-600 text-slate-300 px-3 py-1.5 rounded transition-colors shadow-lg active:scale-95">⏸️ 暂停刷新</button>
                </div>
                <span id="log-status-indicator" class="text-xs text-green-400 flex items-center font-bold"><span class="animate-pulse mr-1 h-2 w-2 bg-green-400 rounded-full inline-block"></span> 实时监控中</span>
            </div>
            
            <div id="log-box" class="flex-grow log-container rounded p-4 text-sm whitespace-pre-wrap shadow-inner text-green-400 overflow-y-auto">
                正在建立安全连接，拉取系统日志...
            </div>
        </div>
    </div>
</body>
</html>
"""

@app.route('/')
def dashboard():
    return render_template_string(WEBUI_TEMPLATE, db_webhook=DB_ONLINE_WEBHOOK, strm_webhook=STRM_WEBHOOK, dry_run=DRY_RUN, ignore_digits=IGNORE_LEADING_DIGITS)

@app.route('/api/scan_duplicates', methods=['GET'])
def api_scan_duplicates():
    logger.info("🔍 [全库查重] 启动本地媒体库查重扫描，正在进行极速预检...")
    local_media_exts = ('.strm', '.mp4', '.mkv', '.avi', '.ts', '.wmv', '.iso', '.rmvb', '.flv', '.m2ts')
    scan_paths = []

    for w in WATCHED_DIRS:
        meta_path = (METADATA_BASE_PATH.rstrip('/') + w.replace('/115open/', '/')).rstrip('/')
        if os.path.exists(meta_path):
            scan_paths.append(meta_path)

    if not scan_paths:
        logger.warning("🔍 [全库查重] 未发现有效的本地元数据监听目录！")
        return jsonify({"success": True, "duplicates": []})

    def count_target_folders(base_path):
        count = 0
        for root, dirs, files in os.walk(base_path):
            if os.path.basename(root).lower() in MOVIE_AUX_DIRS:
                continue
            dirs[:] = [d for d in dirs if d.lower() not in MOVIE_AUX_DIRS]
            count += 1
        return count

    total_folders = 0
    for p in scan_paths:
        total_folders += count_target_folders(p)

    if total_folders == 0:
        logger.info("🔍 [全库查重] 本地目录为空，未发现任何可检查节点。")
        return jsonify({"success": True, "duplicates": []})

    logger.info(f"🔍 [全库查重] 本地预检完毕！共锁定 {total_folders} 个目录节点，正式开始查重与聚类...")
    scanned_count = 0
    total_files_indexed = 0
    fanhao_groups = {}

    try:
        for base_p in scan_paths:
            for root, dirs, files in os.walk(base_p):
                if os.path.basename(root).lower() in MOVIE_AUX_DIRS:
                    continue
                dirs[:] = [d for d in dirs if d.lower() not in MOVIE_AUX_DIRS]

                scanned_count += 1
                if scanned_count % 100 == 0 or scanned_count == total_folders:
                    percent = int((scanned_count / total_folders) * 100)
                    logger.info(f"🔍 [全库查重] 本地扫描进度: {percent}% ({scanned_count}/{total_folders}) | 累计索引 {total_files_indexed} 部媒体，归类 {len(fanhao_groups)} 个番号组")

                for f in files:
                    if f.lower().endswith(local_media_exts):
                        c_canonical, c_raw, _ = parse_fanhao_info(f)
                        if not c_canonical or not is_jav(c_canonical):
                            dir_name = os.path.basename(root)
                            c_canonical, c_raw, _ = parse_fanhao_info(dir_name)
                        
                        if c_canonical and is_jav(c_canonical):
                            f_path = os.path.join(root, f)
                            _, weight, is_part = get_weight_and_fanhao(f)
                            if c_canonical not in fanhao_groups:
                                fanhao_groups[c_canonical] = []
                            
                            total_files_indexed += 1
                            fanhao_groups[c_canonical].append({
                                'path': f_path,
                                'name': f,
                                'folder': os.path.basename(root),
                                'raw_fanhao': c_raw,
                                'weight': weight,
                                'is_part': is_part,
                                'folder_mtime': os.path.getmtime(root),
                                'file_mtime': os.path.getmtime(f_path),
                                'basename': os.path.splitext(f)[0]
                            })

        logger.info(f"🔍 [全库查重] 目录遍历完成，正比对 {len(fanhao_groups)} 组番号的多版本画质阵列...")
        duplicates_result = []
        for canonical, files in fanhao_groups.items():
            version_groups = {}
            for c in files:
                ext = os.path.splitext(c['path'])[1].lower()
                if not c['is_part']:
                    stripped = c['basename']
                else:
                    stripped = re.sub(r'[-_]?(CD[1-9]|PART[A-E])(?![a-zA-Z0-9])$|[-_][AB](?![a-zA-Z0-9])$', '', c['basename'], flags=re.IGNORECASE)
                v_hash = f"{stripped}|{ext}|{c['weight']}|{c['is_part']}"
                if v_hash not in version_groups:
                    version_groups[v_hash] = []
                version_groups[v_hash].append(c)

            distinct_raw = set(f['raw_fanhao'] for f in files)
            distinct_folders = set(f['folder'] for f in files)

            if len(version_groups) > 1 or len(distinct_folders) > 1 or len(distinct_raw) > 1:
                files.sort(key=lambda x: (x['weight'], x['folder_mtime'], x['file_mtime']), reverse=True)
                for idx, item in enumerate(files):
                    item['is_winner'] = (idx == 0)

                duplicates_result.append({
                    'canonical_fanhao': canonical,
                    'raw_fanhaos': list(distinct_raw),
                    'files': files
                })

        logger.info(f"🔍 [全库查重] 查重比对圆满完成！共检索 {total_files_indexed} 部本地媒体，锁定 {len(duplicates_result)} 组重复影片。")
        return jsonify({"success": True, "duplicates": duplicates_result})
    except Exception as e:
        logger.error(f"🔍 [全库查重] 扫描异常中断: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/resolve_duplicates', methods=['POST'])
def api_resolve_duplicates():
    data = request.get_json()
    duplicates = data.get('duplicates', [])
    deleted_count = 0
    actually_killed_metadata = set()

    for item in duplicates:
        files = item.get('files', [])
        if len(files) <= 1:
            continue
        king = files[0]
        losers = files[1:]
        
        king_dir = os.path.dirname(king['path'])
        king_mount_dir = meta_to_mount_dir(king_dir)

        for loser in losers:
            loser_path = loser['path']
            loser_basename = loser['basename']
            loser_dir = os.path.dirname(loser_path)
            mount_dir = meta_to_mount_dir(loser_dir)

            # 收集被淘汰目录内的所有特征主干，严密防御 Emby 延迟删除误报
            actually_killed_metadata.add(loser_basename)
            if os.path.exists(loser_dir):
                actually_killed_metadata.add(os.path.basename(loser_dir))
                try:
                    for entry in os.scandir(loser_dir):
                        if entry.is_file():
                            actually_killed_metadata.add(os.path.splitext(entry.name)[0])
                except Exception:
                    pass

            # 区分：若输家与王者位于不同目录，执行彻底的“本地+网盘整文件夹粉碎”
            if loser_dir != king_dir:
                if not DRY_RUN:
                    # 1. 删除网盘对应整个文件夹
                    if mount_dir and mount_dir != king_mount_dir:
                        if safe_delete_dir(mount_dir):
                            logger.info(f"   [-] ✅ [查重洗板] 成功粉碎网盘冗余目录: {mount_dir}")
                        else:
                            logger.warning(f"   [-] ⚠️ [查重洗板] 网盘目录未能直接删除或受保护: {mount_dir}")

                    # 2. 删除本地媒体文件所在的整个文件夹
                    if safe_delete_dir(loser_dir):
                        deleted_count += 1
                        logger.info(f"   [-] ✅ [查重洗板] 成功粉碎本地媒体所属目录: {loser_dir}")
                    else:
                        logger.warning(f"   [-] ⚠️ [查重洗板] 本地目录未能直接删除或受保护: {loser_dir}")
                else:
                    deleted_count += 1
                    logger.info(f"   [-] [Dry Run 查重洗板] 准备粉碎本地冗余目录: {loser_dir}")
                    if mount_dir and mount_dir != king_mount_dir:
                        logger.info(f"   [-] [Dry Run 查重洗板] 准备粉碎网盘对应目录: {mount_dir}")
            else:
                # 若输家与王者不幸处于同目录，仅删除输家个体媒体文件，确保王者的目录结构完好
                only_delete_json = (loser['weight'] == king['weight'] and not loser['is_part'])
                if not DRY_RUN:
                    if os.path.exists(loser_path):
                        try:
                            os.remove(loser_path)
                            deleted_count += 1
                            logger.info(f"   [-] ✅ [查重洗板] 同目录降维，成功删除本地冗余媒体: {os.path.basename(loser_path)}")
                        except Exception as e:
                            logger.error(f"   [-] ❌ 删除本地媒体失败: {e}")

                    if mount_dir and os.path.exists(mount_dir):
                        for v_ext in ('.mp4', '.mkv', '.avi', '.ts', '.wmv', '.iso', '.rmvb', '.flv', '.m2ts'):
                            cloud_video = os.path.join(mount_dir, loser_basename + v_ext)
                            if os.path.exists(cloud_video):
                                try:
                                    os.remove(cloud_video)
                                    logger.info(f"   [-] ✅ [查重洗板] 成功清理网盘原视频: {os.path.basename(cloud_video)}")
                                except Exception as e:
                                    logger.error(f"   [-] ❌ 删除网盘视频失败: {e}")

                    d1 = clean_exact_metadata(loser_basename, loser_dir, only_delete_json)
                    actually_killed_metadata.update(d1)
                    if mount_dir:
                        d2 = clean_exact_metadata(loser_basename, mount_dir, only_delete_json)
                        actually_killed_metadata.update(d2)
                else:
                    deleted_count += 1
                    logger.info(f"   [-] [Dry Run 查重洗板] 模拟清除同目录下冗余文件: {loser_basename}")

    if actually_killed_metadata:
        history = load_wash_history()
        now = time.time()
        for k in actually_killed_metadata:
            history[k] = now
        history = {k: v for k, v in history.items() if now - v < 604800}
        save_wash_history(history)

    if deleted_count > 0:
        trigger_cms("查重洗板执行完毕")

    return jsonify({"success": True, "deleted": deleted_count})

@app.route('/api/scan_empty_folders', methods=['GET'])
def api_scan_empty():
    empty_meta = []
    logger.info("🧽 [空间猎犬] 启动本地空壳目录扫描，正在进行极速预检...")
    total_folders = 0

    def count_target_folders(base_path):
        count = 0
        for root, dirs, files in os.walk(base_path):
            if os.path.basename(root).lower() in MOVIE_AUX_DIRS:
                continue
            dirs[:] = [d for d in dirs if d.lower() not in MOVIE_AUX_DIRS]
            if any(f.lower().endswith(('.strm', '.nfo')) for f in files):
                dirs.clear()
            count += 1
        return count

    try:
        for w in WATCHED_DIRS:
            meta_path = (METADATA_BASE_PATH.rstrip('/') + w.replace('/115open/', '/')).rstrip('/')
            if os.path.exists(meta_path):
                total_folders += count_target_folders(meta_path)
                
        if total_folders == 0:
            logger.info("🧽 [空间猎犬] 预检完毕，未发现任何本地监听目录。")
            return jsonify({"success": True, "empty_mount": [], "empty_meta": []})
            
        logger.info(f"🧽 [空间猎犬] 预检完成！共计 {total_folders} 个本地目录节点，正式开始查杀排雷...")
        scanned_count = 0
        for w in WATCHED_DIRS:
            meta_path = (METADATA_BASE_PATH.rstrip('/') + w.replace('/115open/', '/')).rstrip('/')
            if os.path.exists(meta_path):
                for root, dirs, files in os.walk(meta_path):
                    if os.path.basename(root).lower() in MOVIE_AUX_DIRS:
                        continue
                    dirs[:] = [d for d in dirs if d.lower() not in MOVIE_AUX_DIRS]
                    has_strm = any(f.lower().endswith('.strm') for f in files)
                    is_movie_dir = has_strm or any(f.lower().endswith('.nfo') for f in files)
                    if is_movie_dir:
                        dirs.clear()

                    scanned_count += 1
                    if scanned_count % 100 == 0 or scanned_count == total_folders:
                        percent = int((scanned_count / total_folders) * 100)
                        logger.info(f"🧽 [空间猎犬] 本地扫描进度: {percent}% ({scanned_count}/{total_folders}) | 累计揪出 {len(empty_meta)} 个空壳")

                    if not dirs and root != meta_path:
                        if not has_strm:
                            empty_meta.append(root)

        logger.info(f"🧽 [空间猎犬] 扫描圆满结束！100% 本地极速排查完毕。最终锁定 {len(empty_meta)} 个僵尸目录。")
        return jsonify({"success": True, "empty_mount": [], "empty_meta": empty_meta})
    except Exception as e:
        logger.error(f"🧽 [空间猎犬] 扫描过程遭遇异常: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/delete_empty_folders', methods=['POST'])
def api_delete_empty():
    data = request.get_json()
    folders = data.get('folders', [])
    deleted = 0
    failed = 0
    
    if DRY_RUN:
        logger.info(f"🧽 [Dry Run] 模拟清理了 {len(folders)} 个本地僵尸目录。")
        return jsonify({"success": True, "deleted": len(folders), "failed": 0})
        
    for f in folders:
        if safe_delete_dir(f):
            deleted += 1
        else:
            failed += 1
            
    if deleted > 0:
        logger.info(f"🧹 [空间清理] 成功斩杀了 {deleted} 个无STRM的本地空壳目录。")
        
    return jsonify({"success": True, "deleted": deleted, "failed": failed})

@app.route('/api/config', methods=['POST'])
def update_config():
    global DRY_RUN, DB_ONLINE_WEBHOOK, STRM_WEBHOOK, IGNORE_LEADING_DIGITS, config
    try:
        data = request.get_json()
        if not data: return jsonify({"success": False, "error": "提交数据无效"}), 400
        
        new_webhook = data.get('DB_ONLINE_WEBHOOK', '').strip()
        new_strm_webhook = data.get('STRM_WEBHOOK', '').strip()
        new_dry_run = bool(data.get('DRY_RUN', True))
        new_ignore_digits = bool(data.get('IGNORE_LEADING_DIGITS', False))
        
        success, res = save_config({
            "DB_ONLINE_WEBHOOK": new_webhook, 
            "STRM_WEBHOOK": new_strm_webhook,
            "DRY_RUN": new_dry_run,
            "IGNORE_LEADING_DIGITS": new_ignore_digits
        })
        
        if success:
            config = res
            DB_ONLINE_WEBHOOK = new_webhook
            STRM_WEBHOOK = new_strm_webhook
            DRY_RUN = new_dry_run
            IGNORE_LEADING_DIGITS = new_ignore_digits
            logger.info(f"⚙️ [热更新] 网页在线修改配置成功！DRY_RUN={DRY_RUN}, 忽略前置数字={IGNORE_LEADING_DIGITS}, STRM触发={STRM_WEBHOOK}")
            return jsonify({"success": True})
        else:
            return jsonify({"success": False, "error": str(res)}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/logs')
def get_logs():
    try:
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            return jsonify({"logs": lines[-100:]})
    except Exception as e:
        return jsonify({"logs": [f"无法读取日志文件: {str(e)}"]})

@app.route('/webhook', methods=['POST'])
def handle_cd2_webhook():
    try:
        payload = request.json
        if not payload or 'data' not in payload: return "OK", 200
        
        for item in payload['data']:
            if item.get('action') in ['create', 'rename'] and str(item.get('is_dir', 'false')).lower() == 'false':
                file_rel_path = item.get('destination_file') or item.get('source_file', '')
                
                if not any(file_rel_path.startswith(d) for d in WATCHED_DIRS): continue
                
                local_file_path = MOUNT_BASE_PATH.rstrip('/') + file_rel_path
                basename = os.path.basename(local_file_path)
                
                logger.info(f"🚀 [通关] 新增目标视频 [{basename}]，开始智能等待挂载同步...")
                
                waited = 0
                found = False
                while waited < 60:
                    if os.path.exists(local_file_path):
                        found = True
                        break
                    time.sleep(5)
                    waited += 5
                    
                if not found:
                    logger.warning(f"⚠️ [失败] 等待超时，本地未刷出该文件: {local_file_path}")
                    continue
                
                _, _, target_is_part = get_weight_and_fanhao(basename)
                if target_is_part:
                    logger.info(f"⏳ [分盘缓冲] 发现分盘文件 [{basename}]，延迟挂起 20 秒，等待同阵列其余分盘完全落盘...")
                    time.sleep(20)
                
                process_xiban(local_file_path)
                
        return "OK", 200
    except Exception as e:
        logger.error(f"处理 Webhook 报错: {str(e)}")
        return "Error", 500

@app.route('/emby_webhook', methods=['GET', 'POST'])
def handle_emby_webhook():
    if request.method == 'GET':
        return "✅ 9KGwash Webhook 中转网关 (v9.15.0) 运行正常！", 200
        
    try:
        payload = request.get_json(silent=True)
        if not payload: return "OK", 200
        
        event_type = payload.get('Event', '')
        item_type = payload.get('Item', {}).get('Type', '') 
        item_path = payload.get('Item', {}).get('Path', '')
        item_name = payload.get('Item', {}).get('Name', '')

        if event_type == 'library.deleted':
            if item_type not in ['Movie', 'Episode', 'Video']:
                logger.info(f"ℹ️ [忽略] Emby 报告删除了非影视对象 [{item_type}]: {item_name}，直接无视！")
                return "Ignored Non-Media", 200
            
            if item_path:
                time.sleep(2.5) 
                if os.path.exists(item_path):
                    logger.info(f"👻 [幽灵防御] 拦截虚假删除: {item_name}。物理路径依然存在，直接丢弃！")
                    return "Phantom Deletion Ignored", 200
            
            basename = os.path.splitext(os.path.basename(item_path))[0] if item_path else item_name
            canonical_fanhao, raw_fanhao, _ = parse_fanhao_info(basename)
            
            if not is_jav(canonical_fanhao):
                logger.info(f"⚠️ [放行] 欧美/非 JAV 影片 {basename} 被删，正常放行转发。")
            else:
                history = load_wash_history()
                now = time.time()
                
                keys_to_delete = [k for k, v in history.items() if now - v > 604800]
                if keys_to_delete:
                    for k in keys_to_delete: del history[k]
                    save_wash_history(history)

                if basename in history:
                    logger.info(f"🛡️ [精准拦截] JAV 影片 {basename} 被删。查阅档案确认由洗板网关物理清理，丢弃不转发。")
                    return "Intercepted", 200
                else:
                    search_fanhao = canonical_fanhao if IGNORE_LEADING_DIGITS else raw_fanhao
                    logger.info(f"🕵️ [补充防御] 档案未记录 [{basename}]，启动【本地极速扫描】确认是否为真实物理删除 (搜寻特征: {search_fanhao})...")
                    
                    folder_still_exists = False
                    for w in WATCHED_DIRS:
                        meta_path = (METADATA_BASE_PATH.rstrip('/') + w.replace('/115open/', '/')).rstrip('/')
                        if fast_check_folder_exists(meta_path, search_fanhao):
                            folder_still_exists = True
                            break
                    
                    if folder_still_exists:
                        logger.info(f"👻 [幽灵拦截] 发现【本地】依然存活包含 [{search_fanhao}] 的物理文件夹！判定为刮削/转移替换产生的虚假删除，直接丢弃通知！")
                        return "Phantom Deletion Ignored", 200
                    
                    logger.info(f"✅ [核实真删] 本地关联目录确已消失。确认为真实手动删除事件，予以放行转发。")
                    
                    if IGNORE_LEADING_DIGITS and raw_fanhao != canonical_fanhao:
                        if 'Item' in payload and isinstance(payload['Item'], dict):
                            if 'Name' in payload['Item'] and isinstance(payload['Item']['Name'], str):
                                payload['Item']['Name'] = payload['Item']['Name'].replace(raw_fanhao, canonical_fanhao)
                            if 'Path' in payload['Item'] and isinstance(payload['Item']['Path'], str):
                                payload['Item']['Path'] = payload['Item']['Path'].replace(raw_fanhao, canonical_fanhao)
                        logger.info(f"✂️ [通知降噪] 转发删除通知前，已剔除数据内数字前缀: {raw_fanhao} -> {canonical_fanhao}")
        
        if DB_ONLINE_WEBHOOK:
            logger.info(f"📤 [中转转发] 将 Emby 事件 [{event_type}] ({payload.get('Item', {}).get('Name', '')}) 转发至 DB online...")
            headers = {k: v for k, v in request.headers if k.lower() != 'host'}
            try:
                resp = requests.post(DB_ONLINE_WEBHOOK, json=payload, headers=headers, timeout=5)
                logger.info(f"   [反馈] DB online 返回状态码: {resp.status_code}")
            except Exception as e:
                logger.error(f"   [转发失败] {e}")

        return "Forwarded", 200
    except Exception as e:
        logger.error(f"处理 Emby Webhook 报错: {str(e)}")
        return "Error", 500

if __name__ == '__main__':
    logger.info("===================================================")
    logger.info("🌸 9KGwash 洗板网关启动 (v9.15.0 智能全端清退与一键复制版)")
    logger.info(f"📡 DB online 目标地址: {DB_ONLINE_WEBHOOK or '未配置'}")
    logger.info(f"📡 STRM 触发目标地址: {STRM_WEBHOOK or '未配置'}")
    logger.info(f"🛡️ Dry Run 模式: {'开启 (模拟)' if DRY_RUN else '关闭 (危险实弹)'}")
    logger.info(f"✂️ 忽略番号前置数字: {'开启 (跨番号通吃+去前缀转发)' if IGNORE_LEADING_DIGITS else '关闭 (精确匹配)'}")
    logger.info(f"🌐 WebUI 控制台地址: 见宿主机映射端口")
    logger.info("===================================================")
    app.run(host='0.0.0.0', port=5000)