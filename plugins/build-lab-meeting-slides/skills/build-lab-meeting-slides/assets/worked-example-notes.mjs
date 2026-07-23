import path from "node:path";
import fs from "node:fs/promises";
import { FileBlob, PresentationFile } from "@oai/artifact-tool";

const inputPath = process.argv[2];
const outputPath = process.argv[3];
if (!inputPath || !outputPath) {
  throw new Error("usage: node add-detailed-speaker-notes.mjs <input.pptx> <output.pptx>");
}

const notes = [
  `Today we will use CUDA sum reduction as a controlled optimization story on an NVIDIA L40S. We begin with a source-inspired shared-memory kernel and change one mechanism at a time: lane assignment, shared-memory access, input pre-addition, warp shuffles, unrolling, and grid sizing. Every K1–K7 kernel in the main reduction ladder uses the same 2 GiB input; controls and scan runs are labeled separately. All measurements use physical Device 3.

The goal is not to memorize K1 through K7. It is to connect each code change to the hardware work it removes, then verify whether that work was actually on the critical path. After reduction, we will reuse the same reasoning for prefix sum, warp primitives, Tensor Cores, and CUDA libraries. First, let us establish the hardware limits behind the measurements.`,

  `Keep three hardware scales in mind. Device memory is the global transport limit: the L40S has 48 GB of GDDR6 and 864 GB/s of rated bandwidth. The chip has 142 SMs. One SM can hold up to 48 resident warps, or 1,536 threads, and its four warp schedulers choose among ready warps. A warp contains 32 lanes issuing a common instruction; shared memory provides a 100 KB, 32-bank staging area, while shuffle instructions exchange register values inside a warp.

These capacities explain the later traffic, residency, and communication effects, but they do not guarantee a particular speed. Reduction timings use three fresh Device-3 processes, each with 10 warmups and 101 CUDA-event samples; scan uses 20 warmups and 101 samples. Nsight Compute first-pass counters and Nsight Systems pass traces are collected in separate profiling runs, so they are evidence about mechanism rather than samples in the timing distribution. With that scope fixed, we can examine the baseline reduction tree.`,

  `This eight-value example shows the complete block-local reduction mechanism. In stage one, adjacent values combine into four partials: 4, 7, 5, and 9. A block barrier ensures that every partial is visible before stage two reads it. Stage two produces 11 and 14, followed by another barrier; stage three combines those values into the final block sum, 25.

For the power-of-two B used here, each level halves the number of live values, so the tile needs log₂B synchronized levels and B−1 additions. The additions are inexpensive, but every level also performs shared-memory accesses and forces the participating warps to rendezvous. This is the on-chip tree that the later kernels progressively streamline. Before changing it, however, we need to compare its work with the much larger cost of bringing the input from DRAM.`,

  `For N FP32 inputs, reduction performs approximately N additions while reading about 4N bytes from global memory. Ignoring the tiny partial-sum output, that is roughly one addition per four bytes, or 0.25 addition per byte. This is the global arithmetic intensity that makes the 2 GiB workload fundamentally bandwidth-oriented.

The 12-byte callout describes a different layer: one naïve shared-tree update reads two four-byte values, writes one four-byte result, and then reaches a block barrier. That traffic occurs in shared memory, not DRAM. We can still improve performance by removing waste from lane selection, shared-memory service, or repeated trees, but only until those costs stop delaying the global input stream. Once DRAM becomes the critical path, further on-chip savings can disappear from elapsed time. Next, we need to see how block-local results are combined across the grid.`,

  `__syncthreads() coordinates threads only inside one block; it cannot make every block in the grid stop at a common point. Therefore, each block completes its local tree, writes one partial sum to global memory, and terminates. A second kernel reduces those partials, and this process repeats until only one value remains.

For the 2 GiB K1–K6 measurements, the complete reduction requires four kernel launches. The later launches process progressively smaller partial arrays, so “four launches” does not mean reading the original 2 GiB four times. It describes the required hierarchy from many independent blocks to one final result. This multi-kernel structure is held constant across the comparable kernels, allowing us to attribute timing changes mainly to the first-pass block implementation. We can now establish K1, the interleaved-addressing baseline against which every subsequent change will be compared.`,

  `At the first K1 stage, s equals 1, so the modulo predicate selects lanes 0, 2, 4, and 6 to update destinations 0, 2, 4, and 6. The odd lanes remain inactive. A warp still issues the instruction as a group, but only the selected lanes perform useful additions; later stages make the active pattern even sparser.

The 47 predicated warp add-paths per block come from the kernel’s fixed control structure. Nsight Compute separately reports 4.188 billion first-pass instructions and 63.59% of peak-sustained DRAM throughput. CUDA-event timing gives 4.501504 milliseconds for the complete 2 GiB reduction. The reported 477 effective GB/s is derived from logical input bytes divided by that end-to-end time, not from a physical DRAM-traffic counter. Together, the code count and Compute profile show that on-chip control and addressing overhead prevent K1 from fully driving memory. K2 keeps the arithmetic identical but changes which lanes execute it.`,

  `K2 preserves the same first-stage pairs and destinations: values 0 and 1 still accumulate at destination 0, values 2 and 3 at destination 2, and so on. The difference is the execution mapping. Lanes 0 through 3 now compute indices 0, 2, 4, and 6, so useful work occupies contiguous lanes instead of every other lane. This is why the diagram retains the K1 destinations while moving the active arrows.

The arithmetic result and global input traffic do not change. The code-derived warp add-path count falls from 47 to 12 per block. Nsight Compute reports 51.68% fewer first-pass instructions and peak-sustained DRAM utilization rising from 63.59% to 88.20%. Separate CUDA-event timing drops from 4.501504 to 3.101696 milliseconds: a 31.10% reduction, or 1.451× speedup. The source labels on the slide separate those evidence types deliberately. Removing sparse-lane execution waste lets the kernel feed memory more effectively. K3 now keeps these compact lanes and reorganizes shared-memory destinations.`,

  `K3 switches to sequential addressing. The active execution lanes remain 0 through 3, so this slide should be compared directly with K2: the lane row is unchanged. What moves is the destination row, from the spaced locations s[0], s[2], s[4], and s[6] to the contiguous run s[0] through s[3]. Subsequent stages continue reducing the front of the shared array.

Nsight Compute reports that the shared-load wavefront count falls from 201.6 million to 52.5 million while global DRAM traffic remains unchanged. This confirms that the shared-memory request pattern becomes much cheaper to service. Separate CUDA-event timing improves only from 3.101696 to 3.013632 milliseconds—2.84%, or 1.029×—because K2 was already driving the global-memory path much more effectively. The optimization works exactly where intended, but that resource is no longer the dominant limit. K4 therefore attacks the number of block trees rather than merely refining one tree.`,

  `K4 performs the first addition while loading from global memory. Each thread reads g[i] and g[i+B], adds them in a register, and writes one value to s[tid]. From that point onward, the shared-memory tree is the same K3 tree; only the load band changes. Because one block now covers 2B input values instead of B, the first-pass block count is halved.

The total input stream is not halved—every FP32 value is still read once—so a twofold speedup would not be expected. The 50% block-count reduction follows from the launch geometry. Nsight Compute reports 45.96% fewer first-pass instructions and peak-sustained DRAM utilization rising from 90.40% to 97.44%. Separate CUDA-event timing improves from 3.013632 to 2.741248 milliseconds: 9.04%, or 1.099×. Half of the block setup, tree, and partial-output work disappears, but the unchanged input stream limits the gain. K4 has now nearly saturated the remaining memory path, preparing us for the K5 and K6 plateau.`,

  `Compare only the tail of K4 and K5. K4 keeps the last reductions in shared memory: each stage reads two locations, writes the result, and then waits at a block-wide barrier. K5 preserves the same addition order, including the shared-memory plus-32 handoff, but exchanges the remaining values inside one warp with shuffle operations at offsets 16, 8, 4, 2, and 1.

Nsight Compute reports 50.22% fewer first-pass instructions, while inspection of the kernel structure gives the tree-barrier reduction from eight to two. Separate CUDA-event timing changes from 2.741248 to 2.740224 milliseconds—about one microsecond, so this is a tie. The same Compute profile places K4 at 97.44% of peak-sustained DRAM throughput. The reason is workload compatibility: removing more on-chip work does not shorten the 2 GiB DRAM critical path.`,

  `K6 applies a smaller, more mechanical optimization. Because the block size is fixed at 256 threads, the upper tree always contains exactly the plus-128 and plus-64 stages. K6 writes those stages explicitly instead of evaluating a runtime loop condition and branch. The picture on the right is intentionally unchanged: the same lanes combine, the same shared-memory addresses are accessed, and the same two block barriers remain.

Nsight Compute reports 32.96% fewer first-pass instructions and 70.59% fewer branches, but the code comparison shows that the data movement and two synchronization points remain unchanged. Separate end-to-end CUDA-event timing moves from 2.740224 to 2.741120 milliseconds, a 0.9-microsecond difference within a tie. Unrolling succeeded at removing control overhead; that overhead simply was not the active bottleneck.`,

  `This slide verifies the K5/K6 plateau rather than merely labeling it. The end-to-end CUDA-event medians remain approximately 2.74 milliseconds for K4, K5, and K6. Meanwhile, Nsight Compute first-pass instruction counts fall from 939.5 million to 467.7 million and then 313.5 million, branch counts fall from 84.9 to 35.7 to 10.5 million, and the code-level tree-barrier count falls from eight to two.

Nsight Compute also places K4 at 97.44% of peak-sustained DRAM throughput; the 2 GiB working set is far larger than the L40S’s 96 MiB L2. A separate 64 MiB warm-cache CUDA-event control improves from 54.0 to 32.8 to 31.7 microseconds. Its absolute time is not directly comparable to the 2 GiB run, but it supports the diagnosis: on-chip optimizations become visible when DRAM is no longer the critical path.`,

  `K7 changes the assignment geometry much more aggressively. K6 launches 1,048,576 first-pass blocks, and each thread reads one local pair before contributing to its block reduction. K7 caps the grid at 568 blocks—142 SMs times four blocks per SM—and reuses those blocks. In each loop iteration, a thread reads four two-value streams separated by G, accumulates them, and advances by 4G. With only 568 blocks covering the full 2 GiB input, each thread processes roughly 3,692 values.

The logical input work is unchanged. End-to-end CUDA-event timing increases from 2.741120 to 2.975744 milliseconds, or 8.56%. In a separate profiling run, Nsight Compute reports that first-pass physical DRAM-read sectors rise from 71.767 to 78.488 million while logical sectors remain 67.109 million. The no-cap time shown in the result band is another CUDA-event control, not a Compute counter. The next slide uses those controls to determine whether the increase comes from the grid cap or from grid-stride code itself.`,

  `The control experiment isolates the cap. Nsight Compute reports the same 67.109 million logical first-pass sectors for K6 and capped K7, but physical DRAM-read sectors rise 9.37%, from 71.767 to 78.488 million. It also reports occupancy falling from 91.22% to 66.52% and the long-scoreboard-stall metric rising from 75.78% to 97.91%. Separate CUDA-event timing rises by 8.56%.

Nsight Systems places almost all of the regression in the first pass: plus 231.748 microseconds, while the tail becomes 6.432 microseconds shorter. For the no-cap control, Nsight Compute returns to 91.25% occupancy and 71.731 million DRAM sectors, while separate CUDA-event timing returns to 2.741248 milliseconds. This identifies the aggressive cap as the cause, but the counters do not prove one unique DRAM-row or burst-level mechanism.`,

  `Read the ladder as a sequence of changing bottlenecks. CUDA-event timing gives 4.502, 3.102, 3.014, 2.741, 2.740, 2.741, and 2.976 milliseconds across K1 through K7. The percentage changes and effective-bandwidth labels are derived from those end-to-end times; effective GB/s means logical input bytes divided by time, not physical DRAM traffic.

Nsight Compute supplies the first-pass shared-memory, instruction, throughput, and physical-DRAM evidence used to explain the changes. K1 to K2 removes control waste; K3 improves shared-memory service; K4 reaches the DRAM plateau; K5 and K6 remove work below that plateau; capped K7 adds physical DRAM service. Those Compute profiles are separate from the timing samples. The general rule carries into scan: an optimization matters only when it shortens the workload’s current critical path.`,

  `Reduction and scan use the same operator, addition here, but promise different outputs. Reduction may discard intermediate values because it returns only the final total, 25. An inclusive scan returns one value per position and includes the current input: 3, 4, 11, and so on, ending at 25. An exclusive scan returns the sum strictly before the current position, so it begins with the additive identity zero and is the inclusive sequence shifted right.

Algorithmically, a parallel scan requires an associative operator; exclusive scan also needs an identity or initial value. FP32 addition is only approximately associative because of rounding, so changing from serial order to a parallel tree can change low-order bits even when both executions are valid numerical sums. Scan must also preserve every prefix instead of only the final value, which creates more communication and motivates specialized prefix networks.`,

  `Kogge–Stone minimizes dependency depth by doubling how far each value can see at every stage. At distance one, indices 1 through 7 add their immediate left neighbor, producing seven combines. At distance two, indices 2 through 7 add a value representing the preceding two elements, producing six more. At distance four, indices 4 through 7 add the preceding four-element prefix, producing four more.

The dependency reach is therefore 2, then 4, then 8, so eight inputs finish in log₂8, or three, synchronized stages. Notice that every arrow reads from the previous row; mixing values updated within the same stage would violate the network’s dependencies. The short depth is attractive when synchronization is expensive, but the dense network performs 17 combines—more work than the alternative on the next slide.`,

  `Brent–Kung reaches the same inclusive result with a sparser tree. The up-sweep first forms selected partial sums: four pair combines, then two four-element combines, and finally one eight-element combine. The down-sweep reuses those stored partials to fill missing prefixes, performing one combine in the first downward stage and three in the second. For eight inputs, that totals 11 combines. Its depth is five stages, or 2log₂8−1.

Compared with Kogge–Stone, Brent–Kung saves six combines but adds two synchronization levels. That is the core trade-off: less total work versus greater dependency depth. This slide shows canonical inclusive Brent–Kung. The lecture PDF’s six-round, 14-add up/down tree instead matches the Blelloch-style exclusive construction, which inserts the identity at the root. Which network wins on a GPU therefore depends on barrier cost, residency, and available arithmetic throughput.`,

  `Here the comparison is not just Kogge–Stone versus Brent–Kung; it is how each network fits the SM. With 256-thread blocks, each block occupies eight warps, so six blocks can fill the L40S’s 48 resident-warp slots. CUDA-event medians tie the two scans at about 0.821 milliseconds, while Nsight Compute reports 88.06% active warps for this residency regime.

A 1024-thread block has 32 warps, but a second such block cannot reside, leaving 16 warp slots unused. CUDA-event medians are 0.875 milliseconds for Kogge–Stone and 1.130 milliseconds for Brent–Kung, a 1.292× gap; Nsight Compute reports about 65.7% active warps. In the padding control, Compute reports 15.4× fewer bank conflicts, while separate CUDA-event timing improves Brent–Kung by only 5.7%. The 8 MiB control values are also CUDA-event measurements. These labels matter because the counters explain the mechanism but are not timing samples.`,

  `At device scope, the prefix network is no longer the main cost; full-array traffic is. For 268,435,456 FP32 elements, the minimum logical payload is one 1 GiB input read plus one 1 GiB output write, or 8 bytes per element. CUB’s single-pass look-back scan completes that job in 3.316 milliseconds, within 1.1% of the 3.280-millisecond device-to-device copy control.

The simple custom composition first writes local prefixes, then reads that full intermediate array and writes the uniformly offset result. Ignoring the tiny block-total array, that is roughly 16 bytes per element—two full-array passes. Its Kogge–Stone and Brent–Kung versions therefore converge at 6.236 and 6.262 milliseconds, only 0.4% apart, and close to the 6.560-millisecond two-copy estimate. The 1.881× CUB advantage comes primarily from avoiding the extra global round trip, not from a faster local tree. To understand the inner primitive used inside either device algorithm, zoom back to one warp.`,

  `This is the same Kogge–Stone dependency doubling, but confined to a single warp and held in registers. Every lane starts with one value. At distances 1, 2, 4, 8, and 16, __shfl_up_sync reads the prior partial sum from the lane d positions lower; lanes whose ID is at least d add that value. After five rounds, each lane contains its inclusive prefix, and lane 31 has accumulated all 32 inputs.

The important hardware match is that the exchange is between registers of participating lanes, so a block-wide __syncthreads() is unnecessary. The mask still has to name the lanes participating in the shuffle. This primitive stops at the warp boundary, however. A block scan must combine per-warp totals through shared memory or another cooperative step, which leads to the hierarchy on the next slide.`,

  `A device scan is built by composing scopes; neither a warp shuffle nor __syncthreads() can synchronize unrelated blocks. In kernel 1, each block performs an exclusive local scan and writes its total. For block 0, inputs [1, 2, 3, 4] become [0, 1, 3, 6] with total 10; block 1 produces [0, 5, 11, 18] with total 26. Kernel 2 scans those totals to create block offsets [0, 10].

Kernel 3 adds the matching offset to every local result, so block 1 begins at 10 and the final grid result is [0, 1, 3, 6, 10, 15, 21, 28]. The red lines mark kernel boundaries: sequential kernel launches in the same stream provide the device-wide phase ordering that a block barrier cannot. This three-kernel structure is easy to reason about and validate, but its final uniform-add pass rereads and rewrites the full array, explaining the extra traffic measured on the previous slide.`,

  `Tensor Cores are not simply “faster CUDA cores”; they are a specialized execution path with a specific operation contract. The lane path on the left supports per-lane communication such as shuffle-based scan and shared-memory synchronization. The Tensor Core path accepts supported matrix tiles and performs matrix multiply-accumulate, conventionally written D = A × B + C, for particular shapes, precisions, and layouts.

The standard FP32 scan shown here does not natively match a dense MMA tile and therefore uses the CUDA-lane path. Consequently, the L40S peak of 733 dense FP8 TFLOPS is not a meaningful roof for this FP32 scan workload. Peak numbers predict performance only when the algorithm enters the matching hardware branch. This contract idea is the bridge to libraries: they are valuable partly because they can recognize and select eligible branches.`,

  `cuBLAS and cuDNN expose richer contracts than a single hand-written kernel, and that gives them room to optimize. For GEMM, cuBLAS receives dimensions, transposition, leading dimensions or layouts, input and compute types, and the alpha and beta coefficients in C = alpha op(A) op(B) + beta C. cuBLAS or cuBLASLt can then choose a tiled implementation and, when the supplied precision and layout are eligible, a Tensor Core path; cuBLASLt also supports more flexible layouts and epilogues.

cuDNN works at operation-graph scope. A sequence such as convolution, bias, and activation is described as a graph, then heuristics select an execution plan. Compatible operations may be fused, reducing launches and intermediate memory traffic, but fusion is not automatic for every graph. The key point is not “libraries are always faster”; it is that a precise contract exposes optimization choices that raw arithmetic alone does not.`,

  `The same associative reduce or scan can be requested at several scopes, and the scope determines who owns coordination. Thrust provides the broadest interface here: STL-like algorithms operate on iterator ranges or containers. CUB DeviceScan and DeviceReduce explicitly manage a whole device range. Inside a custom kernel, CUB BlockScan cooperates across one thread block, while CUB WarpScan or shuffle primitives cooperate within one warp.

Moving outward gives a simpler interface and lets the implementation manage more of the schedule; moving inward gives the programmer more control over data already in registers or shared memory and makes fusion easier. It also transfers responsibility for temporary storage, synchronization, and composition to the caller. Start with the broadest abstraction that matches the required semantics. Descend only when measurement identifies a missing capability, such as a special data layout, fused producer or consumer, or scope the higher-level call cannot express efficiently.`,

  `The final routing question is data shape plus execution scope. For a one-dimensional range reduction or scan, establish a Thrust or CUB Device baseline. For dense matrix multiplication, use cuBLAS or cuBLASLt and check whether the requested shape, precision, and layout qualify for Tensor Cores. For a DNN operation graph, give cuDNN enough graph context to choose or fuse an execution plan. Custom CUDA is the right branch when the required fusion, communication pattern, or data layout is outside those contracts—not merely because custom code feels more controllable.

Compare alternatives with the same inputs, precision, semantics, and end-to-end scope, while accounting for every required transfer; validate correctness before timing. The reduction results give the closing rule: optimize the current bottleneck. A library baseline defines a credible target; measurements tell us whether a custom kernel actually removes a cost or only rearranges work below the performance plateau. Start broad, measure, and customize only with evidence.`,
];

const presentation = await PresentationFile.importPptx(await FileBlob.load(inputPath));
if (presentation.slides.items.length !== notes.length) {
  throw new Error(`expected ${notes.length} slides, found ${presentation.slides.items.length}`);
}

for (let i = 0; i < notes.length; i += 1) {
  const speakerNotes = presentation.slides.items[i].speakerNotes;
  speakerNotes.clear();
  speakerNotes.textFrame.setText(notes[i]);
  speakerNotes.setVisible(true);
}

await fs.mkdir(path.dirname(outputPath), { recursive: true });
const pptx = await PresentationFile.exportPptx(presentation);
await pptx.save(outputPath);

const wordCounts = notes.map((note) => note.trim().split(/\s+/).length);
process.stdout.write(
  `embedded detailed transcripts on ${notes.length} slides | words=${wordCounts.reduce((a, b) => a + b, 0)} | min=${Math.min(...wordCounts)} | max=${Math.max(...wordCounts)}\n`,
);
