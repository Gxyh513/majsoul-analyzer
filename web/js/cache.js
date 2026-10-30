// Lightweight Pure Native IndexedDB Cache for Offline Review Instant Load
const idbReviewCache = {
  _db: null,
  async getDb() {
    if (this._db) return this._db;
    return new Promise((resolve) => {
      if (!window.indexedDB) {
        resolve(null);
        return;
      }
      try {
        const req = indexedDB.open("MajsoulAnalyzerDB", 1);
        req.onupgradeneeded = (e) => {
          const db = req.result;
          if (!db.objectStoreNames.contains("reviews")) {
            const store = db.createObjectStore("reviews", { keyPath: "key" });
            store.createIndex("timestamp", "timestamp", { unique: false });
          }
        };
        req.onsuccess = () => {
          this._db = req.result;
          resolve(this._db);
        };
        req.onerror = (e) => {
          console.warn("IndexedDB open error:", e);
          resolve(null);
        };
      } catch (err) {
        console.warn("IndexedDB init failed:", err);
        resolve(null);
      }
    });
  },
  async get(key) {
    try {
      const db = await this.getDb();
      if (!db) return null;
      return new Promise((resolve) => {
        const tx = db.transaction("reviews", "readonly");
        const store = tx.objectStore("reviews");
        const req = store.get(key);
        req.onsuccess = () => resolve(req.result ? req.result.data : null);
        req.onerror = () => resolve(null);
      });
    } catch (e) {
      console.warn("IndexedDB get error:", e);
      return null;
    }
  },
  async set(key, data) {
    try {
      const db = await this.getDb();
      if (!db) return;
      return new Promise((resolve) => {
        const tx = db.transaction("reviews", "readwrite");
        const store = tx.objectStore("reviews");
        store.put({ key, data, timestamp: Date.now() });
        tx.oncomplete = () => {
          this.prune();
          resolve();
        };
        tx.onerror = () => resolve();
      });
    } catch (e) {
      console.warn("IndexedDB set error:", e);
    }
  },
  async findByGame(gameId, engine, seat = null) {
    if (seat !== null && seat !== undefined) {
      const exact = await this.get(`${gameId}_${seat}_${engine}`);
      if (exact) return exact;
      return await this.get(`${gameId}_${engine}_${seat}`);
    }
    const defaultSeat = await this.get(`${gameId}_0_${engine}`);
    if (defaultSeat) return defaultSeat;

    try {
      const db = await this.getDb();
      if (!db) return null;
      return new Promise((resolve) => {
        const tx = db.transaction("reviews", "readonly");
        const store = tx.objectStore("reviews");
        const req = store.openCursor();
        req.onsuccess = (e) => {
          const cursor = e.target.result;
          if (cursor) {
            const k = String(cursor.key);
            if (k.startsWith(`${gameId}_`) && (k.endsWith(`_${engine}`) || k.includes(`_${engine}_`))) {
              resolve(cursor.value.data);
              return;
            }
            cursor.continue();
          } else {
            resolve(null);
          }
        };
        req.onerror = () => resolve(null);
      });
    } catch (_) {
      return null;
    }
  },
  async prune(maxRecords = 20) {
    try {
      const db = await this.getDb();
      if (!db) return;
      const tx = db.transaction("reviews", "readwrite");
      const store = tx.objectStore("reviews");
      const idx = store.index("timestamp");
      const req = idx.getAllKeys();
      req.onsuccess = () => {
        const keys = req.result;
        if (keys && keys.length > maxRecords) {
          const countToDelete = keys.length - maxRecords;
          for (let i = 0; i < countToDelete; i++) {
            store.delete(keys[i]);
          }
        }
      };
    } catch (e) {
      console.warn("IndexedDB prune error:", e);
    }
  }
};
