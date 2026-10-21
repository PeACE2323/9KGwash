import os
import re
import time
import json
from flask import Flask, request

app = Flask(__name__)

# =====================================================================
# 从挂载的 config.json 文件读取配置
# =====================================================================
CONFIG_PATH = '/config/config.json'

def load_config():
    default_config = {
        "MOUNT_BASE_PATH": "/media",
        "WATCHED_DIRS": ["/Media"],
        "DRY_RUN": True
    }
    
    if not os.path.exists(CONFIG_PATH):
        print(f"⚠️ 找不到配置文件 {CONFIG_PATH}，将使用默认安全配置。")
        return default_config
        
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            user_config = json.load(f)
            print("✅ 成功加载外部配置文件。")
            return user_config
    except Exception as e:
        print(f"❌ 配置文件解析失败 ({e})，将使用默认安全配置。请检查 JSON 格式！")
        return default_config

# 加载配置到全局变量
config = load_config()
MOUNT_BASE_PATH = config.get("MOUNT_BASE_PATH", "/media")
WATCHED_DIRS = config.get("WATCHED_DIRS", [])
DRY_RUN = config.get("DRY_RUN", True)

# =====================================================================

def get_weight_and_fanhao(filename):
    name, ext = os.path.splitext(filename)
    match = re.match(r'^([a-zA-Z]+-\d+)(?:-([UCuc]+))?$', name)
    if not match: return -1, None

    fanhao = match.group(1).upper()
    suffix = (match.group(2) or "").upper()

    weight = 0
    if "U" in suffix and "C" in suffix: weight = 3
    elif "C" in suffix: weight = 2
    elif "U" in suffix: weight = 1

    return weight, fanhao

def process_xiban(dir_path, target_fanhao):
    if not os.path.isdir(dir_path):
        print(f"⚠️ 目录不存在或尚未同步: {dir_path}")
        return

    video_exts = ('.mp4', '.mkv', '.avi', '.ts', '.wmv', '.rmvb', '.iso')
    files_to_check = []

    for f in os.listdir(dir_path):
        if not f.lower().endswith(video_exts): continue
        weight, fanhao = get_weight_and_fanhao(f)
        if fanhao == target_fanhao:
            files_to_check.append({
                'name': f, 'path': os.path.join(dir_path, f), 'weight': weight
            })

    if len(files_to_check) <= 1: return 

    files_to_check.sort(key=lambda x: x['weight'], reverse=True)
    best_file = files_to_check[0]
    print(f"\n🎯 [{target_fanhao}] 触发洗板，保留最高权重: {best_file['name']}")

    for file_info in files_to_check[1:]:
        if file_info['weight'] < best_file['weight']:
            if DRY_RUN:
                print(f"  [-] [Dry Run 模拟] 准备删除: {file_info['name']}")
            else:
                print(f"  [-] 正在删除: {file_info['name']}")
                try:
                    os.remove(file_info['path']) 
                    print("      ✅ 删除成功")
                except Exception as e:
                    print(f"      ❌ 删除失败: {e}")
        elif file_info['weight'] == best_file['weight']:
            print(f"  [!] 重复版本: {file_info['name']}，跳过")

@app.route('/webhook', methods=['POST'])
def handle_cd2_webhook():
    payload = request.json
    if payload.get("event_category") != "file": return "Not a file event", 200

    for item in payload.get("data", []):
        action = item.get("action")
        is_dir = item.get("is_dir")
        source_file = item.get("source_file")

        if action in ["create", "rename"] and str(is_dir).lower() == "false" and source_file:
            # 目录白名单拦截
            is_in_watched_dir = False
            for allowed_dir in WATCHED_DIRS:
                check_dir = allowed_dir if allowed_dir.endswith('/') else allowed_dir + '/'
                if source_file.startswith(check_dir):
                    is_in_watched_dir = True
                    break
            
            if not is_in_watched_dir: continue

            full_path = os.path.join(MOUNT_BASE_PATH, source_file.lstrip('/'))
            dir_path = os.path.dirname(full_path)
            filename = os.path.basename(full_path)

            weight, fanhao = get_weight_and_fanhao(filename)
            if fanhao != -1:
                print(f"\n🚀 新增视频 [{filename}]，延迟3秒验证...")
                time.sleep(3) 
                process_xiban(dir_path, fanhao)

    return "OK", 200

if __name__ == '__main__':
    print("===================================================")
    print("🧹 CloudDrive2 自动洗板服务已启动 (配置分离版)")
    print(f"📂 容器内挂载点: {MOUNT_BASE_PATH}")
    print(f"🎯 云端监听目录: {WATCHED_DIRS}")
    print(f"🛡️ Dry Run 模式: {'开启' if DRY_RUN else '关闭 (危险)'}")
    print("===================================================")
    app.run(host='0.0.0.0', port=5000)
