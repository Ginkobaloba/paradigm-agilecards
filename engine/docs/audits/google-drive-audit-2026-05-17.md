# Google Drive Audit
**Date:** 2026-05-18 (filename dated 2026-05-17 per request)
**Owner:** dramattick1@gmail.com
**Scope:** My Drive (root `0AMhzN25U-2tyUk9PVA`)
**Method:** MCP read-only scan via `list_recent_files` and `search_files`. No deletions, no moves. This document is a recommendation set for Drew to execute.

## Executive Summary

The Drive is doing too many jobs at once. It's serving as:
1. A real document repository (taxes, legal, school, career, patent work).
2. A `My Computer` Drive Desktop sync target that's spilling Desktop, Downloads, and Documents contents straight into Drive.
3. A backup of an old phone camera roll and Snapchat screenshots from late 2024.
4. An unintentional sync of game data (GTAV Enhanced, Rockstar Social Club) and Chromium-style cache directories.
5. A scratch space for Claude scheduled tasks that have created near-duplicate "case-watcher" folders.

**Biggest single wins** (by storage reclaimed once Drew acts):

| Item | Size | Why |
|---|---|---|
| Two `sd-blob*.img` files (Jetson Nano SD card images) | ~13.8 GB each, ~27.6 GB total | Likely duplicate of each other. Should live on cold storage, not Drive. |
| `Windows.iso` in Downloads | 8.45 GB | Microsoft media. Re-downloadable in 10 minutes. |
| GTAV Enhanced / Social Club cache trees | Unknown, but folder count is huge | Game cache being synced by accident. Pure junk. |
| Sept 2024 screenshot dump (folder `1ZTCqas...`) | 100+ files at ~500KB avg | Old phone camera roll. Backup elsewhere if sentimental, then clear. |

Stop Drive Desktop from syncing `Downloads` and game-app data directories. That single config change will prevent 80% of the future mess.

---

## Section 1: Critical Junk (recommend delete)

### 1.1 Game data sync accidents

These folders are clearly Rockstar/Steam/Chromium app data that got swept into Drive by the desktop client. Nothing here has user value. Delete the whole tree.

- `GTAV Enhanced/` (id `1M-cFiHLGZrK3FXvNrRT1GyLf-4P_np43`) and everything under it: `Settings/`, `User Music/`, `Social Club/Renderer/`, `Network/`, `Cache/`, `Cache_Data/`, `Code Cache/`, `Local Storage/`, `Session Storage/`, `DawnCache/`, `GPUCache/`, `blob_storage/`, `js/`, `wasm/`, `index-dir/`, `leveldb/`.
- Standalone Steam ID folder `76561198326723718` (id `16vBLABFL7fPEjSJkw7oiMTDJcBk9M34J`).
- Single-file `LocalSettings` (id `1nN4LYn3bpFvuHBne1DSg5_vKuCCZKte8`) which is an `application/octet-stream` config file from a game.

[GTAV Enhanced folder](https://drive.google.com/drive/folders/1M-cFiHLGZrK3FXvNrRT1GyLf-4P_np43)

### 1.2 OS installer media

These are large installers and ISOs that can be re-downloaded any time. Keep nothing in Drive.

- `Windows.iso` (8.45 GB) [view](https://drive.google.com/file/d/14YOlCE602u_aanir99jRPjYwB02MXWuV/view?usp=drivesdk)
- `MediaCreationTool_22H2.exe` (19 MB) [view](https://drive.google.com/file/d/17LRdsOE5k1xV0zx8P6qa_CAQ_dwt-Zmy/view?usp=drivesdk)
- `rufus-4.14.exe` (2 MB) [view](https://drive.google.com/file/d/1dFglycf7UrjBsSVHwy2VUI0hN6BHKwzd/view?usp=drivesdk)

### 1.3 Office lock files

Microsoft Office writes tiny `~$filename.docx` lock files while a document is open. They're 162 bytes of nothing once Word closes. Three of these are stuck in Downloads:

- `~$ch_Questionnaire_Baird_2026_Drew_Mattick.docx` [view](https://drive.google.com/file/d/1c5deetucaggU2_GV8RMXyjzmNURRbVd1/view?usp=drivesdk)
- `~$ticle_1_v3_Your_Platform_Is_Becoming_an_OS_Commented.docx` [view](https://drive.google.com/file/d/1uuV3qTtaEAWwJ_HI4zPrBcoh1C2gW-HN/view?usp=drivesdk)
- `~$S_421_Mattick_Week2.docx` [view](https://drive.google.com/file/d/1BgGsuOUeffbYpeE850gh8g-qgViKmlt_/view?usp=drivesdk)

### 1.4 Random text/notification cruft at root

- `https://images..txt` (43 bytes, looks like a half-pasted URL) [view](https://drive.google.com/file/d/18wPvsPZ0_JXpZy1cGdT8_8xJUNzK812X/view?usp=drivesdk)
- `Andrew Mattick has shared a file with you` (83 bytes, this is the email notification body saved as a file somehow) [view](https://drive.google.com/file/d/1BRzFPV8ywzqqmaN9OnzEC1Z2dHLgvzmq/view?usp=drivesdk)

### 1.5 Empty / near-empty Untitled documents at root

Five Untitled documents, four are 1 KB (empty Google Doc shell), one is ~2 KB. Open each, confirm no content, delete.

- `Untitled document` (2026-03-04, 1 KB) [open](https://docs.google.com/document/d/11QnFvYvlKua01RlNITymF-G07p5Tqz1Cey6H2rMozFs/edit?usp=drivesdk)
- `Untitled document` (2026-02-27, 1 KB) [open](https://docs.google.com/document/d/1_FRRkJ-UgenLg6sfdliNmsJrDdz1MBZPCwnUnUU6emQ/edit?usp=drivesdk)
- `Untitled document` (2025-09-30, 2.2 KB) [open](https://docs.google.com/document/d/1kRt39nH7y5GIbiaXMqji0BKH8vMlUOLp9vdHb3lyRcU/edit?usp=drivesdk)
- `Untitled document` (2025-05-29, 1 KB) [open](https://docs.google.com/document/d/1uLy__SZqshrcQxaZhFM8sbK6ugCVB4AvfmHa86D_k78/edit?usp=drivesdk)
- `Untitled document` (2025-05-29, 1 KB) [open](https://docs.google.com/document/d/1NK8ONJPSuDOO4gBAWieT411sXx7B0QVAxp0QN169cMs/edit?usp=drivesdk)
- `Untitled document.txt` (in nested folder, 2024-10-22) [view](https://drive.google.com/file/d/1-ihbS1WSXu50uW3tBgXQnhapayGgdpeN/view?usp=drivesdk)

### 1.6 Old phone screenshot dump (Sept 2024)

Folder `1ZTCqas-rm9GpsjTITbn9H5pwCsPOlq_5` is a flat dump of around 100 Samsung-style screenshots from Sept 2024: Snapchat, Reddit, Temu, Chrome, Messages, ChatGPT, Facebook, Drive itself. Filename pattern `Screenshot_20240911_XXXXXX_AppName.jpg`. Two years old, nothing legal-looking that I can tell from titles. Recommendation: backup to Google Photos if there's anything sentimental, then drop the folder.

[Screenshot folder](https://drive.google.com/drive/folders/1ZTCqas-rm9GpsjTITbn9H5pwCsPOlq_5)

### 1.7 Scattered root-level screenshots (2024)

These look like single-shot android screenshots that landed at root one at a time. None look load-bearing.

- `Screenshot_20240807_143439_Snapchat.jpg` [view](https://drive.google.com/file/d/15QbsmdluV5ZuzeKZv05xBZ7wuwaZvDVR/view?usp=drivesdk)
- `Screenshot_20240807_144710_Microsoft 365 (Office).jpg` [view](https://drive.google.com/file/d/15NSNIs3gi8KI6QtJdiaL5SCvOtwdZEIx/view?usp=drivesdk)
- `Screenshot_20241017_124646_CVS.jpg` [view](https://drive.google.com/file/d/15wuSoy2qL67TnSype941FFR6c3LljXVV/view?usp=drivesdk)
- `Screenshot_20241030_003456_Chrome.jpg` [view](https://drive.google.com/file/d/1840ZMtnSL-_rHbyUjbqKs0rNnDOxFc1I/view?usp=drivesdk)
- `Screenshot_20241111_184814_ChatGPT.jpg` [view](https://drive.google.com/file/d/19vs78utp4wuj4sEdSBmjK9h6zlflWFgg/view?usp=drivesdk)
- `Screenshot_20241130_172302_ChatGPT.jpg` [view](https://drive.google.com/file/d/1E3de7RQVjf3_WhUJlmEOjHbqZ-n-7eXp/view?usp=drivesdk)
- `Screenshot_20241227_200814_Adobe Acrobat.jpg` [view](https://drive.google.com/file/d/1NQcKxDCsX0CejlryWqzBJDhFvpdQXIUe/view?usp=drivesdk)
- `EarBuds_return.png` (return label screenshot, the actual return is presumably done) [view](https://drive.google.com/file/d/1-HlHg9K4MBWKH02hbRDMG2MDS1THGr8m/view?usp=drivesdk)

---

## Section 2: Duplicate Cleanup Checklist

For every group, the recommendation lists which one to keep and which to remove. When in doubt, keep the most recent modifiedTime and the cleanest filename.

### 2.1 Jetson Nano SD card images (HUGE)

Two ~13.8 GB `.img` files. Almost certainly the same image, one copied or re-flashed. Same exact size (13,816,037,376 bytes), created within 30 seconds of each other.

- **Keep one** (your call which):
  - `sd-blob-b01.img` parent `1OlZ5bBPCHW05ovXvS4f1fr4thWyHjvTC` [view](https://drive.google.com/file/d/1ZcL-Jkxz3P1kbQllnkl4XpLCqWCWwB_x/view?usp=drivesdk)
  - `sd-blob.img` parent `1FbW7r9NBN4UvJLsKaighracAw__YqcA2` [view](https://drive.google.com/file/d/1Pl40OMLpyZxUVpjkM-3d2xh_o8-aPiaE/view?usp=drivesdk)
- **Better:** move whichever you keep to a NAS or external drive and remove both from Drive. SD card images are not cloud-backup material.

### 2.2 Financial_Disclosure spreadsheets (4 versions)

Created within 4 minutes of each other on 2026-05-13. Three Google Sheets, all 11,920 bytes (so likely identical content), plus the original `.xlsx`.

- **Keep:** the one in the Divorce folder context: `Financial_Disclosure` id `1sXvVrfPkrExiq02nKTZCvpwgI3on3Xa76T_6wsNHQMs`, parent `1LcW3VP5ad8EnwRw2idBeCQFEtviqfQpX` (Divorce folder) [open](https://docs.google.com/spreadsheets/d/1sXvVrfPkrExiq02nKTZCvpwgI3on3Xa76T_6wsNHQMs/edit?usp=drivesdk)
- **Delete the rest:**
  - Root-level Google Sheet copy [open](https://docs.google.com/spreadsheets/d/1a3uw4V9HpNaSEpqyczAMQcD003YtZzdtN_9xQjpwHQA/edit?usp=drivesdk)
  - Copy in folder `1TLj-AbReWGyQC0Fs6PzOJTnB5XZdiHeT` [open](https://docs.google.com/spreadsheets/d/1aIWdpv4rHHZV11S3WnHutbjraq2ZBdC2-tiUI0aUE2M/edit?usp=drivesdk)
  - Root-level `.xlsx` copy [view](https://drive.google.com/file/d/13WL5W8cA6lZp_M_yo4vG1qsTuzCInfXw/view?usp=drivesdk)

### 2.3 April 2026 Resume variants

Three versions of the same April 2026 resume.

- **Keep:** `Mattick_Resume_April2026.docx` in Downloads (most recent edits, 1.16 MB suggests it's the version with embedded content) [view](https://drive.google.com/file/d/1y2Mn_miLfJVIu9MwSb9tcslI0zgj7yPo/view?usp=drivesdk)
- **Delete:**
  - `Mattick_Resume_April2026.docx` root (14 KB, stripped version) [view](https://drive.google.com/file/d/1g1n6hu7d7himE40PMmTK-RhhswZf_wWE/view?usp=drivesdk)
  - `Mattick_Resume_April2026 (AutoRecovered).docx` in `Documents/` (Word autosave crash recovery) [view](https://drive.google.com/file/d/1RyQMH4eUydOkaB9KBBj1HJbJ-XLrkPD9/view?usp=drivesdk)
- **Keep PDF export:** `Resume_Mattick_April2026.pdf` (one copy is enough; pick the more recent of the two)
  - Root copy [view](https://drive.google.com/file/d/11pDxZCZ1w6xsl7KvMYQhoqCd3K-pshNz/view?usp=drivesdk)
  - Downloads copy (same name, identical size 533,694) [view](https://drive.google.com/file/d/1Kvl5VP_59QaWthoeXMxDtyz0zDBxx9wA/view?usp=drivesdk)

### 2.4 Resume legacy variants

Multiple older resume drafts scattered at root. Either consolidate into `Resumes & Career/` or remove the empty ones.

- **Keep current canonical:** `Andrew Mattick Resume May 2026` (lives in `Resumes & Career/`, this looks right) [view](https://drive.google.com/file/d/1-XLnqW8K0eI2-RnanoCVIpkQn2rpAD3K/view?usp=drivesdk)
- **Move into `Resumes & Career/`:**
  - `Resume_June2025` Google Doc [open](https://docs.google.com/document/d/1LrMwM9Bj375pi75eO8wJzmAV9UrcArmRhIhZxgMTgM4/edit?usp=drivesdk)
  - `Feb-2026-Resume` Google Doc [open](https://docs.google.com/document/d/1g5sh8N4pJfmRwuAwHVXKvvbzoz-8BjqVI9Xf5otMCks/edit?usp=drivesdk)
  - `Mattick_CoverLetter_June2025` [open](https://docs.google.com/document/d/1CBZQsw6DGC7IMnvbUA8hjDThZFakvNFMb8koJ9SFsCc/edit?usp=drivesdk)
  - `Mattick_CoverLetter_MetaMask` [open](https://docs.google.com/document/d/1MlZI2se1dSrJqd77Sq5ZxrghHMGVBknb7hbj34-Ulms/edit?usp=drivesdk)
- **Delete (empty stubs):**
  - `Resume` root (11.8 KB) [open](https://docs.google.com/document/d/1HDBGC0pBDB7xz4SUaEHVItYhBIoJMbTgzb54Fv1rewE/edit?usp=drivesdk)
  - `Resume` root (3.5 KB) [open](https://docs.google.com/document/d/1OHnB9dQ6Xs-l2v9_ceZtvOh8NlF48PplSozVLdpLxjo/edit?usp=drivesdk)

### 2.5 Same image, different format

`Screenshot_20241118_200118_SLS.jpg` and `Screenshot_20241118_200118_SLS.pdf` are the exact same file size (1,205,193 bytes). One is a JPG, the other was uploaded with `.pdf` extension but the mimeType is still `image/jpeg`. The `.pdf` one is misnamed.

- **Keep:** the `.jpg` [view](https://drive.google.com/file/d/1CnGNtFM4pGXjOgcK81MBYpduMZgA3zN1/view?usp=drivesdk)
- **Delete:** the misnamed `.pdf` [view](https://drive.google.com/file/d/1BLfPQEjVtNrMGk90cT7KSZfi2Qs_tRWs/view?usp=drivesdk)

### 2.6 20241023_141600.jpg recent duplicate

A "Copy of" of an October 2024 photo was created at root TODAY (2026-05-18, by some sync or copy action). Original is 16 months old.

- **Keep:** the original `20241023_141600.jpg` (or move to Lindsay_Family / Photos folder) [view](https://drive.google.com/file/d/16WLTOZnKhr2xBO4BEutfZkjoQZtaBzlq/view?usp=drivesdk)
- **Delete:** `Copy of 20241023_141600.jpg` (created today, identical size 1,293,777) [view](https://drive.google.com/file/d/1ZWu6ioLMzT4-HI1tBK3TdMawwTqBibDj/view?usp=drivesdk)

### 2.7 Wallet folder vs Wallet - Copy folder

There are two wallet folders. Content includes cryptocurrency wallet paper-backups. **DO NOT BATCH DELETE.** This is the sensitive case in section 4.1. Manual review only.

- `Wallet - Copy/` at root [view](https://drive.google.com/drive/folders/1-bmMj3WGbMoXjNXGASHfjXAdLojzKV-R)
- `Wallet/` inside `Life/Christmas Gift_2024/` [view](https://drive.google.com/drive/folders/1pQWqmNlZmsiSk2WEPro1Zbyo3NefN5X3)
- Inside `Wallet - Copy/`: ten `wallet-1` through `wallet-6` PNG / JPG images plus two `Dogecoin_Wallet_*.pdf` files. Files named `wallet-5` and `Wallet-5_6` look like they might be the same screenshot saved twice. Verify before deleting either.

### 2.8 January 2026 bank statement duplicate

`decemberStatement.pdf` and `Bank Statement - January 2026.pdf` are both exactly 26,330 bytes. Suspiciously identical. Either both are the same statement saved twice with different names, or one was renamed and re-uploaded. Sensitive content, so check both.

- `decemberStatement.pdf` (root) [view](https://drive.google.com/file/d/1GXF3hMYTYU1QXiT1iMUGm3Np6loHwT9B/view?usp=drivesdk)
- `Bank Statement - January 2026.pdf` (in `Documents/`) [view](https://drive.google.com/file/d/1t50j36nuhSBSGnpL7SE3XOG7qhKcBtmS/view?usp=drivesdk)

### 2.9 Project Vector / Vector_Patent twins

Two folders that almost certainly should be one.

- `Project Vector/` (created 2026-05-18) [view](https://drive.google.com/drive/folders/17GZYFXYluL_hAO9peV647953CbTDy4bn)
- `Vector_Patent/` (created 2026-04-27) [view](https://drive.google.com/drive/folders/10cEcX2CsJtet2XY_A6P4-6YWD10Veb-z)
- Plus three loose Vector files at root: `PROJECT VECTOR: PROVISIONAL PATENT APPLICATION`, `Jeremy_Vector`, `Commented-intent-architecture`, `intent-architecture`
- **Recommendation:** merge everything into a single `Project Vector/` folder with subfolders `patent/`, `architecture/`, `correspondence/`.

### 2.10 Claude scheduled task duplicates

Inside `Documents/Claude/Scheduled/` there are three case-watcher folders, each containing their own `SKILL.md`. Looks like scheduled tasks were recreated under slightly different names instead of being updated in place.

- `mattick-case-tracker/` (created 2026-04-26) [view](https://drive.google.com/drive/folders/1a8G9NBXBxvby98F05qmC1KNFsIiM9pv8)
- `mattick-case-watcher/` (created 2026-05-13) [view](https://drive.google.com/drive/folders/1Ij0WXv48o-32SxUyI5l3xJ_C_IwuIGiW)
- `mattick-case-watch-afternoon/` (created 2026-05-13, same day, one minute later) [view](https://drive.google.com/drive/folders/1Q4EEYqJuZZS5rf5j9M_U8qQ6lQ4A_1wf)
- `attorney-followup-reminder/` (related, created 2026-04-26) [view](https://drive.google.com/drive/folders/1_U5nzeOn7HUtLvHRONordE_zNXsRQRQB)
- **Recommendation:** keep the most recent one as the canonical scheduled task, archive the others. Also worth fixing the underlying scheduling skill so it stops spawning new folders for the same task.

### 2.11 Documents folder collision

Drive has two folders named `Documents` because the Drive Desktop client created one at root in late 2024 and a second one came in via the `My Computer/` sync tree later.

- `Documents/` at root (id `1Ov0XO61U5O6GFBoVBLB-bFdvAjQWD_ct`, created 2024-12-28) [view](https://drive.google.com/drive/folders/1Ov0XO61U5O6GFBoVBLB-bFdvAjQWD_ct)
- `My Computer/Documents/` (id `1FxcgETdaVLRcqH-iXYQWPUDbbkfha_Wt`, created 2026-03-10) [view](https://drive.google.com/drive/folders/1FxcgETdaVLRcqH-iXYQWPUDbbkfha_Wt)
- **Recommendation:** decide which is authoritative, move overlapping content into one, then remove the empty one. Drive Desktop's `My Computer` tree is convenient but creates confusion like this.

---

## Section 3: Archive Candidates (old, low recency, possibly still valuable)

These haven't been modified in 18+ months but might still be worth keeping. Move to a new `_archive/` folder so they stop cluttering search results without losing them.

### 3.1 2024 school / attendance docs

Weekly attendance reports for Sept-Oct 2024. Each 2-3 KB. Probably needed for a specific school context Drew has since moved on from.

- `Attendance for week ending 8-30-2024` [open](https://docs.google.com/document/d/1tpOLR6i9us8ut8RP-bfts2DT1g_zT-A4yj_NswkDGHs/edit)
- `Attendance for week ending 9-6-2024` [open](https://docs.google.com/document/d/1QqZV4hlcYpS57BP2QdyKFkdJaTGSvWef6cy-4eq2qFo/edit)
- `Attendance for week ending 9-20-2024` [open](https://docs.google.com/document/d/1B3Jjf64xhRxVPmFXcIvQl51vqUNJxVuSFEaKSYvUzRA/edit)
- `Attendance for week ending 10-11-2024` [open](https://docs.google.com/document/d/13vA7fFuRMW6rCOXkQodSM6gsUT39_YIVf2aJ5IgDidE/edit)
- `Attendance for week ending 10-18-2024` [open](https://docs.google.com/document/d/1rRJZMHPZ2-N5sfNEMmxq2VuWH_6pdj9-CmkbP_o4FYk/edit)

### 3.2 2021 misc

- `they_stopped_in_time_10.pdf` (2021-06) [view](https://drive.google.com/file/d/1D8xS-SHCk31Qh_7Zpoy-TkYJtS_uIWoV/view?usp=drivesdk)
- `NET SQL Assessment.docx` (2021-06, possibly a coding assessment artifact) [view](https://drive.google.com/file/d/1M0YPIxxo-sXBJ8U3YRl3IhefS0qOAals/view?usp=drivesdk)
- `Prepaid_Label.pdf` and `RMA_Agreement.pdf` (2021-11, return shipping; nothing to do five years later) [view](https://drive.google.com/file/d/1oIUMGO6vYdRl0WWY3UHYWabGVjOhyGuP/view?usp=drivesdk), [view](https://drive.google.com/file/d/1w2CAX0-Wxng5F3ENoakP8KJLBRk6RvQa/view?usp=drivesdk)

### 3.3 Sept 2024 work docs (likely Landon-school related)

- `Daily Check In System.docx` (2024-09-24) [view](https://drive.google.com/file/d/1xGOiWymayLsfO8fiypRti2IwpV-grn6T/view?usp=drivesdk)
- `Bugs_Explaination.docx` (2024-09-05) [view](https://drive.google.com/file/d/11NIOvGAcO13vf8Rc8ce-hCHdHtEmdMzU/view?usp=drivesdk)
- `Landon School/` folder [view](https://drive.google.com/drive/folders/1BAr7hU67lSyIv5DiSxv8fzvYkeVRHe1b)

### 3.4 Older photos

- `I_was_in_trouble_for_something.jpg` (2024-10) [view](https://drive.google.com/file/d/17EXv9O1pXoACyWLx_4O8W6VoaEtmNl93/view?usp=drivesdk)
- `Photo - Oct 3 2024` (2024-10) [view](https://drive.google.com/file/d/15KEgHUkntouhhwqD8yuTB0AEmgSegkk0/view?usp=drivesdk)

---

## Section 4: Sensitive / Needs Drew's Eyes-On Review

I did **not** read content of these. They're flagged for your manual review only.

### 4.1 Cryptocurrency wallet material

This is the highest-risk category in the Drive. If any of these contain seed phrases or private keys in plaintext (image, PDF), they should not be in Drive at all - they should be on an encrypted vault or hardware wallet.

- `Wallet - Copy/` folder at root, containing two `Dogecoin_Wallet_*.pdf` files (~800 KB each) and 10 wallet screenshot images [view](https://drive.google.com/drive/folders/1-bmMj3WGbMoXjNXGASHfjXAdLojzKV-R)
- `Wallet/` folder inside `Life/Christmas Gift_2024/` [view](https://drive.google.com/drive/folders/1pQWqmNlZmsiSk2WEPro1Zbyo3NefN5X3)
- `TrustWalletBackup/` folder at root [view](https://drive.google.com/drive/folders/1AyvVT1NNPMjPgBGbxQwxSm0svuWYFg-D)

**Recommendation:** open each in private, confirm whether secrets are visible, and if so move them off Drive entirely. Sensible target: hardware wallet (Ledger / Trezor) or a Bitwarden secure note.

### 4.2 Tax / financial documents

These belong in Drive but should live in the `DrewsTaxes/` tree, not at root.

- `Lindsay Strzyzewski 2020 Tax Return.T20` at root (proprietary TurboTax file from 2020) [view](https://drive.google.com/file/d/1ztH6faFhNXudr1fZj7xdvO6mymb1FKKj/view?usp=drivesdk)
- `SallieMae 1098-E.pdf`, `Elmbrook W2.pdf`, `Firstmark 1098-E.pdf` at root [view](https://drive.google.com/file/d/1m1zC_iEISpwd057_zpifzjimTNgh0MdX/view), [view](https://drive.google.com/file/d/1peIgyyGupeSpPALvZD6G4Mqh5lFupMbM/view), [view](https://drive.google.com/file/d/1Z-zVtBLiQxO5iJawUBbComY0BZAKrwCT/view)
- `DirectDepositForm.pdf` (almost certainly contains bank routing + account numbers) [view](https://drive.google.com/file/d/1KMUBec9KJIxGgJcPt3XOaU0Fjwnxtq1k/view?usp=drivesdk)
- Bank statements at root: `decemberStatement.pdf`, `januaryStatement.pdf` [view](https://drive.google.com/file/d/1GXF3hMYTYU1QXiT1iMUGm3Np6loHwT9B/view), [view](https://drive.google.com/file/d/1zbZYB6oVFu8LEhPeGTmDkJHraMhCwKUR/view)
- `bill_11501.pdf` on Desktop [view](https://drive.google.com/file/d/1pJosE47zyg9xiLbUAoYGpztjyggwr9xq/view?usp=drivesdk)
- `Work Search History - Week Detail.pdf` (unemployment paperwork) [view](https://drive.google.com/file/d/1xhd5QoxrSms4B9ONNgg-IV6RNUcyA5on/view?usp=drivesdk)

### 4.3 Legal / divorce / family law

- `Divorce/` folder at root (active matter) [view](https://drive.google.com/drive/folders/1LcW3VP5ad8EnwRw2idBeCQFEtviqfQpX)
- `Legal Documents/` folder at root [view](https://drive.google.com/drive/folders/1n3yY-zCZW2KyABEHqjHd3D04dnbd9zTG)
- `Mattick MSA 11.10.2025 (Revised).docx` (788 KB, marital settlement agreement) [view](https://drive.google.com/file/d/1q-5XJlmYp2-pM7ITeACJDONdytlRuq6O/view?usp=drivesdk)
- `Mattick MSA 11.2.25.docx` (earlier MSA draft) [view](https://drive.google.com/file/d/1sdViDUmZLKNd9uGYq5TTW7mvvs4fES_t/view?usp=drivesdk)
- `Mattick_MSA_Counter_11_4_25` Google Doc [open](https://docs.google.com/document/d/1ywOPAx4aIPaA9wQwOhffRJOMCjfLw4XUijhodMY-UHI/edit)
- `Mattick v Mattick - 2024FA000016/` folder inside `Documents/Claude/Projects/` [view](https://drive.google.com/drive/folders/1oVcnDiEba1-yxEuATRdGYwU2DGgcNSIr)
- `Motions_to_Adjourn_April_2026.docx` at root [view](https://drive.google.com/file/d/15mFfMa1nNYfelmWXkIzYtRoFSw3pkoOr/view?usp=drivesdk)
- `Motion_To_Adjurn_Pending_Educational_Assesment.docx` at root [view](https://drive.google.com/file/d/1R1OFRqf7ZxlLqN20lUpGlsORPgQ92w6P/view?usp=drivesdk)
- `Motion to Reopen Small Claims Judgment (Case # 2025SC000604)` Google Doc [open](https://docs.google.com/document/d/1y9nggcmoQxNnrSxsXB7HlTPFB3_iKMXvGUVbO-Yg-vk/edit)
- `Wisconsin State Legal Document` at root [view](https://drive.google.com/file/d/1HnnQ-9lDP-qqmAc6o6oqpudv6EhfaYHp/view?usp=drivesdk)
- `Community Support for Commutation of Sentence (1).docx.pdf` at root (note the doubled extension) [view](https://drive.google.com/file/d/1iBLMyunrVp61w-5YC2GpnYtLmBMsS3-8/view?usp=drivesdk)
- `Case_Summary_UW_Law_Clinic.docx` at root [view](https://drive.google.com/file/d/1-YA5jlgDOM3zNV5yPbBrJArzbWGn5Dls/view?usp=drivesdk)
- `Guardian ad Litem Questionnaire update.pdf` (3.5 MB at root) [view](https://drive.google.com/file/d/12G5uYtSRvRpOt4A8COZbAI3lMX7hlJG2/view?usp=drivesdk)
- `Mattick Andrew CBC 2024` Google Doc (background check) [open](https://docs.google.com/document/d/1Rv3KmBAvjT0TXgwUJQL1x68iq3wsRG0DjZ38HWQ7RXU/edit)
- `Fraud/` folder at root [view](https://drive.google.com/drive/folders/1QGe7N83aRozcbli7Jk2BSh80Y7jF8Sam)

All of these belong inside `Legal Documents/` (or a per-matter subfolder).

### 4.4 Patent / IP

- `PROJECT VECTOR: PROVISIONAL PATENT APPLICATION` Google Doc at root [open](https://docs.google.com/document/d/1my7tE9FLoQfVuKqUSX8zyp27-Tdy9DL3DWbvcramI_Y/edit)
- `Mattick_Persistence_of_Self_Working_Draft` [open](https://docs.google.com/document/d/1JyknUdnJgR_zPKe-buN7d4w8QMWNOCI_87Q02lsTLK8/edit)
- `Mattick_Containerized_Intelligence_Propagation_Working_Draft` [open](https://docs.google.com/document/d/147IL-754ivaWF2gElW3DfXajFy2GvtXOZHaX8rgifJc/edit)
- `Mattick_Nexus_Distributed_Cognitive_Architecture_Working_Draft` [open](https://docs.google.com/document/d/1Z8DiWnyfdkdyaVdTN79zyi3ql5DP-xa8k64Fd-j3FX0/edit)
- `intent-architecture` and `Commented-intent-architecture` (likely two versions of the same doc)

Consolidate under one `Project Vector/` folder. Sensitive because of prior art / patent novelty implications: don't share more broadly than needed.

### 4.5 Personal health / medical

- `Contact_Prescription - Sep 12, 2024.pdf` [view](https://drive.google.com/file/d/1AKjJAAhrusfsSrEMpnx01oig8Um_1Xw-/view?usp=drivesdk)
- `Eyewear Prescription - Sep 12, 2026.pdf` (note the year in the title is 2026, modifiedTime is 2024 - probable typo) [view](https://drive.google.com/file/d/1AJpe_DRViInqFHFX93RvG8FJJqwfwm_y/view?usp=drivesdk)
- `Strzyzewski-I-1_Notice_of_Team_Meeting_-1778267501840.pdf` (school IEP meeting notice, shared by Elmbrook teacher) [view](https://drive.google.com/file/d/1gcxT67HQmNsSZRk1L1eiAENbFR2R6iaA/view?usp=drivesdk)

### 4.6 Identity / background check

- `Mattick Andrew CBC 2024` (mentioned above) - this is a Criminal Background Check document. Make sure its sharing permissions are tight. Recommend `get_file_permissions` on it.

---

## Section 5: Keep + Organize (the real work, just put it in the right place)

These are healthy documents but they're sitting at root and should be moved into the appropriate folder.

| File | Current location | Suggested home |
|---|---|---|
| `Two Maids Welcome Packet 2025_compressed.pdf` (9.6 MB) | root | `Resumes & Career/Employer Materials/` |
| `Elmbrook_Compensation_Model.pdf` (800 KB) | root | `Resumes & Career/Employer Materials/Elmbrook/` |
| `RemingtonBehaviorOverview.pdf` | root | `Life/Remington (or pet name)/` if Remi is a child, family folder if a pet |
| `Remi-Weekly-Tracker (1)` Sheet | root | Same as above |
| `Week1_Applications of Buoyancy: Flotation` doc | root | `Academic Coursework/Physics/Week 1/` |
| `Week_Fluids_Discussion_Initial_Post.md` | nested in `Physics Lab - UPOX` | leave (already in a sensible spot) |
| `Week3_Assesment_Mattick_10_28_25` | root | `Academic Coursework/<course>/Week 3/` |
| `POS355_Week5_Assessment_Mattick` | root | `Academic Coursework/POS355/Week 5/` |
| `The Cost of Food Insecurity in Wisconsin` | root | `Academic Coursework/<course>/` |
| `Mattick MSA 11.10.2025 (Revised).docx`, all MSA drafts | root | `Legal Documents/Divorce/MSA Drafts/` |
| `Motion_To_Adjurn*.docx`, `Motions_to_Adjourn_April_2026.docx` | root | `Legal Documents/Motions/` |
| `Wisconsin State Legal Document` | root | `Legal Documents/` |
| `Community Support for Commutation of Sentence` | root | `Legal Documents/Commutation/` |
| `The Mattick's todo list` | root | `Life/Family/` |
| `Flower note` (Dec 2025) | root | `Life/Personal/` |
| `jci_cheat_sheet` (interview cheat sheet for JCI) | root | `Resumes & Career/Interview Prep/JCI/` |
| `Mattick_Resume_JCI_May2026.md`, `Mattick_CoverLetter_JCI_May2026.md` | Downloads | `Resumes & Career/Applications/JCI/` |
| `Mattick_Resume_Eaton_May2026.md`, `Mattick_CoverLetter_Eaton_May2026.md` | Downloads | `Resumes & Career/Applications/Eaton/` |
| `30-Day-MATLAB-Power-Systems-Plan.md` | Downloads | `Academic Coursework/MATLAB/` or `Resumes & Career/Skill Building/` |
| `career-search.zip` | Downloads | `Resumes & Career/Archive/` |
| 3D model `.3mf` files | Downloads | `Life/3D Printing/` (or wherever your maker projects go) |
| `Lad Lake.pdf` | Desktop | `Resumes & Career/Applications/Lad Lake/` |

---

## Section 6: Proposed Folder Structure

If you were starting clean, this is what would make sense given what you actually have. Names are suggestions, not gospel.

```
My Drive/
  Active Work/
    Project Vector/                    (merge Project Vector + Vector_Patent + all loose Vector docs)
      patent/
      architecture/                    (intent-architecture, persistence-of-self, etc.)
      correspondence/                  (Jeremy_Vector, etc.)
    Academic Coursework/
      Physics/                         (UPOX, buoyancy, fluids)
      POS355/
      <other courses>/
    Resumes & Career/
      Applications/
        Eaton/
        JCI/
        Lad Lake/
        <future>/
      Resume Versions/                 (one canonical resume + dated archive)
      Cover Letters/
      Interview Prep/
      Employer Materials/              (welcome packets, compensation models)
      Skill Building/                  (MATLAB plan, learning artifacts)
  Personal/
    Life/
      Family/                          (todo list, Lindsay_Family photos)
      Remi/                            (kid stuff: behavior overview, weekly tracker, school IEP)
      3D Printing/                     (.3mf files, makerspace work)
      Photos/                          (consolidate phone screenshots/photos if you keep any)
    Financial/
      Bank Statements/
      Pay Stubs/                       (existing PayStub/ folder)
      DrewsTaxes/                      (already well-organized - leave alone)
      Direct Deposit/
    Health/
      Prescriptions/
      Insurance/
    Crypto/                            (or better: nothing in Drive, use hardware wallet)
  Legal Documents/
    Divorce/                           (existing folder)
      MSA Drafts/
      Motions/
      Disclosures/                     (Financial_Disclosure lives here)
      Court Notices/
    Other Matters/
      Small Claims 2025SC000604/
      Commutation/
      Fraud/
      Mentorship-EEOC/                 (existing folder)
    GAL & Family Court/                (Guardian ad Litem questionnaire, IEP, custody)
  Scheduled Tasks/                     (single canonical home for Claude scheduled jobs)
    mattick-case-watcher/              (just one)
  _archive/                            (anything you don't actively touch but won't delete)
    2024-school-attendance/
    2021-misc/
    Sept-2024-screenshots/             (if you keep them)
  _to-review/                          (parking lot - migrate then delete this folder)
```

**Folders to remove entirely** (after content rescue):
- `My Computer/` and its `Desktop/`, `Downloads/`, `Documents/` trees (or at minimum, exclude Downloads from Drive Desktop sync)
- All game-cache trees rooted in `GTAV Enhanced/`
- `Wallet - Copy/` once you've consolidated wallet backups elsewhere
- `Sms Amanda/`, `Shared files Amanda/`, `Sophia/` - review if you want to keep these standalone or fold them into a `Personal/People/` subfolder

---

## Section 7: Settings / Behavioral Changes (so this doesn't grow back)

1. **Drive Desktop sync scope.** The biggest contributor to the mess is `My Computer/` mirroring your Desktop, Downloads, and Documents into Drive. Open the Google Drive desktop app preferences and either turn off the `My Computer` backup, or at least exclude `Downloads/`, `Desktop/`, game install directories (Steam, Rockstar Games, etc.), and any application data directories. Anything that ends in `Cache`, `Local Storage`, `Session Storage`, `Code Cache`, `wasm`, `js`, or `index-dir` is app data, not user data.
2. **One canonical resume.** Pick one master `.docx`, version it inside, and export PDFs only when applying. Stop saving `_AutoRecovered`, `_v2`, `_final`, `_finalfinal` variants to Drive.
3. **Scheduled tasks.** Whatever skill is creating `mattick-case-watch*` folders should update in place, not spawn new folders. Worth tightening that skill before the next run.
4. **Screenshots.** If you want phone screenshots in the cloud, point them at Google Photos rather than Drive root. Drive root is for documents.
5. **Crypto seeds.** Seed phrases and paper wallets should never live unencrypted in Drive. If those wallet PDFs/PNGs contain phrases, migrate to a hardware wallet or an encrypted Bitwarden secure note, then remove.

---

## Appendix A: Survey Caps and Caveats

- **Scope:** First-pass survey via Drive search APIs. Did not paginate beyond ~500 files. Older items (>2 years) were sampled, not exhaustively listed.
- **No content reading** on financial, legal, medical, or crypto files. Only titles, sizes, dates, parent folder IDs.
- **Total Drive size not directly available** via the search API. The largest single items (the two 13.8 GB `.img` files and the 8.45 GB Windows.iso) account for ~36 GB, which is likely the bulk of consumed quota.
- **Folder counts** for game-cache trees were not tallied exhaustively. The folder hierarchy under `GTAV Enhanced/Social Club/Renderer/` alone produced ~15 nested folders in the first page of results.
- **Shared-with-me files** (e.g. the Elmbrook teacher's weekly comms) were not in scope for cleanup since Drew doesn't own them.

## Appendix B: Action Order (recommended)

1. **High-leverage deletes** (Section 1 plus the giant install media in 2.1). Roughly 30 minutes, reclaims most quota.
2. **Sensitive review** (Section 4). Open each, decide stay/move/secret-rotate. Not parallelizable.
3. **Duplicate cleanup** (Section 2). Methodical, mostly trivial decisions.
4. **Move loose root files** into the structure (Section 5 + Section 6). The longest piece, but feels good once done.
5. **Fix sync and scheduled task config** (Section 7). Without this, the mess grows back.

## Appendix C: Permissions Spot-Check (not done)

I did not run `get_file_permissions` on any file. Recommendation: before publicizing the Vector patent docs more broadly, run a permissions audit on:
- `PROJECT VECTOR: PROVISIONAL PATENT APPLICATION`
- `Mattick Andrew CBC 2024`
- `Mattick MSA 11.10.2025 (Revised).docx`
- The full `Wallet - Copy/` folder

If sharing is set to "anyone with link," lock it down.
