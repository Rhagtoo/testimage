# Report: ID Generator Analysis — Session 2026-07-13

## Experiments Performed

### 1. Two-browser session namespace test
- Chrome vs Chrome Incognito: 25 IDs each, interleaved generation
- **Result: ZERO prefix-5 overlap (0/42)** — sessions are independent namespaces

### 2. 1000-ID single-session deep analysis
- 1000 consecutive IDs from one browser session, 5.1 IDs/s over 195s
- Full battery: MI, bit distribution, gap histogram, autocorrelation, LCG/XOR-shift tests

### 3. Temporal prefix evolution
- Split 1000 IDs into windows: [0-100], [400-500], [900-999]
- **Result: First∩Mid=0, Mid∩Last=0, First∩Last=0** — prefix space drifts continuously

---

## Key Findings

### Mutual Information (MI) convergence
```
N=50:   4.35 bits
N=100:  4.27 bits
N=200:  3.60 bits
N=300:  3.36 bits
N=500:  2.87 bits
N=700:  2.55 bits
N=1000: 2.12 bits
```
Decaying slower than 1/√N → likely stabilizes at non-zero (est. 1.0-1.5 bits).
Consistent gradient across positions: MI(0,1)=2.12 > MI(5,6)=1.65.

### Bit-level analysis
- bit0=61.2%, bit40=38.9%, bit41=36.7% (vs 50% expected for full 42-bit range)
- BUT: range is [0, 62^7) = 80.1% of 2^42, so bit41 expected=37.6% — matches observation
- bit0 bias (61.2% vs 50%) UNEXPLAINED by range truncation
- All standard tests PASS: serial correlation, runs, median

### Generator structure
- ❌ NOT LCG (no constant multiplier across 1000 states)
- ❌ NOT XOR-shift  
- ❌ NOT 32-bit (all 42 bits used)
- ❌ NOT order-2 linear recurrence
- ✅ Passes: serial correlation (484+/515-), runs test, median (506:494)
- ✅ Diff mod odd primes = 100% coverage

### Prefix-6 clustering
- 19.1% of IDs share prefix-6 with another ID
- Gap distribution: mean=4.7, std=4.5, peak at gap=1 (26 occurrences)
- Broad gap histogram (1-24), no periodic structure

---

## Remaining Hypotheses (none falsified yet)

1. **Feistel/PRP** — reversible permutation with N rounds, session key
2. **Seeded fast hash** — HASH(session_seed || counter) with non-crypto hash (xxHash/SipHash/Murmur)
3. **State machine** — evolving internal state with output function

All three are consistent with:
- Session-specific namespace
- Continuous prefix drift
- Good statistical properties
- No recoverable LCG/XOR-shift structure
- Non-zero MI convergence

---

## Recommended Next Experiments (by priority)

1. **MI convergence to 5000** — does it stabilize or hit zero?
2. **Multiple sessions × different rates** — 1 ID/s vs 10 ID/s vs 50 ID/s — does clustering change?
3. **Interleaved generation** — A,B,A,B... from two sessions simultaneously, check for cross-session structure
4. **Bit-bias correction** — compute exact expected bit distribution for uniform [0, 62^7) analytically

## Practical Outcome

Generator NOT reverse-engineerable without session seed. For discovery purposes: search engine scraping is the viable path. DuckDuckGo confirmed to index target galleries (6 foreign IDs recovered in 1 minute).
