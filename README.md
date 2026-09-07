# Spotify Playlist Sorter

Sort tracks in your own Spotify playlists by **artist**, **album**, **title**, or **date added** — from the terminal.

## Features

- Interactive playlist picker
- Sort keys: `artist`, `album`, `title`, `added_at`
- `--dry-run` preview before writing
- OAuth (PKCE) with local browser login
- Token cache so you don’t re-authorize every run

## Requirements

- Python 3.9+
- A [Spotify Developer](https://developer.spotify.com/dashboard) app

## Setup

### 1. Spotify app

1. Create an app in the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard).
2. Under **Redirect URIs**, add exactly:

   ```text
   http://127.0.0.1:8888/callback
   ```

3. Copy the **Client ID** and **Client Secret**.

### 2. Project

```bash
git clone https://github.com/yappydoor/spotify-playlist-sorter.git
cd spotify-playlist-sorter

python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env
# edit .env and paste your Client ID / Secret
```

## Usage

Preview (does **not** change the playlist):

```bash
./run.sh --dry-run
# or:
python sort_playlist.py --dry-run
```

Apply sorting:

```bash
./run.sh
```

Confirm with `y` when asked.

### Options

| Flag | Description |
|------|-------------|
| `--by artist\|album\|title\|added_at` | Sort key (skips the menu) |
| `--playlist <id-or-url>` | Target playlist directly |
| `--desc` | Descending order |
| `--dry-run` | Preview only |

Examples:

```bash
./run.sh --by artist --dry-run
./run.sh --by album --playlist https://open.spotify.com/playlist/XXXXXXXX
```

## Notes

- Prefer sorting playlists **you own**. Collaborative / followed playlists may reject reorders.
- Local files are skipped.
- Secrets stay in `.env` and `.token_cache.json` (both gitignored).

## License

MIT
