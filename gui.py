import customtkinter as ctk
import sqlite3
import requests
import threading
import datetime
import re
from bs4 import BeautifulSoup
from urllib.parse import urljoin, unquote
import bencodepy


# =========================
# CONFIG
# =========================
DB_NAME = "downloads.db"
HEADERS = {"User-Agent": "Mozilla/5.0"}

# ====== QBIT CONFIG ======
QBIT_URL = "http://10.0.0.3:8080"
QBIT_USER = "admin"
QBIT_PASS = "111111"

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


# =========================
# DATABASE
# =========================
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS actors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        url TEXT UNIQUE,
        created_at TEXT
    )
    """)

    cursor.execute("""
CREATE TABLE IF NOT EXISTS qbit_cache (
    code TEXT PRIMARY KEY,
    torrent_size INTEGER
)
""")


    conn.commit()
    conn.close()


# =========================
# QBIT LOGIN
# =========================
def qbit_login():
    session = requests.Session()
    try:
        r = session.post(
            f"{QBIT_URL}/api/v2/auth/login",
            data={"username": QBIT_USER, "password": QBIT_PASS},
            timeout=10
        )
        if r.text == "Ok.":
            return session
        else:
            return None
    except:
        return None


# =========================
# EXTRACT ACTOR NAME
# =========================
def extract_actor_name(url):
    try:
        slug = url.rstrip("/").split("/")[-1]
        parts = slug.split("-")

        if parts[-1].isdigit():
            parts = parts[:-1]

        return " ".join(parts).title()
    except:
        return "Unknown"


# =========================
# LOAD ACTORS
# =========================
def load_actors():
    actor_box.delete("1.0", "end")

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM actors")
    rows = cursor.fetchall()
    conn.close()

    for row in rows:
        actor_box.insert("end", "• " + row[0] + "\n")


# =========================
# ADD ACTOR
# =========================
def add_actor():
    url = url_entry.get().strip()

    if not url:
        log("⚠ Nhập link diễn viên")
        return

    name = extract_actor_name(url)

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    try:
        cursor.execute("""
        INSERT INTO actors (name, url, created_at)
        VALUES (?, ?, ?)
        """, (name, url, datetime.datetime.now().isoformat()))
        conn.commit()
        log(f"✅ Đã thêm: {name}")
    except:
        log("⚠ Link đã tồn tại")

    conn.close()
    url_entry.delete(0, "end")
    load_actors()


# =========================
# LOG
# =========================
def log(text):
    log_box.insert("end", text + "\n")
    log_box.see("end")


# =========================
# CRAWL ALL ACTORS
# =========================
def start_crawl():
    threading.Thread(target=crawl_all, daemon=True).start()

def crawl_all():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT name, url FROM actors")
    actors = cursor.fetchall()

    for name, page_url in actors:
        log(f"🔎 Crawl {name}")

        try:
            r = requests.get(page_url, headers=HEADERS, timeout=15)
            soup = BeautifulSoup(r.text, "html.parser")

            for a in soup.find_all("a", href=True):
                if "/download/" in a["href"]:
                    full_url = urljoin(page_url, a["href"])
                    try:
                        cursor.execute("""
                        INSERT INTO downloads (url, created_at)
                        VALUES (?, ?)
                        """, (full_url, datetime.datetime.now().isoformat()))
                    except:
                        pass

            conn.commit()

        except Exception as e:
            log(f"❌ Lỗi crawl {name}: {e}")

    conn.close()
    log("✔ Crawl tất cả hoàn tất")


# =========================
# DOWNLOAD + PUSH TO QBIT
# =========================
def start_download():
    threading.Thread(target=download_all, daemon=True).start()

def extract_code(name):
    if not name:
        return None
    match = re.search(r'([A-Z]{2,10}-\d{2,6})', name.upper())
    return match.group(1) if match else None


def get_torrent_total_size(torrent_content):
    try:
        metadata = bencodepy.decode(torrent_content)
        info = metadata[b'info']

        if b'files' in info:
            # multi file torrent
            return sum(f[b'length'] for f in info[b'files'])
        else:
            # single file torrent
            return info[b'length']
    except Exception:
        return 0


def download_all():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("SELECT id, url FROM downloads WHERE status='new'")
    rows = cursor.fetchall()

    # ===== LOGIN QBIT =====
    qbit = requests.Session()
    try:
        login = qbit.post(
            f"{QBIT_URL}/api/v2/auth/login",
            data={"username": QBIT_USER, "password": QBIT_PASS},
            timeout=10
        )
        if login.text != "Ok.":
            log("❌ Sai user/pass qBit")
            conn.close()
            return
        else:
            log("✔ Đã kết nối qBittorrent")
    except Exception as e:
        log(f"❌ Không kết nối được qBit: {e}")
        conn.close()
        return

    for file_id, url in rows:
        try:
            log(f"⬇ Đang tải: {url}")

            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code != 200:
                log(f"❌ HTTP lỗi: {r.status_code}")
                continue

            filename = f"{file_id}.torrent"

            if "content-disposition" in r.headers:
                cd = r.headers["content-disposition"]
                if "filename*=" in cd:
                    filename = cd.split("filename*=")[-1]
                    filename = filename.split("''")[-1]
                    filename = unquote(filename)
                elif "filename=" in cd:
                    filename = cd.split("filename=")[-1].strip('"')

            filename = re.sub(r'[\\/*?:"<>|]', "_", filename)

            # ===== ADD TRƯỚC =====
            with open(filename, "wb") as f:
                f.write(r.content)

            with open(filename, "rb") as torrent_file:
                qbit.post(
                    f"{QBIT_URL}/api/v2/torrents/add",
                    files={"torrents": torrent_file}
                )

            log(f"🚀 Đã add: {filename}")

            # ===== CHỜ qBit UPDATE =====
            import time
            time.sleep(3)

            # ===== LẤY LIST SAU KHI ADD =====
            qbit_list = qbit.get(
                f"{QBIT_URL}/api/v2/torrents/info"
            ).json()

            # ===== GROUP THEO CODE =====
            grouped = {}

            for t in qbit_list:
                code = extract_code(t["name"])
                if code:
                    grouped.setdefault(code, []).append(t)

            # ===== GIỮ BẢN LỚN NHẤT =====
            for code, torrents in grouped.items():
                if len(torrents) <= 1:
                    continue

                # sort theo total_size giảm dần
                torrents_sorted = sorted(
                    torrents,
                    key=lambda x: x.get("total_size", 0),
                    reverse=True
                )

                # giữ cái đầu (lớn nhất)
                keep = torrents_sorted[0]

                # xoá phần còn lại
                hashes_to_delete = [
                    t["hash"] for t in torrents_sorted[1:]
                ]

                if hashes_to_delete:
                    log(f"🧹 {code}: Giữ {keep['name']} ({keep['total_size']})")

                    qbit.post(
                        f"{QBIT_URL}/api/v2/torrents/delete",
                        data={
                            "hashes": "|".join(hashes_to_delete),
                            "deleteFiles": "true"
                        }
                    )

            cursor.execute(
                "UPDATE downloads SET status='downloaded' WHERE id=?",
                (file_id,)
            )
            conn.commit()

        except Exception as e:
            log(f"❌ Lỗi: {e}")

    conn.close()
    log("🎉 Hoàn tất - chỉ giữ bản dung lượng lớn nhất")











# =========================
# GUI
# =========================
init_db()

root = ctk.CTk()
root.geometry("1000x650")
root.title("Ultimate Torrent Manager + qBit")

# Sidebar
sidebar = ctk.CTkFrame(root, width=300)
sidebar.pack(side="left", fill="y")

ctk.CTkLabel(sidebar, text="ACTRESSES", font=("Arial", 18, "bold")).pack(pady=15)

url_entry = ctk.CTkEntry(sidebar, placeholder_text="Nhập link diễn viên...")
url_entry.pack(pady=5)

ctk.CTkButton(sidebar, text="Thêm Diễn Viên", command=add_actor).pack(pady=5)

actor_box = ctk.CTkTextbox(sidebar, height=450)
actor_box.pack(pady=10, fill="y")

# Main area
main = ctk.CTkFrame(root)
main.pack(side="right", fill="both", expand=True)

top_bar = ctk.CTkFrame(main)
top_bar.pack(fill="x")

ctk.CTkButton(top_bar, text="🔎 Crawl Tất Cả", command=start_crawl).pack(side="left", padx=10, pady=10)
ctk.CTkButton(top_bar, text="⬇ Download + Push qBit", command=start_download).pack(side="left", padx=10)

log_box = ctk.CTkTextbox(main)
log_box.pack(fill="both", expand=True, padx=10, pady=10)

load_actors()

root.mainloop()
