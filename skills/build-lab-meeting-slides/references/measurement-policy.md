# GPU measurement and evidence policy

## Device contract

All work on `ssh l40s-yunm` uses physical Device 3, the fourth GPU:

```bash
CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES=3
```

Inside the CUDA process this physical GPU appears as logical device 0. Never relabel a logical-device-0 result as physical GPU 0.

Use `scripts/lab_slides.py gpu` so the wrapper:

- queries physical index 3 before the run;
- verifies the expected GPU name and optional UUID;
- aborts when a compute process is already using that device;
- injects the two required environment variables;
- records command, device identity, stdout/stderr, and exit status;
- queries physical index 3 again after the run.

Live execution also requires `measurements.enabled: true` in `labdeck.json`. Leave it false when the deck must use existing evidence only. A dry run is permitted while disabled so the command can be reviewed safely.

Do not use the other GPUs to accelerate independent runs.

## Separate protocols

### CUDA EVENTS

Use CUDA events for primary kernel or end-to-end timing. Record warmups, sample count, fresh-process count, statistic, workload, data type, stream behavior, and included transfers/launches.

For the worked reduction protocol, the accepted scope was three fresh processes, ten warmups, 101 timed samples per process, and the median of process medians. The prefix-sum run used twenty warmups and 101 samples. These are examples, not universal constants; encode the actual protocol in the run manifest and slide notes.

### NSIGHT COMPUTE

Collect kernel counters in a separate profiling run. Use them for mechanism attribution: instructions, branches, wavefronts, sectors, occupancy, stall metrics, or throughput. Do not use Nsight Compute replay duration as the primary timing distribution.

### NSIGHT SYSTEMS

Collect a separate timeline/pass trace when phase decomposition matters. Use it to localize a regression to a pass, launch, transfer, or synchronization interval. It does not replace the CUDA-event timing protocol.

## Claims ledger

All visible numbers belong in `content/claims.json`. Required fields:

```json
{
  "id": "k7-physical-dram-sectors",
  "value": 78.488,
  "unit": "million sectors",
  "kind": "nsight-compute",
  "run_id": "reduction-device3-2026-07-14",
  "source": "evidence/normalized/first-pass.csv",
  "allowed_use": "mechanism",
  "slide_label": "NSIGHT COMPUTE",
  "notes": "first pass only"
}
```

Allowed kinds and labels:

| kind | visible label | allowed use |
|---|---|---|
| `cuda-events` | `CUDA EVENTS` | primary elapsed time |
| `nsight-compute` | `NSIGHT COMPUTE` | mechanism attribution |
| `nsight-systems` | `NSIGHT SYSTEMS` | pass/timeline decomposition |
| `derived` | `DERIVED` | speedup, logical payload/time |
| `code-derived` | `CODE-DERIVED` | blocks, barriers, structural work |
| `vendor-spec` | `VENDOR SPEC` | documented hardware characteristic |
| `inference` | `INFERENCE` | bounded causal interpretation |

Effective GB/s must say whether it is logical payload divided by elapsed time. Do not describe it as physical DRAM bandwidth unless a matching counter actually measures that quantity.

## Reuse and comparability

Evidence is reusable only when these match:

- GPU name, physical index, and UUID;
- driver, CUDA, compiler, architecture, and profiler versions;
- source and binary SHA-256;
- build flags;
- workload size, initialization, precision, semantics, and output validation;
- warmups, sample count, process count, statistic, and included work.

Controls must be labeled as controls, especially when size, cache state, grid cap, or pass scope differs. Do not place a 64 MiB warm-cache control next to a 2 GiB result without an explicit scope label.

## Interpretation boundaries

- A plateau can be real even when instructions or barriers fall. Verify whether the optimized resource is still on the critical path.
- A regression must be isolated with a counterfactual when possible: capped versus uncapped grid, padded versus unpadded storage, first pass versus tail.
- Counters can localize extra work without proving one unique physical cause. For example, extra DRAM service plus lower occupancy does not uniquely prove DRAM row-locality or burst aggregation.
- Validate matched inclusive/exclusive semantics before timing a scan. A faster result with different semantics is not an optimization.

## Hardware-fact precision

Distinguish architectural maximum, default per-block limit, and measured attribute. In the worked L40S deck, roughly 100 KB is the shared-memory capacity per SM, while 48 KB is the traditional/default per-block allocation limit; larger dynamic allocations require the appropriate opt-in mechanism. Verify the current device and CUDA documentation before repeating any number in a future deck.
