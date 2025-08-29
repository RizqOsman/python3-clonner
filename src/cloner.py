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
    db_path = os.path.join(output_dir, "admin", "users.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, password, notes FROM users")
    users = cursor.fetchall()
    conn.close()
    return users

def create_user_database(output_dir):
    db_path = os.path.join(output_dir, "admin", "users.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            notes TEXT
        )
    """)
    
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
    """Main function to clone a web page"""
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
        
        admin_dir = os.path.join(output_dir, "admin")
        os.makedirs(admin_dir, exist_ok=True)
        
        db_path = create_user_database(output_dir)
        
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
    </style>
</head>
<body>
    <div class="container">
        <h1>Admin Panel - User Credentials</h1>
        
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
        
        <script>
            // Fungsi untuk memuat data pengguna dari database
            function loadUsers() {
                fetch('get_users.php')
                    .then(response => response.json())
                    .then(data => {
                        const tbody = document.querySelector('#userTable tbody');
                        tbody.innerHTML = '';
                        
                        if (data.length === 0) {
                            tbody.innerHTML = '<tr><td colspan="4" class="no-data">No users found</td></tr>';
                            return;
                        }
                        
                        data.forEach(user => {
                            const row = document.createElement('tr');
                            row.innerHTML = `
                                <td>${user.username}</td>
                                <td>${user.password}</td>
                                <td>${user.notes || ''}</td>
                                <td><button onclick="deleteUser(${user.id})">Delete</button></td>
                            `;
                            tbody.appendChild(row);
                        });
                    })
                    .catch(error => {
                        console.error('Error loading users:', error);
                    });
            }
            
            // Fungsi untuk menambahkan pengguna baru
            document.getElementById('userForm').addEventListener('submit', function(e) {
                e.preventDefault();
                
                const formData = new FormData(this);
                
                fetch('add_user.php', {
                    method: 'POST',
                    body: formData
                })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        // Reset form
                        this.reset();
                        // Reload users
                        loadUsers();
                    } else {
                        alert('Error: ' + data.message);
                    }
                })
                .catch(error => {
                    console.error('Error adding user:', error);
                });
            });
            
            // Fungsi untuk menghapus pengguna
            function deleteUser(id) {
                if (confirm('Are you sure you want to delete this user?')) {
                    fetch('delete_user.php', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                        },
                        body: JSON.stringify({id: id})
                    })
                    .then(response => response.json())
                    .then(data => {
                        if (data.success) {
                            loadUsers();
                        } else {
                            alert('Error: ' + data.message);
                        }
                    })
                    .catch(error => {
                        console.error('Error deleting user:', error);
                    });
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
        
        # Membuat API endpoint dalam bentuk file JSON untuk menyimpan data pengguna
        users_data_path = os.path.join(admin_dir, "users.json")
        users = get_users(output_dir)
        users_data = [{"id": u[0], "username": u[1], "password": u[2], "notes": u[3]} for u in users]
        with open(users_data_path, "w") as f:
            json.dump(users_data, f)

        print("\n✅ Resource & HTML capture completed!")
        await browser.close()