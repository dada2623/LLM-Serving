# 异构 PD 压测数据


性能筛选完成：69/76 单元；正式测试完成：3/最多 78 单元。验收、预热与校准不计入这两个数量。

另有 7 个单元未满足采样要求，未计入完成数量。

四种部署验收：并发隔离 4/4；14K 输入、完整 1024-token 输出 4/4。


GPU 传输校准：通过，记录 100/100 次操作。

验证、预热、筛选、正式测试分开标记；验证图不能作为正式性能结论。当前尚未覆盖的部署不填零、不外推。

| 单元 | 成功/错误 | TTFT P50 秒 | TPOT P50 秒 | 输出 token/s | 样本要求完成 |
| --- | --- | --- | --- | --- | --- |
| cells/calibration-M-code-0.5 | 20/0 | 4.741 | 0.045 | 10.01 | True |
| cells/calibration-M-code-0.8 | 20/0 | 4.846 | 0.046 | 15.74 | True |
| cells/calibration-M-code-1.0 | 20/0 | 4.783 | 0.046 | 19.44 | True |
| cells/calibration-M-code-1.2 | 20/0 | 4.777 | 0.049 | 23.06 | True |
| cells/calibration-M-interference-0.5 | 20/0 | 0.315 | 0.041 | 10.97 | True |
| cells/calibration-M-interference-0.8 | 20/0 | 0.090 | 0.043 | 17.36 | True |
| cells/calibration-M-interference-1.0 | 20/0 | 0.092 | 0.044 | 21.54 | True |
| cells/calibration-M-interference-1.2 | 20/0 | 0.094 | 0.044 | 25.68 | True |
| cells/calibration-M-interference-1.6 | 20/0 | 0.092 | 0.044 | 33.70 | True |
| cells/calibration-M-interference-2.0 | 20/0 | 0.089 | 0.044 | 41.52 | True |
| cells/calibration-N-C | 10/0 | 4.967 | 0.042 | 21.19 | True |
| cells/calibration-N-L | 10/0 | 4.948 | 0.044 | 15.87 | True |
| cells/calibration-N-M | 10/0 | 0.658 | 0.041 | 23.26 | True |
| cells/calibration-N-S | 10/0 | 0.061 | 0.041 | 24.21 | True |
| cells/formal-r1-N-C-c1 | 50/0 | 4.167 | 0.040 | 22.96 | True |
| cells/formal-r1-N-L-c1 | 50/0 | 4.169 | 0.040 | 17.95 | True |
| cells/formal-r1-N-L-c2 | 50/0 | 6.358 | 0.050 | 27.02 | True |
| cells/screen-A-C-c1 | 10/0 | 10.717 | 0.092 | 9.80 | True |
| cells/screen-A-C-c2 | 8/2 | 16.051 | 0.103 | 13.46 | True |
| cells/screen-A-C-c4 | 6/4 | 132.550 | 0.104 | 12.61 | True |
| cells/screen-A-L-c1 | 10/0 | 10.648 | 0.091 | 7.56 | True |
| cells/screen-A-L-c2 | 10/0 | 15.969 | 0.119 | 11.04 | True |
| cells/screen-A-L-c4 | 10/0 | 56.893 | 0.119 | 11.06 | True |
| cells/screen-A-M-code-r0.5 | 10/0 | 10.637 | 0.103 | 12.99 | True |
| cells/screen-A-M-code-r0.8 | 8/2 | 28.800 | 0.109 | 14.21 | True |
| cells/screen-A-M-code-r1.0 | 7/3 | 68.789 | 0.109 | 14.12 | True |
| cells/screen-A-M-code-r1.2 | 6/4 | 76.126 | 0.109 | 13.08 | True |
| cells/screen-A-M-interference-r0.5 | 10/0 | 0.226 | 0.099 | 25.80 | True |
| cells/screen-A-M-interference-r0.8 | 10/0 | 16.601 | 0.099 | 29.98 | True |
| cells/screen-A-M-interference-r1.0 | 10/0 | 26.507 | 0.099 | 30.95 | True |
| cells/screen-A-M-interference-r1.2 | 10/0 | 33.056 | 0.099 | 31.65 | True |
| cells/screen-A-S-c1 | 10/0 | 0.183 | 0.089 | 11.15 | True |
| cells/screen-A-S-c2 | 10/0 | 0.254 | 0.095 | 20.89 | True |
| cells/screen-A-S-c4 | 10/0 | 0.332 | 0.097 | 33.67 | True |
| cells/screen-AP-ND-C-c1 | 10/0 | 41.424 | 0.043 | 11.94 | True |
| cells/screen-AP-ND-C-c2 | 10/0 | 41.887 | 0.044 | 21.72 | True |
| cells/screen-AP-ND-C-c4 | 8/2 | 125.693 | 0.042 | 19.75 | True |
| cells/screen-AP-ND-L-c1 | 10/0 | 42.084 | 0.044 | 4.79 | True |
| cells/screen-AP-ND-L-c2 | 10/0 | 69.796 | 0.045 | 6.13 | True |
| cells/screen-AP-ND-L-c4 | 8/2 | 152.828 | 0.044 | 5.40 | True |
| cells/screen-AP-ND-M-code-r0.5 | 10/0 | 43.315 | 0.044 | 13.07 | True |
| cells/screen-AP-ND-M-code-r0.8 | 9/1 | 46.795 | 0.045 | 16.39 | True |
| cells/screen-AP-ND-M-code-r1.0 | 8/2 | 58.216 | 0.044 | 16.69 | True |
| cells/screen-AP-ND-M-code-r1.2 | 7/3 | 73.203 | 0.043 | 15.98 | True |
| cells/screen-AP-ND-M-interference-r0.5 | 10/0 | 6.747 | 0.046 | 29.37 | True |
| cells/screen-AP-ND-M-interference-r0.8 | 10/0 | 33.663 | 0.045 | 35.01 | True |
| cells/screen-AP-ND-M-interference-r1.0 | 10/0 | 56.354 | 0.046 | 33.63 | True |
| cells/screen-AP-ND-M-interference-r1.2 | 10/0 | 61.733 | 0.045 | 34.86 | True |
| cells/screen-AP-ND-S-c1 | 10/0 | 4.530 | 0.038 | 21.19 | True |
| cells/screen-AP-ND-S-c2 | 10/0 | 4.729 | 0.040 | 39.25 | True |
| cells/screen-AP-ND-S-c4 | 10/0 | 4.707 | 0.041 | 63.69 | True |
| cells/screen-N-C-c1 | 10/0 | 4.212 | 0.040 | 22.63 | True |
| cells/screen-N-C-c2 | 10/0 | 6.308 | 0.044 | 40.22 | True |
| cells/screen-N-C-c4 | 10/0 | 54.568 | 0.043 | 40.65 | True |
| cells/screen-N-L-c1 | 10/0 | 4.213 | 0.040 | 17.72 | True |
| cells/screen-N-L-c2 | 10/0 | 6.331 | 0.050 | 26.78 | True |
| cells/screen-N-L-c4 | 10/0 | 23.359 | 0.050 | 26.82 | True |
| cells/screen-N-M-code-r0.5 | 10/0 | 4.900 | 0.044 | 14.36 | True |
| cells/screen-N-M-code-r0.8 | 10/0 | 4.804 | 0.049 | 21.84 | True |
| cells/screen-N-M-code-r1.0 | 10/0 | 4.969 | 0.049 | 26.50 | True |
| cells/screen-N-M-code-r1.2 | 10/0 | 4.637 | 0.048 | 28.93 | True |
| cells/screen-N-M-interference-r0.5 | 10/0 | 0.088 | 0.048 | 30.19 | True |
| cells/screen-N-M-interference-r0.8 | 10/0 | 0.092 | 0.055 | 44.99 | True |
| cells/screen-N-M-interference-r1.0 | 10/0 | 0.117 | 0.051 | 50.48 | True |
| cells/screen-N-M-interference-r1.2 | 10/0 | 0.097 | 0.046 | 58.24 | True |
| cells/screen-N-S-c1 | 10/0 | 0.053 | 0.038 | 26.54 | True |
| cells/screen-N-S-c2 | 10/0 | 0.080 | 0.043 | 45.00 | True |
| cells/screen-N-S-c4 | 10/0 | 0.114 | 0.041 | 79.60 | True |
| cells/screen-NP-AD-C-c1 | 10/0 | 7.069 | 0.091 | 10.23 | True |
| cells/screen-NP-AD-C-c2 | 10/0 | 6.961 | 0.098 | 18.91 | True |
| cells/screen-NP-AD-C-c4 | 8/2 | 113.757 | 0.098 | 18.25 | True |
| cells/screen-NP-AD-L-c1 | 10/0 | 6.884 | 0.091 | 8.50 | True |
| cells/screen-NP-AD-L-c2 | 10/0 | 7.278 | 0.096 | 15.45 | True |
| cells/screen-NP-AD-L-c4 | 10/0 | 39.081 | 0.096 | 15.39 | True |
| cells/screen-NP-AD-M-code-r0.5 | 10/0 | 7.507 | 0.095 | 13.12 | True |
| cells/screen-NP-AD-M-code-r0.8 | 0/2 | — | — | 0.00 | False |
| cells/screen-NP-AD-M-code-r0.8-retry-1-run-id | 3/3 | 7.393 | 0.096 | 14.75 | False |
| cells/screen-NP-AD-M-code-r0.8-retry-2-run-id | 0/2 | — | — | 0.00 | False |
| cells/screen-NP-AD-M-code-r0.8-retry-3-run-id | 0/2 | — | — | 0.00 | False |
| cells/screen-NP-AD-M-code-r1.0 | 0/2 | — | — | 0.00 | False |
| cells/screen-NP-AD-M-code-r1.0-retry-1-run-id | 0/2 | — | — | 0.00 | False |
| cells/screen-NP-AD-M-code-r1.0-retry-2-run-id | 0/2 | — | — | 0.00 | False |
| cells/screen-NP-AD-M-code-r1.0-retry-3-run-id | 0/2 | — | — | 0.00 | False |
| cells/screen-NP-AD-M-code-r1.2 | 0/2 | — | — | 0.00 | False |
| cells/screen-NP-AD-M-code-r1.2-retry-1-run-id | 0/2 | — | — | 0.00 | False |
| cells/screen-NP-AD-M-code-r1.2-retry-2-run-id | 0/2 | — | — | 0.00 | False |
| cells/screen-NP-AD-M-code-r1.2-retry-3-run-id | 0/2 | — | — | 0.00 | False |
| cells/screen-NP-AD-M-interference-r0.5 | 0/2 | — | — | 0.00 | False |
| cells/screen-NP-AD-M-interference-r0.5-retry-1-run-id | 0/2 | — | — | 0.00 | False |
| cells/screen-NP-AD-M-interference-r0.5-retry-2-run-id | 0/2 | — | — | 0.00 | False |
| cells/screen-NP-AD-M-interference-r0.5-retry-3-run-id | 0/2 | — | — | 0.00 | False |
| cells/screen-NP-AD-M-interference-r0.8 | 0/2 | — | — | 0.00 | False |
| cells/screen-NP-AD-M-interference-r0.8-retry-1-run-id | 0/2 | — | — | 0.00 | False |
| cells/screen-NP-AD-M-interference-r0.8-retry-2-run-id | 0/2 | — | — | 0.00 | False |
| cells/screen-NP-AD-M-interference-r0.8-retry-3-run-id | 0/2 | — | — | 0.00 | False |
| cells/screen-NP-AD-M-interference-r1.0 | 0/2 | — | — | 0.00 | False |
| cells/screen-NP-AD-M-interference-r1.0-retry-1-run-id | 0/2 | — | — | 0.00 | False |
| cells/screen-NP-AD-M-interference-r1.0-retry-2-run-id | 0/2 | — | — | 0.00 | False |
| cells/screen-NP-AD-M-interference-r1.0-retry-3-run-id | 0/2 | — | — | 0.00 | False |
| cells/screen-NP-AD-M-interference-r1.2 | 0/2 | — | — | 0.00 | False |
| cells/screen-NP-AD-M-interference-r1.2-retry-1-run-id | 0/2 | — | — | 0.00 | False |
| cells/screen-NP-AD-M-interference-r1.2-retry-2-run-id | 0/2 | — | — | 0.00 | False |
| cells/screen-NP-AD-M-interference-r1.2-retry-3-run-id | 0/2 | — | — | 0.00 | False |
| cells/screen-NP-AD-S-c1 | 10/0 | 0.991 | 0.090 | 10.93 | True |
| cells/screen-NP-AD-S-c2 | 10/0 | 0.997 | 0.096 | 20.38 | True |
| cells/screen-NP-AD-S-c4 | 10/0 | 1.060 | 0.097 | 33.70 | True |
| cells/screen-REPLICAS-M-code-r0.5 | 10/0 | 5.151 | 0.067 | 13.17 | True |
| cells/screen-REPLICAS-M-code-r0.8 | 10/0 | 4.901 | 0.044 | 19.16 | True |
| cells/screen-REPLICAS-M-code-r1.0 | 10/0 | 5.354 | 0.044 | 22.53 | True |
| cells/screen-REPLICAS-M-code-r1.2 | 10/0 | 5.264 | 0.045 | 25.57 | True |
| cells/screen-REPLICAS-M-interference-r0.5 | 10/0 | 0.249 | 0.047 | 30.33 | True |
| cells/screen-REPLICAS-M-interference-r0.8 | 10/0 | 0.176 | 0.047 | 37.03 | True |
| cells/screen-REPLICAS-M-interference-r1.0 | 10/0 | 0.137 | 0.047 | 33.18 | True |
| cells/screen-REPLICAS-M-interference-r1.2 | 10/0 | 0.198 | 0.079 | 39.03 | True |
| cells/validation-concurrency-A-1 | 1/0 | 10.496 | 0.093 | 2.39 | True |
| cells/validation-concurrency-A-2 | 2/0 | 15.791 | 0.284 | 2.60 | True |
| cells/validation-concurrency-A-4 | 4/0 | 27.865 | 0.266 | 2.66 | True |
| cells/validation-concurrency-AP-ND-1 | 1/0 | 40.702 | 0.050 | 0.76 | True |
| cells/validation-concurrency-AP-ND-2 | 2/0 | 61.355 | 0.050 | 0.77 | True |
| cells/validation-concurrency-AP-ND-4 | 0/4 | — | — | 0.00 | True |
| cells/validation-concurrency-N-1 | 1/0 | 4.871 | 0.054 | 4.87 | True |
| cells/validation-concurrency-N-2 | 2/0 | 7.237 | 0.135 | 5.59 | True |
| cells/validation-concurrency-N-4 | 4/0 | 12.175 | 0.121 | 6.02 | True |
| cells/validation-concurrency-NP-AD-1 | 1/0 | 7.183 | 0.092 | 3.19 | True |
| cells/validation-concurrency-NP-AD-2 | 2/0 | 10.553 | 0.092 | 3.82 | True |
| cells/validation-concurrency-NP-AD-4 | 4/0 | 17.130 | 0.093 | 4.30 | True |
| cells/validation-full-A | 1/0 | 10.620 | 0.090 | 9.93 | True |
| cells/validation-full-AP-ND | 1/0 | 42.381 | 0.039 | 12.39 | True |
| cells/validation-full-N | 1/0 | 4.348 | 0.043 | 21.40 | True |
| cells/validation-full-NP-AD | 1/0 | 7.589 | 0.090 | 10.24 | True |
| cells/validation-recheck-AP-ND-4 | 4/0 | 104.444 | 0.057 | 0.76 | True |
| cells/warmup-calibration-N-run-id-0 | 1/0 | 5.076 | 0.049 | 4.84 | True |
| cells/warmup-calibration-N-run-id-1 | 1/0 | 4.913 | 0.041 | 5.17 | True |
| cells/warmup-calibration-N-run-id-2 | 1/0 | 4.679 | 0.043 | 5.32 | True |
| cells/warmup-calibration-N-run-id-3 | 1/0 | 5.181 | 0.043 | 4.91 | True |
| cells/warmup-calibration-N-run-id-0 | 1/0 | 5.228 | 0.045 | 4.83 | True |
| cells/warmup-calibration-N-run-id-1 | 1/0 | 4.660 | 0.043 | 5.31 | True |
| cells/warmup-calibration-N-run-id-2 | 1/0 | 4.762 | 0.043 | 5.24 | True |
| cells/warmup-calibration-N-run-id-3 | 1/0 | 4.660 | 0.043 | 5.34 | True |
| cells/warmup-formal-r1-N-run-id-0 | 1/0 | 4.486 | 0.043 | 5.49 | True |
| cells/warmup-formal-r1-N-run-id-1 | 1/0 | 4.701 | 0.041 | 5.34 | True |
| cells/warmup-formal-r1-N-run-id-2 | 1/0 | 4.566 | 0.041 | 5.46 | True |
| cells/warmup-formal-r1-N-run-id-0 | 1/0 | 4.450 | 0.042 | 5.55 | True |
| cells/warmup-formal-r1-N-run-id-1 | 1/0 | 4.685 | 0.040 | 5.39 | True |
| cells/warmup-formal-r1-N-run-id-2 | 1/0 | 4.736 | 0.042 | 5.29 | True |
| cells/warmup-formal-r1-N-run-id-0 | 1/0 | 4.147 | 0.040 | 5.91 | True |
| cells/warmup-formal-r1-N-run-id-1 | 1/0 | 4.285 | 0.039 | 5.78 | True |
| cells/warmup-formal-r1-N-run-id-2 | 1/0 | 4.340 | 0.040 | 5.74 | True |
| cells/warmup-formal-r1-N-run-id-0 | 1/0 | 4.280 | 0.042 | 5.72 | True |
| cells/warmup-formal-r1-N-run-id-1 | 1/0 | 4.428 | 0.041 | 5.61 | True |
| cells/warmup-formal-r1-N-run-id-2 | 1/0 | 4.413 | 0.040 | 5.63 | True |
| cells/warmup-formal-r1-N-run-id-0 | 1/0 | 4.740 | 0.044 | 5.23 | True |
| cells/warmup-formal-r1-N-run-id-1 | 1/0 | 4.440 | 0.041 | 5.58 | True |
| cells/warmup-formal-r1-N-run-id-2 | 1/0 | 4.428 | 0.040 | 5.61 | True |
| cells/warmup-interference-A-run-id-0 | 1/0 | 10.646 | 0.094 | 2.35 | True |
| cells/warmup-interference-A-run-id-1 | 1/0 | 10.537 | 0.090 | 2.40 | True |
| cells/warmup-interference-A-run-id-2 | 1/0 | 10.600 | 0.091 | 2.38 | True |
| cells/warmup-interference-AP-ND-run-id-0 | 1/0 | 40.826 | 0.070 | 0.74 | True |
| cells/warmup-interference-AP-ND-run-id-1 | 1/0 | 40.297 | 0.047 | 0.77 | True |
| cells/warmup-interference-AP-ND-run-id-2 | 1/0 | 40.694 | 0.049 | 0.76 | True |
| cells/warmup-interference-N-run-id-0 | 1/0 | 4.757 | 0.046 | 5.17 | True |
| cells/warmup-interference-N-run-id-1 | 1/0 | 4.488 | 0.042 | 5.51 | True |
| cells/warmup-interference-N-run-id-2 | 1/0 | 4.699 | 0.042 | 5.32 | True |
| cells/warmup-interference-NP-AD-run-id-0 | 1/0 | 8.117 | 0.116 | 2.73 | True |
| cells/warmup-interference-NP-AD-run-id-1 | 1/0 | 7.665 | 0.091 | 3.04 | True |
| cells/warmup-interference-NP-AD-run-id-2 | 1/0 | 7.458 | 0.091 | 3.11 | True |
| cells/warmup-interference-NP-AD-run-id-3 | 1/0 | 7.458 | 0.091 | 3.10 | True |
| cells/warmup-interference-REPLICAS-run-id-0 | 1/0 | 4.838 | 0.043 | 5.17 | True |
| cells/warmup-interference-REPLICAS-run-id-1 | 1/0 | 4.869 | 0.040 | 5.22 | True |
| cells/warmup-interference-REPLICAS-run-id-2 | 1/0 | 4.683 | 0.040 | 5.39 | True |
| cells/warmup-mixed-screen-A-run-id-0 | 1/0 | 10.757 | 0.095 | 2.34 | True |
| cells/warmup-mixed-screen-A-run-id-1 | 1/0 | 10.638 | 0.091 | 2.37 | True |
| cells/warmup-mixed-screen-A-run-id-2 | 1/0 | 10.658 | 0.091 | 2.37 | True |
| cells/warmup-mixed-screen-A-run-id-0 | 1/0 | 10.741 | 0.095 | 2.34 | True |
| cells/warmup-mixed-screen-A-run-id-1 | 1/0 | 10.637 | 0.092 | 2.37 | True |
| cells/warmup-mixed-screen-A-run-id-2 | 1/0 | 10.667 | 0.092 | 2.37 | True |
| cells/warmup-mixed-screen-A-run-id-0 | 1/0 | 10.568 | 0.094 | 2.37 | True |
| cells/warmup-mixed-screen-A-run-id-1 | 1/0 | 10.575 | 0.092 | 2.38 | True |
| cells/warmup-mixed-screen-A-run-id-2 | 1/0 | 10.600 | 0.091 | 2.38 | True |
| cells/warmup-mixed-screen-A-run-id-0 | 1/0 | 10.694 | 0.095 | 2.34 | True |
| cells/warmup-mixed-screen-A-run-id-1 | 1/0 | 10.559 | 0.093 | 2.38 | True |
| cells/warmup-mixed-screen-A-run-id-2 | 1/0 | 10.558 | 0.092 | 2.39 | True |
| cells/warmup-mixed-screen-A-run-id-0 | 1/0 | 10.639 | 0.095 | 2.35 | True |
| cells/warmup-mixed-screen-A-run-id-1 | 1/0 | 10.532 | 0.091 | 2.39 | True |
| cells/warmup-mixed-screen-A-run-id-2 | 1/0 | 10.576 | 0.091 | 2.38 | True |
| cells/warmup-mixed-screen-A-run-id-0 | 1/0 | 10.608 | 0.095 | 2.36 | True |
| cells/warmup-mixed-screen-A-run-id-1 | 1/0 | 10.588 | 0.091 | 2.38 | True |
| cells/warmup-mixed-screen-A-run-id-2 | 1/0 | 10.586 | 0.091 | 2.38 | True |
| cells/warmup-mixed-screen-A-run-id-0 | 1/0 | 10.609 | 0.094 | 2.36 | True |
| cells/warmup-mixed-screen-A-run-id-1 | 1/0 | 10.574 | 0.091 | 2.38 | True |
| cells/warmup-mixed-screen-A-run-id-2 | 1/0 | 10.547 | 0.092 | 2.39 | True |
| cells/warmup-mixed-screen-AP-ND-run-id-0 | 1/0 | 41.696 | 0.078 | 0.73 | True |
| cells/warmup-mixed-screen-AP-ND-run-id-1 | 1/0 | 42.393 | 0.051 | 0.73 | True |
| cells/warmup-mixed-screen-AP-ND-run-id-2 | 1/0 | 42.989 | 0.045 | 0.72 | True |
| cells/warmup-mixed-screen-AP-ND-run-id-0 | 1/0 | 43.669 | 0.075 | 0.70 | True |
| cells/warmup-mixed-screen-AP-ND-run-id-1 | 1/0 | 42.737 | 0.049 | 0.72 | True |
| cells/warmup-mixed-screen-AP-ND-run-id-2 | 1/0 | 42.882 | 0.046 | 0.72 | True |
| cells/warmup-mixed-screen-AP-ND-run-id-0 | 1/0 | 42.613 | 0.070 | 0.71 | True |
| cells/warmup-mixed-screen-AP-ND-run-id-1 | 1/0 | 42.306 | 0.050 | 0.73 | True |
| cells/warmup-mixed-screen-AP-ND-run-id-2 | 1/0 | 43.096 | 0.046 | 0.72 | True |
| cells/warmup-mixed-screen-AP-ND-run-id-0 | 1/0 | 40.589 | 0.065 | 0.75 | True |
| cells/warmup-mixed-screen-AP-ND-run-id-1 | 1/0 | 40.180 | 0.043 | 0.77 | True |
| cells/warmup-mixed-screen-AP-ND-run-id-2 | 1/0 | 40.670 | 0.044 | 0.76 | True |
| cells/warmup-mixed-screen-AP-ND-run-id-0 | 1/0 | 41.516 | 0.072 | 0.73 | True |
| cells/warmup-mixed-screen-AP-ND-run-id-1 | 1/0 | 40.339 | 0.047 | 0.77 | True |
| cells/warmup-mixed-screen-AP-ND-run-id-2 | 1/0 | 40.621 | 0.047 | 0.76 | True |
| cells/warmup-mixed-screen-AP-ND-run-id-0 | 1/0 | 41.880 | 0.072 | 0.73 | True |
| cells/warmup-mixed-screen-AP-ND-run-id-1 | 1/0 | 40.385 | 0.046 | 0.77 | True |
| cells/warmup-mixed-screen-AP-ND-run-id-2 | 1/0 | 40.471 | 0.047 | 0.76 | True |
| cells/warmup-mixed-screen-N-run-id-0 | 1/0 | 4.655 | 0.042 | 5.36 | True |
| cells/warmup-mixed-screen-N-run-id-1 | 1/0 | 4.761 | 0.041 | 5.28 | True |
| cells/warmup-mixed-screen-N-run-id-2 | 1/0 | 5.045 | 0.042 | 5.04 | True |
| cells/warmup-mixed-screen-N-run-id-0 | 1/0 | 4.835 | 0.044 | 5.15 | True |
| cells/warmup-mixed-screen-N-run-id-1 | 1/0 | 4.716 | 0.041 | 5.34 | True |
| cells/warmup-mixed-screen-N-run-id-2 | 1/0 | 4.646 | 0.043 | 5.34 | True |
| cells/warmup-mixed-screen-N-run-id-0 | 1/0 | 4.943 | 0.047 | 4.98 | True |
| cells/warmup-mixed-screen-N-run-id-1 | 1/0 | 4.674 | 0.042 | 5.34 | True |
| cells/warmup-mixed-screen-N-run-id-2 | 1/0 | 4.654 | 0.042 | 5.35 | True |
| cells/warmup-mixed-screen-N-run-id-0 | 1/0 | 4.541 | 0.042 | 5.46 | True |
| cells/warmup-mixed-screen-N-run-id-1 | 1/0 | 4.699 | 0.042 | 5.32 | True |
| cells/warmup-mixed-screen-N-run-id-2 | 1/0 | 4.450 | 0.040 | 5.61 | True |
| cells/warmup-mixed-screen-N-run-id-0 | 1/0 | 4.333 | 0.043 | 5.63 | True |
| cells/warmup-mixed-screen-N-run-id-1 | 1/0 | 4.489 | 0.041 | 5.56 | True |
| cells/warmup-mixed-screen-N-run-id-2 | 1/0 | 4.412 | 0.041 | 5.61 | True |
| cells/warmup-mixed-screen-N-run-id-0 | 1/0 | 4.375 | 0.042 | 5.62 | True |
| cells/warmup-mixed-screen-N-run-id-1 | 1/0 | 4.458 | 0.041 | 5.56 | True |
| cells/warmup-mixed-screen-N-run-id-2 | 1/0 | 4.433 | 0.041 | 5.59 | True |
| cells/warmup-mixed-screen-N-run-id-0 | 1/0 | 4.263 | 0.042 | 5.74 | True |
| cells/warmup-mixed-screen-N-run-id-1 | 1/0 | 4.378 | 0.041 | 5.66 | True |
| cells/warmup-mixed-screen-N-run-id-2 | 1/0 | 4.500 | 0.041 | 5.55 | True |
| cells/warmup-mixed-screen-NP-AD-run-id-0 | 1/0 | 8.090 | 0.114 | 2.75 | True |
| cells/warmup-mixed-screen-NP-AD-run-id-1 | 1/0 | 7.117 | 0.092 | 3.20 | True |
| cells/warmup-mixed-screen-NP-AD-run-id-2 | 1/0 | 7.647 | 0.092 | 3.04 | True |
| cells/warmup-mixed-screen-NP-AD-run-id-3 | 1/0 | 7.427 | 0.093 | 3.10 | True |
| cells/warmup-mixed-screen-NP-AD-run-id-0 | 1/0 | 8.338 | 0.117 | 2.67 | True |
| cells/warmup-mixed-screen-NP-AD-run-id-1 | 1/0 | 7.366 | 0.094 | 3.11 | True |
| cells/warmup-mixed-screen-NP-AD-run-id-2 | 1/0 | 7.484 | 0.093 | 3.08 | True |
| cells/warmup-mixed-screen-NP-AD-run-id-3 | 1/0 | 7.239 | 0.093 | 3.16 | True |
| cells/warmup-mixed-screen-NP-AD-run-id-0 | 1/0 | 8.556 | 0.115 | 2.63 | True |
| cells/warmup-mixed-screen-NP-AD-run-id-1 | 1/0 | 7.819 | 0.091 | 3.00 | True |
| cells/warmup-mixed-screen-NP-AD-run-id-2 | 1/0 | 8.293 | 0.092 | 2.87 | True |
| cells/warmup-mixed-screen-NP-AD-run-id-3 | 1/0 | 8.174 | 0.091 | 2.91 | True |
| cells/warmup-mixed-screen-NP-AD-run-id-0 | 1/0 | 8.275 | 0.117 | 2.69 | True |
| cells/warmup-mixed-screen-NP-AD-run-id-1 | 1/0 | 7.752 | 0.092 | 3.02 | True |
| cells/warmup-mixed-screen-NP-AD-run-id-2 | 1/0 | 7.962 | 0.092 | 2.96 | True |
| cells/warmup-mixed-screen-NP-AD-run-id-3 | 1/0 | 7.602 | 0.092 | 3.06 | True |
| cells/warmup-mixed-screen-NP-AD-run-id-0 | 1/0 | 7.597 | 0.120 | 2.83 | True |
| cells/warmup-mixed-screen-NP-AD-run-id-1 | 1/0 | 6.937 | 0.091 | 3.27 | True |
| cells/warmup-mixed-screen-NP-AD-run-id-2 | 1/0 | 7.218 | 0.091 | 3.18 | True |
| cells/warmup-mixed-screen-NP-AD-run-id-3 | 1/0 | 6.900 | 0.091 | 3.28 | True |
| cells/warmup-mixed-screen-NP-AD-run-id-0 | 1/0 | 7.712 | 0.117 | 2.82 | True |
| cells/warmup-mixed-screen-NP-AD-run-id-1 | 1/0 | 6.769 | 0.091 | 3.34 | True |
| cells/warmup-mixed-screen-NP-AD-run-id-2 | 1/0 | 7.402 | 0.091 | 3.12 | True |
| cells/warmup-mixed-screen-NP-AD-run-id-3 | 1/0 | 6.947 | 0.091 | 3.28 | True |
| cells/warmup-mixed-screen-NP-AD-run-id-0 | 1/0 | 7.426 | 0.117 | 2.89 | True |
| cells/warmup-mixed-screen-NP-AD-run-id-1 | 1/0 | 6.764 | 0.093 | 3.31 | True |
| cells/warmup-mixed-screen-NP-AD-run-id-2 | 1/0 | 6.922 | 0.092 | 3.27 | True |
| cells/warmup-mixed-screen-NP-AD-run-id-3 | 1/0 | 7.172 | 0.091 | 3.19 | True |
| cells/warmup-mixed-screen-REPLICAS-run-id-0 | 1/0 | 4.836 | 0.043 | 5.18 | True |
| cells/warmup-mixed-screen-REPLICAS-run-id-1 | 1/0 | 5.352 | 0.047 | 4.70 | True |
| cells/warmup-mixed-screen-REPLICAS-run-id-2 | 1/0 | 4.832 | 0.042 | 5.20 | True |
| cells/warmup-mixed-screen-REPLICAS-run-id-3 | 1/0 | 5.332 | 0.042 | 4.83 | True |
| cells/warmup-mixed-screen-REPLICAS-run-id-4 | 1/0 | 4.665 | 0.041 | 5.37 | True |
| cells/warmup-mixed-screen-REPLICAS-run-id-5 | 1/0 | 4.853 | 0.042 | 5.20 | True |
| cells/warmup-mixed-screen-REPLICAS-run-id-6 | 1/0 | 4.890 | 0.040 | 5.21 | True |
| cells/warmup-mixed-screen-REPLICAS-run-id-0 | 1/0 | 4.599 | 0.042 | 5.40 | True |
| cells/warmup-mixed-screen-REPLICAS-run-id-1 | 1/0 | 4.704 | 0.042 | 5.32 | True |
| cells/warmup-mixed-screen-REPLICAS-run-id-2 | 1/0 | 4.808 | 0.042 | 5.24 | True |
| cells/warmup-mixed-screen-REPLICAS-run-id-0 | 1/0 | 4.625 | 0.042 | 5.37 | True |
| cells/warmup-mixed-screen-REPLICAS-run-id-1 | 1/0 | 4.871 | 0.040 | 5.22 | True |
| cells/warmup-mixed-screen-REPLICAS-run-id-2 | 1/0 | 4.713 | 0.045 | 5.23 | True |
| cells/warmup-mixed-screen-REPLICAS-run-id-0 | 1/0 | 4.189 | 0.041 | 5.85 | True |
| cells/warmup-mixed-screen-REPLICAS-run-id-1 | 1/0 | 4.239 | 0.039 | 5.84 | True |
| cells/warmup-mixed-screen-REPLICAS-run-id-2 | 1/0 | 4.319 | 0.040 | 5.75 | True |
| cells/warmup-mixed-screen-REPLICAS-run-id-0 | 1/0 | 4.322 | 0.042 | 5.67 | True |
| cells/warmup-mixed-screen-REPLICAS-run-id-1 | 1/0 | 4.459 | 0.041 | 5.57 | True |
| cells/warmup-mixed-screen-REPLICAS-run-id-2 | 1/0 | 4.499 | 0.040 | 5.54 | True |
| cells/warmup-mixed-screen-REPLICAS-run-id-0 | 1/0 | 4.300 | 0.042 | 5.70 | True |
| cells/warmup-mixed-screen-REPLICAS-run-id-1 | 1/0 | 4.369 | 0.041 | 5.65 | True |
| cells/warmup-mixed-screen-REPLICAS-run-id-2 | 1/0 | 4.491 | 0.041 | 5.53 | True |
| cells/warmup-screen-A-run-id-0 | 1/0 | 10.602 | 0.095 | 2.36 | True |
| cells/warmup-screen-A-run-id-1 | 1/0 | 10.547 | 0.092 | 2.39 | True |
| cells/warmup-screen-A-run-id-2 | 1/0 | 10.605 | 0.091 | 2.38 | True |
| cells/warmup-screen-AP-ND-run-id-0 | 1/0 | 41.443 | 0.071 | 0.73 | True |
| cells/warmup-screen-AP-ND-run-id-1 | 1/0 | 40.402 | 0.044 | 0.77 | True |
| cells/warmup-screen-AP-ND-run-id-2 | 1/0 | 41.051 | 0.044 | 0.75 | True |
| cells/warmup-screen-AP-ND-run-id-0 | 1/0 | 42.886 | 0.096 | 0.70 | True |
| cells/warmup-screen-AP-ND-run-id-1 | 1/0 | 41.339 | 0.048 | 0.75 | True |
| cells/warmup-screen-AP-ND-run-id-2 | 1/0 | 40.499 | 0.053 | 0.76 | True |
| cells/warmup-screen-N-run-id-0 | 1/0 | 4.238 | 0.040 | 5.82 | True |
| cells/warmup-screen-N-run-id-1 | 1/0 | 4.126 | 0.040 | 5.96 | True |
| cells/warmup-screen-N-run-id-2 | 1/0 | 4.142 | 0.039 | 5.93 | True |
| cells/warmup-screen-NP-AD-run-id-0 | 1/0 | 7.233 | 0.117 | 2.95 | True |
| cells/warmup-screen-NP-AD-run-id-1 | 1/0 | 7.225 | 0.092 | 3.17 | True |
| cells/warmup-screen-NP-AD-run-id-2 | 1/0 | 7.273 | 0.092 | 3.16 | True |
| cells/warmup-session-A-run-id-0 | 1/0 | 10.618 | 0.094 | 2.36 | True |
| cells/warmup-session-A-run-id-1 | 1/0 | 10.611 | 0.091 | 2.38 | True |
| cells/warmup-session-A-run-id-2 | 1/0 | 10.610 | 0.090 | 2.38 | True |
| cells/warmup-session-AP-ND-run-id-0 | 1/0 | 43.362 | 0.076 | 0.70 | True |
| cells/warmup-session-AP-ND-run-id-1 | 1/0 | 42.563 | 0.051 | 0.72 | True |
| cells/warmup-session-AP-ND-run-id-2 | 1/0 | 42.408 | 0.047 | 0.73 | True |
| cells/warmup-session-N-run-id-0 | 1/0 | 4.486 | 0.044 | 5.45 | True |
| cells/warmup-session-N-run-id-1 | 1/0 | 4.646 | 0.042 | 5.36 | True |
| cells/warmup-session-N-run-id-2 | 1/0 | 4.807 | 0.040 | 5.30 | True |
| cells/warmup-session-NP-AD-run-id-0 | 1/0 | 8.030 | 0.119 | 2.73 | True |
| cells/warmup-session-NP-AD-run-id-1 | 1/0 | 7.232 | 0.091 | 3.18 | True |
| cells/warmup-session-NP-AD-run-id-2 | 1/0 | 7.130 | 0.092 | 3.21 | True |
| cells/warmup-session-NP-AD-run-id-3 | 1/0 | 7.110 | 0.092 | 3.21 | True |
| cells/warmup-session-REPLICAS-run-id-0 | 1/0 | 4.587 | 0.041 | 5.44 | True |
| cells/warmup-session-REPLICAS-run-id-1 | 1/0 | 5.152 | 0.042 | 4.95 | True |
| cells/warmup-session-REPLICAS-run-id-2 | 1/0 | 4.808 | 0.042 | 5.22 | True |
| preflight/nvidia-language-full | 1/0 | 4.432 | 0.043 | 20.95 | True |
| preflight/nvidia-short | 0/1 | — | — | 0.00 | True |
| preflight/nvidia-short-ready | 1/0 | 25.667 | 0.833 | 0.25 | True |
| preflight/nvidia-text-long | 0/1 | — | — | 0.00 | True |
| preflight-amd-full | 1/0 | 11.477 | 0.099 | 9.10 | True |
| preflight-amd-long | 1/0 | 11.464 | 0.099 | 0.66 | True |
| preflight-amd-short | 1/0 | 0.558 | 0.141 | 5.10 | True |


尚未完成三次正式重复前，不宣称尾延迟、容量或 PD 收益已经得到验证。

## GPU 传输实测

| 方向 | MiB | 次数 | 成功 | 中位 MiB/s | 中位秒 |
| --- | --- | --- | --- | --- | --- |
| NVIDIA → AMD | 1 | 10 | 10 | 7.19 | 0.139 |
| NVIDIA → AMD | 16 | 10 | 10 | 62.75 | 0.255 |
| NVIDIA → AMD | 64 | 10 | 10 | 98.04 | 0.653 |
| NVIDIA → AMD | 128 | 10 | 10 | 107.12 | 1.195 |
| NVIDIA → AMD | 512 | 10 | 10 | 107.16 | 4.778 |
| AMD → NVIDIA | 1 | 10 | 10 | 5.57 | 0.180 |
| AMD → NVIDIA | 16 | 10 | 10 | 11.66 | 1.373 |
| AMD → NVIDIA | 64 | 10 | 10 | 12.68 | 5.047 |
| AMD → NVIDIA | 128 | 10 | 10 | 12.86 | 9.950 |
| AMD → NVIDIA | 512 | 10 | 10 | 12.84 | 39.875 |

## 固定会话回放

同一份预录制历史用于各部署；当前回复允许 EOS，不执行工具命令。此组不代表真实 Claude Code 客户端操作。

| 部署 | 完成请求/30 | 错误 |
| --- | --- | --- |
| A | 30 | 0 |
| AP-ND | 30 | 0 |
| N | 30 | 0 |
| NP-AD | 30 | 0 |
| REPLICAS | 30 | 0 |

## 资源采样峰值

Windows GPUAdapterMemory 计数器包含桌面及其他进程；共享显存不能直接视为权重 CPU offload。

| 主机 | 样本数 | 专用 GPU GiB | 共享 GPU GiB | 主机已用 GiB |
| --- | --- | --- | --- | --- |
| A | 12 | 0.28 | 0.10 | 21.06 |
| N | 11 | 1.96 | 0.08 | 23.56 |
| A | 26 | 0.28 | 0.10 | 21.27 |
| N | 25 | 2.68 | 0.15 | 23.58 |
| A | 43 | 1.56 | 0.15 | 23.49 |
| N | 42 | 3.50 | 0.20 | 24.28 |
| A | 244 | 14.55 | 0.23 | 26.62 |
| N | 239 | 15.33 | 0.36 | 27.94 |
| A | 1079 | 14.77 | 0.38 | 29.78 |
| N | 1080 | 15.42 | 0.38 | 27.80 |
| A | 27 | 13.85 | 0.23 | 21.39 |
| N | 27 | 15.10 | 0.34 | 27.87 |
| A | 26 | 13.89 | 0.23 | 21.38 |
| N | 26 | 13.79 | 0.15 | 27.93 |
| A | 1078 | 14.19 | 0.29 | 21.57 |
| N | 1080 | 15.39 | 0.58 | 28.72 |
| A | 908 | 14.88 | 0.26 | 23.36 |
| N | 890 | 15.37 | 0.70 | 29.98 |
| A | 1693 | 14.96 | 0.29 | 23.40 |
| N | 1668 | 15.42 | 0.48 | 28.37 |
| A | 24 | 1.32 | 0.09 | 21.05 |
| N | 24 | 13.62 | 0.20 | 24.54 |
| A | 47 | 0.68 | 0.13 | 22.82 |
| N | 46 | 15.35 | 0.38 | 29.17 |
| A | 26 | 0.71 | 0.16 | 21.38 |
| N | 25 | 15.21 | 0.18 | 30.03 |
| A | 504 | 14.26 | 0.29 | 23.55 |
| N | 560 | 15.41 | 0.21 | 26.47 |
| A | 133 | 14.27 | 0.29 | 23.63 |
| N | 132 | 15.25 | 0.18 | 25.41 |
| A | 27 | 0.53 | 0.13 | 22.24 |
| N | 26 | 15.03 | 0.20 | 26.34 |
| A | 395 | 14.26 | 0.26 | 23.67 |
| N | 454 | 15.28 | 0.21 | 26.41 |
| N | 941 | 15.35 | 0.27 | 27.39 |
| N | 838 | 15.35 | 0.27 | 27.39 |
| A | 438 | 14.19 | 0.26 | 24.32 |
| N | 479 | 15.35 | 0.19 | 26.38 |
| N | 129 | 15.18 | 0.27 | 27.39 |
