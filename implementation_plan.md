# Implementation Plan — Oracle Validation & ID Analysis
## 2026-07-25

---

## Stage 1: Oracle Validation (CRITICAL)

**Goal:** Build a confusion matrix for the body-size oracle. Determine if it's real or artifact.

### 1.1. Test Set Preparation

#### 1.1a. KNOWN_EXISTS (100 IDs from our account)
**Input:** `anchor_ids.txt` (2207 IDs)  
**Verification:** SESSIONKEY check — `POST postimg.cc/json?action=list&album={ID}` → 500(132B) = exists, 404(56B) = doesn't.  
**Output:** `oracle_testset/exists_verified.jsonl`

```python
# verify_anchors.py
# 1. Read anchor_ids.txt
# 2. For each ID: SESSIONKEY check (no proxy needed, direct)
# 3. Filter to first 100 confirmed existing
# 4. Save: {"gid": "...", "label": "KNOWN_EXISTS", "verified_http": 500, "verified_size": 132}
```

**Time:** ~2 sec (100 IDs at 100+ rps via SESSIONKEY)

#### 1.1b. KNOWN_DELETED (100 IDs we create then delete)
**Challenge:** Requires browser automation for upload + delete.

**Approach A (fast, manual):** Manually create 10 galleries in browser, record IDs, then delete them. Wait 10 min, create 10 more.  
**Approach B (automated):** CDP-based browser automation.

```python
# create_and_delete.py (CDP-based)
# 1. Open Chrome with CDP
# 2. Upload 10 images to create 10 galleries → record IDs
# 3. Delete them via UI
# 4. Save: {"gid": "...", "label": "KNOWN_DELETED"}
```

**Fallback:** Use galleries from `upload_dataset.csv` that are known-dead (404 on SESSIONKEY). Less reliable — might be "never existed" from another account.

#### 1.1c. KNOWN_NEVER (1000 random IDs)
```python
# generate_never.py
# Generate 1000 random 7-char base62 IDs
# Verify NONE exist via SESSIONKEY (just in case)
# Save: {"gid": "...", "label": "KNOWN_NEVER"}
```

**Time:** ~10 sec

#### 1.1d. KNOWN_FOREIGN (IDs found by scanner)
**Input:** `found_counter_scan.txt` (505 lines) + `bv68s9M`, `wjBbsRH`  
**Verification:** SESSIONKEY check  
**Output:** `oracle_testset/foreign_verified.jsonl`

### 1.2. Multi-IP Probe

```python
# oracle_probe.py
"""
For each ID in the test set, probe via multiple AdGuard locations.
For each probe, record:
  - gid, label
  - timestamp
  - adguard_location, ip
  - http_code, content_length, raw_body_sha256
  - cookies_present
  - cf-ray, cf-cache-status (if present in response headers)

Probe each ID through 5 different locations:
  - Europe (e.g. Stockholm)
  - Europe (e.g. Frankfurt)
  - Asia (e.g. Singapore)
  - Americas (e.g. New York)
  - Direct (no proxy)

Output: oracle_probe_results.jsonl
Each line: {"gid": "...", "label": "KNOWN_EXISTS", "location": "stockholm",
             "ts": "...", "http": 404, "size": 28099, "sha256": "...",
             "cf_ray": "...", "cookies": ["GUESTKEY=..."]}
"""
```

**Important constraints:**
- One location at a time (single-factor principle)
- Same User-Agent for all probes
- Record exact time for time-of-day analysis
- Probe all test-set IDs through location A, then switch to B

**Estimated probes:** 1250 IDs × 5 locations = 6250 probes  
**Estimated time:** 6250 probes ÷ 0.5 rps = ~3.5 hours

### 1.3. Confusion Matrix Builder

```python
# build_confusion.py
"""
Input: oracle_probe_results.jsonl
Output: confusion_matrix.json, metrics.json

For each oracle threshold (size > 28073 → EXISTS_BANNED):
  - Per-location confusion matrix
  - Overall confusion matrix
  - Precision, Recall, F1 per location
  - False Positive Rate, False Negative Rate

Also output:
  - Size distribution per label (histogram)
  - ROC curve (varying threshold)
  - Stability matrix: does the same ID give the same size across locations?
"""
```

### 1.4. Oracle Stability Analysis

```python
# oracle_stability.py
"""
Questions to answer:
1. Does the same ID return the same size across 5 locations? → stability_score
2. Does the same ID return the same size at different times? → time_stability
3. Are there IDs that flip between size classes? → flip_list
4. What's the distribution of sizes within each label? → size_histograms
5. Can we find a threshold that perfectly separates classes? → optimal_threshold
"""
```

---

## Stage 2: Statistical ID Analysis (HIGH)

**Goal:** Prove or disprove specific statistical properties of ID generation.

### 2.1. Position Independence

```python
# analyze_positions.py
"""
Chi-square test for independence of each position pair (i,j):
  H0: symbols at pos[i] independent of symbols at pos[j]
  
For each pair (21 pairs for 7 positions):
  - Build 62×62 contingency table
  - Compute χ², p-value
  - Bonferroni correction (21 tests)
  - Output significant dependencies

Also: mutual information between each position pair.
"""
```

### 2.2. N-gram Analysis

```python
# analyze_ngrams.py
"""
Bigram analysis (adjacent positions):
  - Observed bigram frequencies vs expected (uniform)
  - χ² test for each bigram position
  - Top over/under-represented bigrams

Trigram analysis:
  - Same as bigram but for 3 consecutive chars

Same-symbol analysis:
  - Do IDs ever repeat symbols? (e.g. "aa" at pos 0-1)
  - Expected vs observed
"""
```

### 2.3. Autocorrelation

```python
# analyze_autocorr.py
"""
Sort IDs by creation timestamp (from upload_intel.jsonl).
For lag k = 1,2,3,...:
  - Compute autocorrelation of base62→int sequence
  - Compute autocorrelation of per-position symbol sequence

If PRNG is truly random: autocorrelation → 0 for all lags > 0.
If sequential counter: high autocorrelation for small lags.
"""
```

### 2.4. Prefix/Suffix Decomposition

```python
# analyze_prefix_suffix.py
"""
Treat each ID as two parts:
  - Prefix (positions 0-4): 5 chars, 62^5 = 916M space
  - Suffix (positions 5-6): 2 chars, 62^2 = 3844 space

Questions:
1. Are prefix and suffix independent? (χ² test)
2. Does the same prefix appear across different sessions?
3. Does suffix distribution differ by prefix?
4. Is there a checksum relationship between prefix→suffix?
"""
```

### 2.5. NIST Battery (if justified by 2.1-2.4)

```python
# nist_tests.py
"""
Convert IDs to binary representation (6 bits per char = 42 bits).
Run selected NIST SP 800-22 tests:
  - Frequency (monobit)
  - Frequency within a block
  - Runs
  - Longest run of ones
  - DFT (spectral)
  
Only run if basic tests (2.1-2.4) show interesting patterns.
"""
```

---

## Stage 3: Session Locality (MEDIUM)

**Goal:** Determine if sequential IDs exist within a single browser session.

### 3.1. Bulk Gallery Creation

**Challenge:** Browser automation for upload.

```python
# create_batch.py (CDP-based)
"""
1. Open Chrome with CDP, navigate to postimg.cc
2. Authenticate (GUESTKEY from cookie)
3. Loop 50 times:
   a. Upload 1 image → record gallery ID
   b. Sleep 2 seconds
4. Save all IDs with timestamps
"""
```

**Alternative (manual):** Create 50 galleries in browser, copy IDs from devtools network tab.

### 3.2. Gap Analysis

```python
# analyze_gaps.py
"""
Input: batch IDs sorted by creation order
Output: gap distribution

Questions:
1. Is gap=1 consistent within a session? (we already saw it)
2. Does the gap pattern change over time?
3. Is the gap distribution uniform or clustered?
4. Are there "bursts" of close IDs?
"""
```

### 3.3. Foreign Session Comparison

```python
# compare_sessions.py
"""
Use SESSIONKEY to read galleries from different accounts.
Build gap distributions for:
  - Our account (known creation order)
  - Foreign account A (unknown order, sort by server time)
  - Foreign account B

Test: is the gap distribution the same across accounts?
"""
```

### 3.4. Conditional Brute-Force (only if gap pattern confirmed)

```python
# session_brute.py
"""
If gap=1 pattern is confirmed within a session:
  1. Create new session
  2. Create 5 seed galleries → record IDs
  3. For each seed, brute-force ±10 in the ID space
  4. Expected: find other users' galleries near our seeds
"""
```

---

## Stage 4: Arithmetic Patterns (LOW)

**Goal:** Check if bv68s9M finding is random or follows a pattern.

```python
# pattern_check.py
"""
Target: bv68s9M (found at seed+18 from bv68s94)
Probes:
  1. bv68s94 + 36 = ?
  2. bv68s94 + 54 = ?
  3. bv68s94 + 72 = ?
  4. bv68s94 + 18×N for N=1..10
  5. Gray code neighbors of bv68s9M
  6. Single-bit-flip neighbors (42 bits → 42 probes)

Output: which IDs exist, which don't.
"""
```

**Total probes:** ~100  
**Time:** ~3 minutes

---

## Execution Order

```
Week 1:
  Day 1: Stage 1.1 (test set preparation) — most can be done offline
  Day 2: Stage 1.2 (multi-IP probe) — runs in background ~3.5h
  Day 3: Stage 1.3-1.4 (confusion matrix + stability)
  Day 4: Stage 2 (statistical analysis) — all offline
  Day 5: Stage 4 (arithmetic patterns) — quick, 3 min

Week 2:
  Day 1-2: Stage 3 (session locality) — depends on browser automation setup
  Day 3: Stage 3 analysis + report update
```

## Dependencies

| Stage | Needs | Blocker? |
|-------|-------|----------|
| 1.1a | SESSIONKEY (we have it) | No |
| 1.1b | Browser automation OR manual | **Yes** — CDP setup needed |
| 1.1c | Nothing | No |
| 1.1d | SESSIONKEY | No |
| 1.2 | AdGuard pool (60 ports) | **Yes** — pool needs to be alive |
| 1.3-1.4 | Results from 1.2 | No (waiting) |
| 2 | numpy/scipy (both available) | No |
| 3 | Browser automation | **Yes** — same as 1.1b |
| 4 | AdGuard pool | **Yes** — same as 1.2 |

## Key Files to Create

```
oracle_validator/
  verify_anchors.py       — Stage 1.1a: verify known existing
  generate_never.py       — Stage 1.1c: generate known-never set
  verify_foreign.py       — Stage 1.1d: verify foreign finds
  oracle_probe.py         — Stage 1.2: multi-IP probing
  build_confusion.py      — Stage 1.3: confusion matrix
  oracle_stability.py     — Stage 1.4: stability analysis

stat_analysis/
  analyze_positions.py    — Stage 2.1: position independence
  analyze_ngrams.py       — Stage 2.2: n-gram analysis
  analyze_autocorr.py     — Stage 2.3: autocorrelation
  analyze_prefix.py       — Stage 2.4: prefix/suffix decomposition
  nist_tests.py           — Stage 2.5: NIST battery

session_locality/
  create_batch.py         — Stage 3.1: bulk gallery creation
  analyze_gaps.py         — Stage 3.2: gap analysis
  compare_sessions.py     — Stage 3.3: foreign session comparison
  session_brute.py        — Stage 3.4: conditional brute-force

pattern_check/
  pattern_check.py        — Stage 4: arithmetic patterns

oracle_testset/           — Data directory
  exists_verified.jsonl
  deleted_verified.jsonl
  never_generated.jsonl
  foreign_verified.jsonl
  probe_results.jsonl
  confusion_matrix.json
  metrics.json
```
