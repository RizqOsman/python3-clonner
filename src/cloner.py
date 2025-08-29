import os
import time
import asyncio
import sqlite3
from urllib.parse import urlparse
from playwright.async_api import async_playwright

from .utils import mkdir, extract_and_replace_data_uri
from .handlers import create_response_handler, handle_request
from .crawler import auto_scroll_lazy, crawl_additional_links
from .rewriter import rewrite_html_links
import json

def get_users(output_dir):
    """Mengambil semua pengguna dari database"""
    db_path = os.path.join(output_dir, "admin", "users.db")
    # Jika database belum ada, kembalikan daftar kosong
    if not os.path.exists(db_path):
        return []
        
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, password, notes FROM users")
        users = cursor.fetchall()
        conn.close()
        return users
    except sqlite3.Error as e:
        print(f"Error accessing database: {e}")
        return []

def create_user_database(output_dir):
    """Membuat database SQLite untuk menyimpan data pengguna"""
    db_path = os.path.join(output_dir, "admin", "users.db")
    # Membuat direktori admin jika belum ada
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Membuat tabel pengguna
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            notes TEXT
        )
    """)
    
    # Menambahkan pengguna default jika tabel kosong
    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] == 0:
        cursor.execute("""
            INSERT INTO users (username, password, notes) VALUES 
            ('admin', 'admin123', 'Default admin account'),
            ('user', 'user123', 'Default user account')
        """)
    
    conn.commit()
    conn.close()
    return db_path

async def clone_page(url: str, output_dir: str, full_load: bool, total_timeout_ms: int, headless: bool, crawl_internal=False):
    mkdir(output_dir)
    start_time = time.time()
    end_time = start_time + (total_timeout_ms / 1000)

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=headless, channel="chrome", args=["--no-sandbox"])
        except Exception:
            print("⚠️ Chrome not found, using Chromium instead")
            browser = await pw.chromium.launch(headless=headless, args=["--no-sandbox"])
        
        page = await browser.new_page()

        handle_response = await create_response_handler(page, output_dir)
        page.on("response", handle_response)

        print(f"⏱ Total capture time: {total_timeout_ms} ms ({total_timeout_ms/1000:.0f} seconds)")
        print(f"🌐 Opening {url}...")

        wait_mode = "networkidle" if full_load else "domcontentloaded"
        await page.route("**/*", handle_request)
        await page.goto(url, wait_until=wait_mode, timeout=0)
        await auto_scroll_lazy(page)
        
        if crawl_internal:
            print("🔍 Searching and downloading additional links...")
            await crawl_additional_links(page, url, output_dir)
        else:
            print("🚫 Internal link crawling disabled")

        remaining_time = end_time - time.time()
        if remaining_time > 0:
            print(f"⏱ Waiting {int(remaining_time)} seconds to capture additional resources...")
            while time.time() < end_time:
                await asyncio.sleep(1)

        parsed_url = urlparse(url)
        domain_dir = os.path.join(output_dir, parsed_url.netloc)
        html_path = os.path.join(domain_dir, "index.html")
        os.makedirs(domain_dir, exist_ok=True)

        html_content = await page.content()
        embedded_dir = os.path.join(domain_dir, "assets", "html", "embedded")
        html_content = extract_and_replace_data_uri(
            html_content,
            embedded_dir,
            "html_embedded"  
        )
        
        
        html_content = rewrite_html_links(html_content, url, domain_dir)

        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        print(f"📄 HTML saved: {html_path}")
        
        admin_dir = os.path.join(domain_dir, "admin")
        os.makedirs(admin_dir, exist_ok=True)
        
        db_path = create_user_database(output_dir)
        
        # Membuat API sederhana untuk mengelola pengguna
        api_script = '''#!/usr/bin/env python3
import json
import sqlite3
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

# Menentukan path database
DB_PATH = os.path.join(os.path.dirname(__file__), "users.db")

def get_users():
    """Mengambil semua pengguna dari database"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, password, notes FROM users")
    users = cursor.fetchall()
    conn.close()
    return [{"id": u[0], "username": u[1], "password": u[2], "notes": u[3]} for u in users]

def add_user(username, password, notes):
    """Menambahkan pengguna baru ke database"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO users (username, password, notes) VALUES (?, ?, ?)", 
                      (username, password, notes))
        conn.commit()
        success = True
        message = "User added successfully"
    except sqlite3.IntegrityError:
        success = False
        message = "Username already exists"
    except Exception as e:
        success = False
        message = str(e)
    finally:
        conn.close()
    return {"success": success, "message": message}

def delete_user(user_id):
    """Menghapus pengguna dari database"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
        success = True
        message = "User deleted successfully"
    except Exception as e:
        success = False
        message = str(e)
    finally:
        conn.close()
    return {"success": success, "message": message}

class APIHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/users":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            users = get_users()
            self.wfile.write(json.dumps(users).encode())
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")

    def do_POST(self):
        if self.path == "/api/users":
            content_length = int(self.headers["Content-Length"])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode())
            
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            
            result = add_user(data.get("username"), data.get("password"), data.get("notes", ""))
            self.wfile.write(json.dumps(result).encode())
        elif self.path == "/api/users/delete":
            content_length = int(self.headers["Content-Length"])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode())
            
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            
            result = delete_user(data.get("id"))
            self.wfile.write(json.dumps(result).encode())
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

if __name__ == "__main__":
    if len(sys.argv) > 1:
        port = int(sys.argv[1])
    else:
        port = 8001
        
    server = HTTPServer(("localhost", port), APIHandler)
    print(f"Starting API server on port {port}...")
    print(f"API server running on http://localhost:{port}")
    print("Press Ctrl+C to stop the server")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\\nShutting down API server...")
        server.shutdown()
'''
        
        api_path = os.path.join(admin_dir, "api.py")
        with open(api_path, "w", encoding="utf-8") as f:
            f.write(api_script)
        print(f"📄 API script saved: {api_path}")
        
        # Membuat halaman admin dengan akses ke database
        admin_html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Admin Panel</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f5f5f5;
        }
        .container {
            background-color: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        h1 {
            color: #333;
            text-align: center;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 20px;
        }
        th, td {
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid #ddd;
        }
        th {
            background-color: #4CAF50;
            color: white;
        }
        tr:hover {
            background-color: #f5f5f5;
        }
        .no-data {
            text-align: center;
            color: #666;
            font-style: italic;
            padding: 20px;
        }
        form {
            margin: 20px 0;
            padding: 15px;
            background-color: #e9f7ef;
            border-radius: 5px;
        }
        input, textarea {
            width: 100%;
            padding: 8px;
            margin: 5px 0 10px 0;
            box-sizing: border-box;
            border: 1px solid #ccc;
            border-radius: 4px;
        }
        button {
            background-color: #4CAF50;
            color: white;
            padding: 10px 15px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
        }
        button:hover {
            background-color: #45a049;
        }
        .message {
            padding: 10px;
            margin: 10px 0;
            border-radius: 4px;
        }
        .error {
            background-color: #ffebee;
            color: #c62828;
            border: 1px solid #ffcdd2;
        }
        .success {
            background-color: #e8f5e9;
            color: #2e7d32;
            border: 1px solid #c8e6c9;
        }
        .api-info {
            background-color: #fff3e0;
            border: 1px solid #ffe0b2;
            padding: 15px;
            border-radius: 4px;
            margin: 20px 0;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>Admin Panel - User Credentials</h1>
        
        <div class="api-info">
            <p><strong>Info:</strong> Untuk fungsionalitas lengkap (tambah/hapus pengguna), jalankan API server terpisah:</p>
            <p><code>cd admin && python api.py</code></p>
            <p>Lalu refresh halaman ini.</p>
        </div>
        
        <div id="message"></div>
        
        <form id="userForm">
            <h2>Add New User</h2>
            <label for="username">Username:</label>
            <input type="text" id="username" name="username" required>
            
            <label for="password">Password:</label>
            <input type="password" id="password" name="password" required>
            
            <label for="notes">Notes:</label>
            <textarea id="notes" name="notes" rows="2"></textarea>
            
            <button type="submit">Add User</button>
        </form>
        
        <table id="userTable">
            <thead>
                <tr>
                    <th>Username</th>
                    <th>Password</th>
                    <th>Notes</th>
                    <th>Action</th>
                </tr>
            </thead>
            <tbody>
                <!-- Data akan dimuat melalui JavaScript -->
            </tbody>
        </table>
        
        <!-- Memuat file JavaScript API -->
        <script src="api.js"></script>
        <script>
            // Menampilkan pesan
            function showMessage(text, isError = false) {
                const messageDiv = document.getElementById('message');
                messageDiv.textContent = text;
                messageDiv.className = 'message ' + (isError ? 'error' : 'success');
                setTimeout(() => {
                    messageDiv.textContent = '';
                    messageDiv.className = '';
                }, 3000);
            }
            
            // Fungsi untuk memuat data pengguna dari API
            async function loadUsers() {
                try {
                    // Mencoba menggunakan API server terlebih dahulu
                    const response = await fetch('http://localhost:8001/api/users');
                    if (response.ok) {
                        // Jika API server tersedia, gunakan itu
                        const data = await response.json();
                        renderUsers(data);
                    } else {
                        // Jika tidak, gunakan file JSON langsung
                        const users = await UserAPI.getUsers();
                        renderUsers(users);
                    }
                } catch (error) {
                    // Jika terjadi error (misalnya CORS), gunakan file JSON langsung
                    try {
                        const users = await UserAPI.getUsers();
                        renderUsers(users);
                    } catch (innerError) {
                        console.error('Error loading users:', innerError);
                        showMessage('Error loading users: ' + innerError.message, true);
                    }
                }
            }
            
            // Fungsi untuk merender data pengguna ke tabel
            function renderUsers(users) {
                const tbody = document.querySelector('#userTable tbody');
                tbody.innerHTML = '';
                
                if (users.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="4" class="no-data">No users found</td></tr>';
                    return;
                }
                
                users.forEach(user => {
                    const row = document.createElement('tr');
                    row.innerHTML = `
                        <td>${user.username}</td>
                        <td>${user.password}</td>
                        <td>${user.notes || ''}</td>
                        <td><button onclick="deleteUser(${user.id})">Delete</button></td>
                    `;
                    tbody.appendChild(row);
                });
            }
            
            // Fungsi untuk menambahkan pengguna baru
            document.getElementById('userForm').addEventListener('submit', async function(e) {
                e.preventDefault();
                
                const formData = new FormData(this);
                const user = {
                    username: formData.get('username'),
                    password: formData.get('password'),
                    notes: formData.get('notes') || ''
                };
                
                try {
                    // Mencoba menggunakan API server terlebih dahulu
                    const response = await fetch('http://localhost:8001/api/users', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                        },
                        body: JSON.stringify(user)
                    });
                    
                    if (response.ok) {
                        // Jika API server tersedia, gunakan itu
                        const result = await response.json();
                        if (result.success) {
                            this.reset();
                            showMessage('User added successfully');
                            loadUsers(); // Refresh the user list
                        } else {
                            showMessage('Error: ' + result.message, true);
                        }
                    } else {
                        // Jika tidak, gunakan implementasi browser-only
                        await UserAPI.addUser(user);
                        loadUsers(); // Refresh the user list
                    }
                } catch (error) {
                    // Jika terjadi error (misalnya CORS), gunakan implementasi browser-only
                    await UserAPI.addUser(user);
                    loadUsers(); // Refresh the user list
                }
            });
            
            // Fungsi untuk menghapus pengguna
            async function deleteUser(id) {
                if (!confirm('Are you sure you want to delete this user?')) return;
                
                try {
                    // Mencoba menggunakan API server terlebih dahulu
                    const response = await fetch('http://localhost:8001/api/users/delete', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                        },
                        body: JSON.stringify({id: id})
                    });
                    
                    if (response.ok) {
                        // Jika API server tersedia, gunakan itu
                        const result = await response.json();
                        if (result.success) {
                            showMessage('User deleted successfully');
                            loadUsers(); // Refresh the user list
                        } else {
                            showMessage('Error: ' + result.message, true);
                        }
                    } else {
                        // Jika tidak, gunakan implementasi browser-only
                        await UserAPI.deleteUser(id);
                        loadUsers(); // Refresh the user list
                    }
                } catch (error) {
                    // Jika terjadi error (misalnya CORS), gunakan implementasi browser-only
                    await UserAPI.deleteUser(id);
                    loadUsers(); // Refresh the user list
                }
            }
            
            // Muat pengguna saat halaman dimuat
            document.addEventListener('DOMContentLoaded', function() {
                loadUsers();
            });
        </script>
    </div>
</body>
</html>"""
        
        admin_path = os.path.join(admin_dir, "index.html")
        with open(admin_path, "w", encoding="utf-8") as f:
            f.write(admin_html)
        print(f"📄 Admin panel saved: {admin_path}")
        
        # Membuat API endpoint dalam bentuk file JSON untuk menyimpan data pengguna (fallback)
        users_data_path = os.path.join(admin_dir, "users.json")
        users = get_users(output_dir)
        users_data = [{"id": u[0], "username": u[1], "password": u[2], "notes": u[3]} for u in users]
        with open(users_data_path, "w") as f:
            json.dump(users_data, f)
            
        # Membuat file JavaScript untuk menangani operasi CRUD langsung dari browser
        js_api_content = '''// File API untuk menangani operasi CRUD terhadap users.json
// Fungsi untuk membaca data pengguna
async function getUsers() {
    try {
        const response = await fetch('users.json');
        if (!response.ok) {
            throw new Error('Gagal memuat data pengguna');
        }
        return await response.json();
    } catch (error) {
        console.error('Error:', error);
        return [];
    }
}

// Fungsi untuk menambahkan pengguna baru
async function addUser(userData) {
    try {
        // Dalam implementasi browser-only, kita hanya bisa menampilkan pesan
        // bahwa fitur ini memerlukan server backend
        alert('Fitur penambahan pengguna memerlukan server backend.\\n' +
              'Silakan jalankan API server terpisah dengan perintah:\\n' +
              'cd admin && python api.py');
        return { success: false, message: 'Fitur memerlukan server backend' };
    } catch (error) {
        console.error('Error:', error);
        return { success: false, message: error.message };
    }
}

// Fungsi untuk menghapus pengguna
async function deleteUser(userId) {
    try {
        // Dalam implementasi browser-only, kita hanya bisa menampilkan pesan
        // bahwa fitur ini memerlukan server backend
        alert('Fitur penghapusan pengguna memerlukan server backend.\\n' +
              'Silakan jalankan API server terpisah dengan perintah:\\n' +
              'cd admin && python api.py');
        return { success: false, message: 'Fitur memerlukan server backend' };
    } catch (error) {
        console.error('Error:', error);
        return { success: false, message: error.message };
    }
}

// Mengekspor fungsi-fungsi agar bisa digunakan di halaman admin
window.UserAPI = {
    getUsers,
    addUser,
    deleteUser
};'''
        
        js_api_path = os.path.join(admin_dir, "api.js")
        with open(js_api_path, "w", encoding="utf-8") as f:
            f.write(js_api_content)
        print(f"📄 API JS saved: {js_api_path}")
        
        # Membuat file package.json untuk memungkinkan npm start
        package_json_content = {
            "name": "cloned-website",
            "version": "1.0.0",
            "description": "Cloned website with admin panel",
            "scripts": {
                "start": "concurrently \"python api.py 8001\" \"python -m http.server 8000\"",
                "frontend": "python -m http.server 8000",
                "backend": "python api.py 8001"
            },
            "dependencies": {},
            "devDependencies": {
                "concurrently": "^7.0.0"
            }
        }
        
        package_json_path = os.path.join(domain_dir, "package.json")
        with open(package_json_path, "w") as f:
            json.dump(package_json_content, f, indent=2)
        print(f"📄 package.json saved: {package_json_path}")
        
        # Membuat file README dengan instruksi penggunaan
        readme_content = f"""# Cloned Website

Website ini telah dikloning menggunakan python3-clonner.

## Cara Menjalankan

### Metode 1: Menggunakan NPM (Disarankan)

Pastikan Anda telah menginstal Node.js dan npm.

Untuk menjalankan situs dengan API server (full functionality):
```bash
npm install
npm start
```

Ini akan menjalankan dua server:
- API server di http://localhost:8001
- Web server di http://localhost:8000

Admin panel dapat diakses di: http://localhost:8000/admin/

### Metode 2: Menggunakan Python saja

Untuk menjalankan hanya web server (tanpa API):
```bash
python -m http.server 8000
```

Akses situs di: http://localhost:8000

Untuk menjalankan API server (untuk CRUD functionality):
```bash
cd admin
python api.py
```

## Struktur Direktori

- `/` - File utama website
- `/admin/` - Admin panel dan database
- `/assets/` - CSS, JS, gambar, dan aset lainnya
"""

        readme_path = os.path.join(domain_dir, "README.md")
        with open(readme_path, "w", encoding="utf-8") as f:
            f.write(readme_content)
        print(f"📄 README.md saved: {readme_path}")
        
        # Membuat bash script untuk menjalankan web yang sudah di-clone
        bash_script_content = '''#!/bin/bash

# Script untuk menjalankan website yang sudah di-clone
# Memeriksa apakah Node.js terinstall
if command -v node &> /dev/null
then
    echo "Node.js terdeteksi, menggunakan npm start..."
    npm install
    npm start
else
    echo "Node.js tidak ditemukan, menggunakan Python server..."
    
    # Membuat direktori untuk log file
    LOG_DIR="logs"
    mkdir -p "$LOG_DIR"
    
    # Menjalankan API server di background
    echo "Menjalankan API server di port 8001..."
    cd admin
    python api.py 8001 > "../$LOG_DIR/api.log" 2>&1 &
    API_PID=$!
    cd ..
    
    # Memberi waktu untuk API server mulai
    sleep 2
    
    # Menjalankan web server
    echo "Menjalankan web server di port 8000..."
    echo "Website dapat diakses di: http://localhost:8000"
    echo "Admin panel di: http://localhost:8000/admin"
    echo "Tekan Ctrl+C untuk menghentikan server"
    
    # Menangani interrupt signal untuk menghentikan proses background
    trap "kill $API_PID 2> /dev/null; exit" INT TERM
    
    python -m http.server 8000
    
    # Menghentikan API server ketika web server dihentikan
    kill $API_PID 2> /dev/null
fi
'''

        bash_script_path = os.path.join(domain_dir, "run.sh")
        with open(bash_script_path, "w", encoding="utf-8") as f:
            f.write(bash_script_content)
        
        # Membuat file executable
        os.chmod(bash_script_path, 0o755)
        print(f"📄 Bash script saved: {bash_script_path}")

        print("\n✅ Resource & HTML capture completed!")
        await browser.close()