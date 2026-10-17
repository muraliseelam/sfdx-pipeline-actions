/**
 * Sample class for the pipeline's end-to-end tests: valid Apex with a
 * High-severity Code Analyzer finding (Limits.getHeapSize() inside a loop).
 */
public with sharing class E2eHeapWatch {
    public static Integer sampleHeap(Integer iterations) {
        Integer peak = 0;
        for (Integer i = 0; i < iterations; i++) {
            peak = Math.max(peak, Limits.getHeapSize());
        }
        return peak;
    }
}
