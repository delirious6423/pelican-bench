# Every seed: score, time and token headroom

[Methods](METHODS.md) · [CSV](data/sweep-1-cells.csv) · [Gallery](GALLERY.md)

Each cell: blind grade (1-10) / wall time (m:ss). **T** = truncated (token ceiling 49152 or 2700 s deadline). Models sorted by high-effort mean.

### high effort

| model | mean | seed 11 | seed 22 | seed 33 | seed 44 | seed 55 | median time | truncated |
|---|---|---|---|---|---|---|---|---|
| Gyro-M TQ2_0 | 8.8 | 9 / 9:18 | 7 / 4:17 | 8 / 6:37 | 10 / 8:50 | 10 / 2:10 | 6:37 | 0/5 |
| turboderp EXL3 2.68bpw h6 | 8.4 | 8 / 15:40 | 8 / 16:41 | 8 / 8:42 | 10 / 16:35 | 8 / 13:50 | 15:40 | 0/5 |
| EXL3-SC 3.05bpw h8 | 8.0 | 8 / 12:01 | 8 / 10:24 | 7 / 11:03 | 9 / 5:59 | 8 / 12:54 | 11:03 | 0/5 |
| EXL3-SC 2.05bpw h8 | 7.8 | 8 / 10:43 | 8 / 16:53 | 8 / 19:48 | 6 / 8:27 | 9 / 8:50 | 10:43 | 0/5 |
| GGUF IQ3_S | 6.0 | 8 / 6:59 | 6 / 9:02 | 1 / 12:05 **T** | 8 / 5:18 | 7 / 7:35 | 7:35 | 1/5 |
| Gyro-S TQ1_0 | 5.4 | 8 / 2:30 | 2 / 4:24 | 6 / 4:33 | 3 / 3:57 | 8 / 1:58 | 3:57 | 0/5 |
| turboderp EXL3 2.18bpw h6 | 4.6 | 3 / 9:17 | 3 / 4:35 | 3 / 10:18 | 6 / 11:28 | 8 / 13:02 | 10:18 | 0/5 |
| GGUF IQ3_XXS | 3.0 | 3 / 4:12 | 6 / 4:27 | 1 / 3:23 | 4 / 3:58 | 1 / 5:21 | 4:12 | 0/5 |

### medium effort

| model | mean | seed 11 | seed 22 | seed 33 | seed 44 | seed 55 | median time | truncated |
|---|---|---|---|---|---|---|---|---|
| Gyro-M TQ2_0 | 2.8 | 3 / 0:41 | 5 / 0:57 | 1 / 0:32 | 2 / 0:29 | 3 / 0:42 | 0:41 | 0/5 |
| turboderp EXL3 2.68bpw h6 | 4.0 | 2 / 0:49 | 4 / 1:28 | 6 / 2:47 | 3 / 2:56 | 5 / 1:09 | 1:28 | 0/5 |
| EXL3-SC 3.05bpw h8 | 3.8 | 3 / 1:38 | 3 / 2:01 | 6 / 2:12 | 6 / 1:13 | 1 / 2:33 | 2:01 | 0/5 |
| EXL3-SC 2.05bpw h8 | 3.4 | 5 / 1:18 | 4 / 1:24 | 4 / 1:48 | 1 / 0:55 | 3 / 1:38 | 1:24 | 0/5 |
| GGUF IQ3_S | 4.8 | 5 / 0:55 | 5 / 0:54 | 7 / 1:05 | 1 / 1:02 | 6 / 0:33 | 0:55 | 0/5 |
| Gyro-S TQ1_0 | 2.4 | 4 / 0:26 | 1 / 0:30 | 4 / 0:41 | 1 / 0:44 | 2 / 0:45 | 0:41 | 0/5 |
| turboderp EXL3 2.18bpw h6 | 4.4 | 6 / 1:38 | 3 / 2:16 | 6 / 2:02 | 3 / 1:01 | 4 / 2:15 | 2:02 | 0/5 |
| GGUF IQ3_XXS | 3.4 | 3 / 0:30 | 2 / 0:31 | 5 / 0:35 | 3 / 0:46 | 4 / 0:41 | 0:35 | 0/5 |

### low effort

| model | mean | seed 11 | seed 22 | seed 33 | seed 44 | seed 55 | median time | truncated |
|---|---|---|---|---|---|---|---|---|
| Gyro-M TQ2_0 | 3.2 | 1 / 0:42 | 1 / 0:36 | 4 / 0:39 | 3 / 0:33 | 7 / 0:46 | 0:39 | 0/5 |
| turboderp EXL3 2.68bpw h6 | 3.2 | 2 / 1:28 | 3 / 2:17 | 4 / 1:26 | 6 / 1:42 | 1 / 1:34 | 1:34 | 0/5 |
| EXL3-SC 3.05bpw h8 | 5.2 | 4 / 1:21 | 7 / 1:26 | 5 / 2:00 | 5 / 1:16 | 5 / 1:21 | 1:21 | 0/5 |
| EXL3-SC 2.05bpw h8 | 2.8 | 1 / 1:07 | 4 / 1:07 | 2 / 0:53 | 3 / 0:58 | 4 / 0:57 | 0:58 | 0/5 |
| GGUF IQ3_S | 2.6 | 3 / 0:37 | 1 / 0:39 | 4 / 0:45 | 4 / 0:57 | 1 / 0:33 | 0:39 | 0/5 |
| Gyro-S TQ1_0 | 3.4 | 1 / 0:29 | 6 / 0:42 | 4 / 0:31 | 1 / 0:27 | 5 / 0:34 | 0:31 | 0/5 |
| turboderp EXL3 2.18bpw h6 | 3.8 | 3 / 1:57 | 4 / 1:13 | 3 / 2:09 | 6 / 1:42 | 3 / 1:26 | 1:42 | 0/5 |
| GGUF IQ3_XXS | 2.8 | 1 / 0:30 | 2 / 0:46 | 4 / 0:37 | 5 / 0:54 | 2 / 0:36 | 0:37 | 0/5 |

### Token headroom

Most tokens one run used (reasoning + answer) against the ceiling of 49152.

| model | low | medium | high |
|---|---|---|---|
| Gyro-M TQ2_0 | 3239 | 3900 | 33842 |
| turboderp EXL3 2.68bpw h6 | 5000 | 6054 | 31573 |
| EXL3-SC 3.05bpw h8 | 4288 | 5438 | 24426 |
| EXL3-SC 2.05bpw h8 | 3152 | 4895 | 48196 |
| GGUF IQ3_S | 4713 | 5021 | 49152 |
| Gyro-S TQ1_0 | 4016 | 3962 | 23215 |
| turboderp EXL3 2.18bpw h6 | 5407 | 5293 | 27800 |
| GGUF IQ3_XXS | 5231 | 4068 | 24409 |

Total: 1/120 truncated (GGUF IQ3_S high seed 33: length at 49152 tokens, 12:05).
