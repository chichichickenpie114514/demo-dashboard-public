"""
In-memory cache backed by Google Cloud Storage.

Progressive warm-up: on boot we synchronously load only top-level JSONs
(months.json, trend_history.json) and the latest month's files — usually
sub-second. The remaining months populate in a background daemon thread,
newest -> oldest. Requests that hit an unwarmed month fall back to a
synchronous per-month load (protected by a per-month lock to prevent
thundering herd from concurrent requests).

Public API (unchanged for callers):
  .load()            — boot-time initialiser (now progressive)
  .load_from_local() — dev: eager-load every JSON under a local dir
  .get(key)          — lookup; synchronous fallback if month not cached yet
  .is_loaded         — True once boot load returned
  .warm_progress()   — observability
"""
import json
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor

GCS_BUCKET = os.environ.get('GCS_BUCKET', '')  # empty = local-only mode
TTL_SECONDS = int(os.environ.get('GCS_TTL', '86400'))  # 24 hour default (demo data is static)


def _is_month_prefix(s: str) -> bool:
    return (
        len(s) == 7
        and s[4] == '-'
        and s[:4].isdigit()
        and s[5:7].isdigit()
    )


def _month_of(key: str):
    """Return the YYYY-MM month-prefix of a cache key, or None if non-monthly."""
    if '/' not in key:
        return None
    prefix = key.split('/', 1)[0]
    return prefix if _is_month_prefix(prefix) else None


class GCSDataStore:
    def __init__(self, bucket_name=None):
        self._cache = {}
        self._loaded_months = set()
        self._top_loaded = False
        self._loaded_at = 0
        self._state_lock = threading.Lock()
        self._month_locks = {}
        self._warm_thread = None
        self._bucket_name = bucket_name or GCS_BUCKET
        self._bucket = None
        self._local_data_dir = None  # fallback path for TTL expiry reload

    # ── bucket helper ───────────────────────────────────────────────────────
    def _get_bucket(self):
        if self._bucket is None and self._bucket_name:
            from google.cloud import storage
            try:
                self._bucket = storage.Client().bucket(self._bucket_name)
            except Exception as e:
                print(f'[gcs] WARN: cannot connect to GCS: {e}')
                return None
        return self._bucket

    # ── public API ──────────────────────────────────────────────────────────
    def load(self):
        """Fast-boot: top-level + latest month sync; remaining months in background.
        Skips gracefully when GCS_BUCKET is not configured or credentials are missing."""
        if not self._bucket_name:
            print('[gcs] no GCS_BUCKET configured — GCS loading skipped (use local data/)')
            self._top_loaded = True
            self._loaded_at = time.time()
            return

        t0 = time.time()
        bucket = self._get_bucket()
        if bucket is None:
            print('[gcs] GCS bucket unavailable — GCS loading skipped (use local data/)')
            self._top_loaded = True
            self._loaded_at = time.time()
            return

        top_blobs = []
        per_month = {}
        for b in bucket.list_blobs():
            if not b.name.endswith('.json'):
                continue
            key = b.name[:-len('.json')]
            m = _month_of(key)
            if m is None:
                top_blobs.append(b)
            else:
                per_month.setdefault(m, []).append(b)

        months = sorted(per_month.keys())
        latest = months[-1] if months else None

        top_bytes = self._fetch_blobs(top_blobs)
        latest_bytes = 0
        if latest:
            latest_bytes = self._fetch_blobs(per_month[latest])
            self._loaded_months.add(latest)
        self._top_loaded = True
        self._loaded_at = time.time()
        print(
            f"[gcs] boot: top={len(top_blobs)}f/{top_bytes}B, "
            f"latest={latest} ({len(per_month.get(latest, []))}f/{latest_bytes}B) "
            f"in {time.time() - t0:.2f}s "
            f"(bucket=gs://{self._bucket_name}/)"
        )

        remaining = [m for m in reversed(months) if m != latest]
        if remaining:
            self._warm_thread = threading.Thread(
                target=self._warm_rest,
                args=(remaining, per_month),
                daemon=True,
                name='gcs-warm',
            )
            self._warm_thread.start()

    def load_from_local(self, data_dir):
        """Dev mode: eager-load every JSON under data_dir (tiny dataset)."""
        self._local_data_dir = data_dir  # remember for TTL expiry reload
        cache = {}
        months = set()
        for root, _dirs, files in os.walk(data_dir):
            for f in files:
                if not f.endswith('.json'):
                    continue
                fp = os.path.join(root, f)
                key = os.path.relpath(fp, data_dir).replace(os.sep, '/')[:-len('.json')]
                with open(fp, 'r', encoding='utf-8') as fh:
                    cache[key] = json.load(fh)
                m = _month_of(key)
                if m:
                    months.add(m)
        self._cache = cache
        self._loaded_months = months
        self._top_loaded = True
        self._loaded_at = time.time()
        print(f"[gcs] local: {len(cache)} files, {len(months)} months from {data_dir}")

    def get(self, key):
        """Return cached JSON. Synchronously loads the month if not warmed yet."""
        if self._loaded_at > 0 and (time.time() - self._loaded_at) > TTL_SECONDS:
            self._invalidate()
            if self._local_data_dir and os.path.isdir(self._local_data_dir):
                self.load_from_local(self._local_data_dir)
            else:
                self.load()

        if key in self._cache:
            return self._cache[key]

        month = _month_of(key)
        if month is None:
            return None

        lock = self._get_month_lock(month)
        with lock:
            if month not in self._loaded_months:
                t0 = time.time()
                bucket = self._get_bucket()
                blobs = [b for b in bucket.list_blobs(prefix=f'{month}/')
                         if b.name.endswith('.json')]
                n_bytes = self._fetch_blobs(blobs)
                self._loaded_months.add(month)
                print(f"[gcs] lazy {month}: {len(blobs)}f/{n_bytes}B in {time.time() - t0:.2f}s")
        return self._cache.get(key)

    # ── introspection ───────────────────────────────────────────────────────
    @property
    def is_loaded(self):
        return self._top_loaded

    def warm_progress(self):
        return {
            'top_loaded': self._top_loaded,
            'months_cached': sorted(self._loaded_months),
            'files_cached': len(self._cache),
        }

    # ── internals ───────────────────────────────────────────────────────────
    def _fetch_blobs(self, blobs):
        """Concurrently download blobs into self._cache. Returns total bytes."""
        if not blobs:
            return 0

        def fetch(blob):
            text = blob.download_as_text()
            key = blob.name[:-len('.json')]
            return key, json.loads(text), len(text)

        total = 0
        with ThreadPoolExecutor(max_workers=16) as pool:
            for key, data, n in pool.map(fetch, blobs):
                self._cache[key] = data
                total += n
        return total

    def _get_month_lock(self, month):
        lock = self._month_locks.get(month)
        if lock is None:
            with self._state_lock:
                lock = self._month_locks.get(month)
                if lock is None:
                    lock = threading.Lock()
                    self._month_locks[month] = lock
        return lock

    def _warm_rest(self, months, per_month):
        t0 = time.time()
        for m in months:
            if m in self._loaded_months:
                continue
            lock = self._get_month_lock(m)
            with lock:
                if m in self._loaded_months:
                    continue
                try:
                    n = self._fetch_blobs(per_month[m])
                    self._loaded_months.add(m)
                    print(f"[gcs] warm {m}: {len(per_month[m])}f/{n}B")
                except Exception as e:
                    print(f"[gcs] warm {m} failed: {e}")
        print(f"[gcs] warm complete in {time.time() - t0:.2f}s, "
              f"{len(self._loaded_months)} months cached")

    def _invalidate(self):
        self._cache = {}
        self._loaded_months = set()
        self._top_loaded = False
        self._loaded_at = 0
