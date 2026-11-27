/*
 * Pilot Stats — Gamelog folder scanner.
 *
 * Chrome / Edge: pick the Gamelogs folder once (File System Access API). The
 * folder is remembered in IndexedDB; while this tab stays open the folder is
 * rescanned every minute and new or grown logs are uploaded, so an ongoing
 * session's numbers refresh about once a minute.
 *
 * Other browsers: "Scan a folder once" (<input webkitdirectory>) does the
 * same filter-and-upload pass a single time.
 *
 * Logs are read locally. Only files whose "Listener:" is one of the user's
 * own characters are sent, through the same upload endpoint the page form
 * uses (with Accept: application/json). The server re-checks ownership.
 *
 * Exposes window.pilotstatsScanner.uploadFiles(files) for multi-file drops.
 */
(function () {
    "use strict";

    const root = document.getElementById("pilotstats-scanner");
    if (!root) {
        return;
    }

    const RESCAN_MS = 60 * 1000;
    const LIST_LIMIT = 60;
    const HEADER_BYTES = 4096;
    const DB_NAME = "pilotstats-scanner";
    const DB_VERSION = 1;

    const uploadUrl = root.dataset.uploadUrl;
    const charactersUrl = root.dataset.charactersUrl;
    const recentDays = parseInt(root.dataset.recentDays || "7", 10);

    const el = {
        pick: root.querySelector('[data-scanner-action="pick"]'),
        resume: root.querySelector('[data-scanner-action="resume"]'),
        stop: root.querySelector('[data-scanner-action="stop"]'),
        onceInput: root.querySelector("[data-scanner-input]"),
        older: root.querySelector("[data-scanner-older]"),
        status: root.querySelector("[data-scanner-status]"),
        support: root.querySelector("[data-scanner-support]"),
        list: root.querySelector("[data-scanner-list]"),
    };

    // Results the server gives for a file that won't change by resending the
    // same bytes. A file with one of these is only sent again once it grows.
    const SETTLED = new Set([
        "created", "updated", "duplicate", "no_new_lines", "no_combat",
        "not_gamelog", "not_owned", "too_large", "other_account",
    ]);
    const OK = new Set(["created", "updated"]);
    const LABELS = {
        created: "uploaded",
        updated: "updated",
        duplicate: "already uploaded",
        no_new_lines: "no new lines",
        no_combat: "no combat lines",
        not_gamelog: "not a Gamelog",
        not_owned: "not your character",
        too_large: "too large",
        other_account: "uploaded by another account",
        invalid: "rejected",
        throttled: "waiting (rate limit)",
        error: "failed",
    };

    let ownedNames = null;
    let watchHandle = null;
    let timer = null;
    let scanning = false;

    // ---- IndexedDB (best effort: private windows may refuse it) -----------

    function openDb() {
        return new Promise((resolve) => {
            let request;
            try {
                request = indexedDB.open(DB_NAME, DB_VERSION);
            } catch (e) {
                resolve(null);
                return;
            }
            request.onupgradeneeded = () => {
                const db = request.result;
                if (!db.objectStoreNames.contains("handles")) {
                    db.createObjectStore("handles");
                }
                if (!db.objectStoreNames.contains("files")) {
                    db.createObjectStore("files");
                }
            };
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => resolve(null);
        });
    }

    const dbPromise = openDb();
    const memory = { handles: new Map(), files: new Map() };

    async function dbGet(store, key) {
        const db = await dbPromise;
        if (!db) {
            return memory[store].get(key);
        }
        return new Promise((resolve) => {
            try {
                const req = db.transaction(store).objectStore(store).get(key);
                req.onsuccess = () => resolve(req.result);
                req.onerror = () => resolve(undefined);
            } catch (e) {
                resolve(memory[store].get(key));
            }
        });
    }

    async function dbPut(store, key, value) {
        memory[store].set(key, value);
        const db = await dbPromise;
        if (!db) {
            return;
        }
        await new Promise((resolve) => {
            try {
                const tx = db.transaction(store, "readwrite");
                tx.objectStore(store).put(value, key);
                tx.oncomplete = resolve;
                tx.onerror = resolve;
                tx.onabort = resolve;
            } catch (e) {
                resolve();
            }
        });
    }

    // ---- UI helpers -------------------------------------------------------

    function setStatus(text) {
        el.status.textContent = text;
    }

    function addRow(name, code, detail, sessionUrl) {
        const li = document.createElement("li");
        const nameEl = document.createElement("span");
        nameEl.className = "pstats-scan-name";
        nameEl.textContent = name;
        nameEl.title = detail || name;
        const resultEl = document.createElement(sessionUrl ? "a" : "span");
        resultEl.className = "pstats-scan-result " +
            (OK.has(code) ? "is-ok" : (code === "error" || code === "invalid") ? "is-error" : "is-skip");
        resultEl.textContent = LABELS[code] || code;
        if (sessionUrl) {
            resultEl.href = sessionUrl;
        }
        li.append(nameEl, resultEl);
        el.list.prepend(li);
        while (el.list.children.length > LIST_LIMIT) {
            el.list.lastElementChild.remove();
        }
    }

    function csrfToken() {
        const input = document.querySelector("#pilotstats-upload-form [name=csrfmiddlewaretoken]");
        return input ? input.value : "";
    }

    // ---- Server calls -----------------------------------------------------

    async function loadOwnedNames() {
        if (ownedNames) {
            return ownedNames;
        }
        const response = await fetch(charactersUrl, {
            credentials: "same-origin",
            headers: { Accept: "application/json" },
        });
        if (!response.ok) {
            throw new Error("Couldn't load your characters (" + response.status + ").");
        }
        const data = await response.json();
        ownedNames = new Set(data.characters || []);
        return ownedNames;
    }

    function sleep(ms) {
        return new Promise((resolve) => setTimeout(resolve, ms));
    }

    async function sendFile(file) {
        for (let attempt = 0; attempt < 3; attempt++) {
            const body = new FormData();
            body.append("log_file", file, file.name);
            let response;
            try {
                response = await fetch(uploadUrl, {
                    method: "POST",
                    credentials: "same-origin",
                    headers: { Accept: "application/json", "X-CSRFToken": csrfToken() },
                    body,
                });
            } catch (e) {
                return { result: "error", message: String(e) };
            }
            if (response.status === 429) {
                const wait = parseInt(response.headers.get("Retry-After") || "30", 10);
                setStatus("Upload limit reached; waiting " + wait + " s before continuing…");
                await sleep(Math.min(Math.max(wait, 1), 90) * 1000);
                continue;
            }
            const type = response.headers.get("Content-Type") || "";
            if (!type.includes("application/json")) {
                // 403 (no upload permission) or a login redirect.
                return { result: "error", message: "Server answered " + response.status + "." };
            }
            return response.json();
        }
        return { result: "throttled", message: "Still rate limited; will retry next scan." };
    }

    // ---- Scanning ---------------------------------------------------------

    async function readHeader(file) {
        const text = await file.slice(0, HEADER_BYTES).text();
        const info = {};
        text.split(/\r?\n/).slice(0, 8).forEach((line) => {
            const trimmed = line.trim();
            if (trimmed.startsWith("Listener:")) {
                info.listener = trimmed.slice("Listener:".length).trim();
            } else if (trimmed.startsWith("Session Started:")) {
                info.started = trimmed.slice("Session Started:".length).trim();
            }
        });
        return info;
    }

    function fileKey(file, folderKey) {
        return (folderKey || "") + "/" + (file.webkitRelativePath || file.name);
    }

    /**
     * Upload the eligible files in *files*, one at a time.
     * options.recentOnly: skip files older than the recent-days window.
     * options.remember: keep per-file state so unchanged files are skipped.
     */
    async function processFiles(files, options) {
        const names = await loadOwnedNames();
        const cutoff = Date.now() - recentDays * 24 * 3600 * 1000;
        const counts = { sent: 0, ok: 0, skipped: 0 };

        const candidates = files
            .filter((file) => /\.txt$/i.test(file.name))
            .sort((a, b) => a.lastModified - b.lastModified);

        for (const file of candidates) {
            if (options.recentOnly && !el.older.checked && file.lastModified < cutoff) {
                counts.skipped++;
                continue;
            }
            const key = fileKey(file, options.folderKey);
            const seen = options.remember ? await dbGet("files", key) : undefined;
            if (seen && seen.size === file.size && SETTLED.has(seen.result)) {
                counts.skipped++;
                continue;
            }

            const header = await readHeader(file);
            if (!header.listener) {
                // Chat logs and other text files: never leave the machine.
                if (options.remember) {
                    await dbPut("files", key, { size: file.size, result: "not_gamelog" });
                }
                counts.skipped++;
                continue;
            }
            if (!names.has(header.listener)) {
                if (options.remember) {
                    await dbPut("files", key, { size: file.size, result: "not_owned" });
                }
                counts.skipped++;
                continue;
            }

            setStatus("Uploading " + file.name + "…");
            const data = await sendFile(file);
            counts.sent++;
            if (OK.has(data.result)) {
                counts.ok++;
            }
            addRow(file.name, data.result, data.message, data.session_url);
            if (options.remember && SETTLED.has(data.result)) {
                await dbPut("files", key, { size: file.size, result: data.result, at: Date.now() });
            }
        }
        return counts;
    }

    async function listFolder(handle) {
        const files = [];
        for await (const entry of handle.values()) {
            if (entry.kind === "file" && /\.txt$/i.test(entry.name)) {
                files.push(await entry.getFile());
            }
        }
        return files;
    }

    function summary(counts, when) {
        return (when ? when + ": " : "") +
            counts.ok + " uploaded or updated, " +
            (counts.sent - counts.ok) + " not stored, " +
            counts.skipped + " skipped.";
    }

    async function scanWatched() {
        if (scanning || !watchHandle) {
            return;
        }
        scanning = true;
        try {
            const files = await listFolder(watchHandle);
            const counts = await processFiles(files, {
                recentOnly: true, remember: true, folderKey: watchHandle.name,
            });
            const time = new Date().toLocaleTimeString();
            setStatus("Watching “" + watchHandle.name + "”. Last scan " + summary(counts, time) +
                " Next scan in about a minute; keep this tab open.");
        } catch (e) {
            setStatus("Scan stopped: " + e.message);
            stopWatching();
        } finally {
            scanning = false;
        }
    }

    async function startWatching(handle) {
        watchHandle = handle;
        el.pick.hidden = true;
        el.resume.hidden = true;
        el.stop.hidden = false;
        await scanWatched();
        if (watchHandle) {
            timer = setInterval(scanWatched, RESCAN_MS);
        }
    }

    function stopWatching() {
        if (timer) {
            clearInterval(timer);
        }
        timer = null;
        watchHandle = null;
        el.stop.hidden = true;
        if ("showDirectoryPicker" in window) {
            el.pick.hidden = false;
            showResume();
        }
    }

    async function showResume() {
        const saved = await dbGet("handles", "gamelogs");
        if (!saved || watchHandle) {
            return;
        }
        el.resume.textContent = "Resume scanning “" + saved.name + "”";
        el.resume.hidden = false;
    }

    // ---- Wiring -----------------------------------------------------------

    if ("showDirectoryPicker" in window) {
        el.pick.hidden = false;
        el.support.textContent =
            "This browser can keep watching the folder while this tab is open.";
        showResume();

        el.pick.addEventListener("click", async () => {
            let handle;
            try {
                handle = await window.showDirectoryPicker({
                    id: "pilotstats-gamelogs", mode: "read", startIn: "documents",
                });
            } catch (e) {
                return; // picker cancelled
            }
            await dbPut("handles", "gamelogs", handle);
            startWatching(handle);
        });

        el.resume.addEventListener("click", async () => {
            const handle = await dbGet("handles", "gamelogs");
            if (!handle) {
                el.resume.hidden = true;
                return;
            }
            let permission = "denied";
            try {
                permission = await handle.requestPermission({ mode: "read" });
            } catch (e) {
                permission = "denied";
            }
            if (permission === "granted") {
                startWatching(handle);
            } else {
                setStatus("The browser didn't allow reading that folder. Choose it again.");
            }
        });

        el.stop.addEventListener("click", () => {
            stopWatching();
            setStatus("Stopped. Nothing is read from your folder until you start again.");
        });
    } else {
        el.support.textContent =
            "This browser can scan a folder once, but can't keep watching it. " +
            "Chrome or Edge can keep it updated while the tab is open.";
    }

    el.onceInput.addEventListener("change", async () => {
        const files = Array.from(el.onceInput.files || []);
        el.onceInput.value = "";
        if (!files.length) {
            return;
        }
        try {
            const counts = await processFiles(files, { recentOnly: true, remember: true, folderKey: "once" });
            setStatus("Folder scanned: " + summary(counts));
        } catch (e) {
            setStatus("Scan failed: " + e.message);
        }
    });

    window.pilotstatsScanner = {
        /** Upload every dropped Gamelog in turn (no age filter: the user picked them). */
        async uploadFiles(fileList) {
            const files = Array.from(fileList || []);
            try {
                const counts = await processFiles(files, { recentOnly: false, remember: false });
                setStatus("Dropped files: " + summary(counts));
                return counts;
            } catch (e) {
                setStatus("Upload failed: " + e.message);
                return null;
            }
        },
    };
})();
