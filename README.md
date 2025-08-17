# Python Website Cloner

Aplikasi untuk mengkloning halaman web dan semua asetnya ke disk lokal, mengubah semua link ke path lokal.

## Fitur

- Kloning halaman web lengkap termasuk HTML, CSS, JavaScript, gambar, dan aset lainnya
- Konversi semua URL menjadi path lokal
- Ekstraksi data URI base64 menjadi file terpisah
- Auto-scrolling untuk menangkap konten lazy load
- Crawling link tambahan untuk memastikan semua aset tertaut dengan benar

## Persyaratan

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

## Penggunaan

```bash
python main.py https://website-anda.com output_folder [opsi]
```

### Opsi

- `--full`: Tunggu hingga jaringan idle (lebih lama tapi lebih lengkap)
- `--timeout 60s`: Waktu total untuk proses kloning (dalam detik, menit, milidetik)
- `--no-headless`: Tampilkan browser saat crawling (untuk debugging)

## Struktur Kode

Kode diorganisir dalam beberapa modul:

- `main.py`: Titik masuk utama aplikasi
- `src/utils.py`: Fungsi-fungsi utilitas untuk path dan manipulasi file
- `src/rewriter.py`: Fungsi untuk mengubah link dalam HTML dan CSS
- `src/handlers.py`: Handler untuk request dan response HTTP
- `src/crawler.py`: Fungsi untuk crawling dan interaksi dengan halaman
- `src/cloner.py`: Fungsi utama untuk proses kloning

## Contoh Penggunaan

```bash
# Kloning sederhana
python main.py https://example.com output

# Kloning dengan waktu yang lebih lama untuk website besar
python main.py https://example.com output --timeout 3m

# Kloning dengan menunggu semua aset dimuat
python main.py https://example.com output --full

# Kloning dengan browser yang terlihat (untuk debug)
python main.py https://example.com output --no-headless
```
